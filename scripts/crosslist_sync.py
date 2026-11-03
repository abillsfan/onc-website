#!/usr/bin/env python3
"""Keep cards listed on BOTH eBay and Whatnot from selling twice.

Runs every few minutes from GitHub Actions (.github/workflows/crosslist-sync.yml).

How cards are linked: a Whatnot product whose external_id is "ebay:<eBay item number>" is the
Whatnot copy of that eBay listing. Each card is quantity 1 on both sites.

Rules (Carl, Nov 2 2026):
  * Sold on eBay                 -> Whatnot copy set to 0 (stays hidden).
  * eBay auction gets a bid      -> Whatnot copy set to 0. If the auction later ends unsold, set back to 1.
  * Sold on Whatnot              -> eBay listing ended (if it has no bids).
  * Card added to a Whatnot show that is live or starts within SHOW_PULL_HOURS -> eBay listing ended,
    so it can only sell in the show.
  * eBay can't be checked        -> every linked Whatnot copy set to 0 until a later run can check again.
  * Anything that looks like a double sale, or a Whatnot outage, fails the run so GitHub emails Carl.

Test mode: unless data/crosslist-config.json has "live": true (and the run doesn't set DRY_RUN=1), nothing is
changed on either site and the state file is not saved; the run only reports what it WOULD do.

Secrets (repository secrets, never printed): EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, EBAY_REFRESH_TOKEN,
WHATNOT_API_TOKEN. State (links + what the sync did) lives in crosslist-state.json on the
"crosslist-state" branch so state commits don't redeploy the website. No buyer details are stored.
"""
import base64, json, os, sys, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

EBAY_API = "https://api.ebay.com"
WN_API = "https://api.whatnot.com/seller-api/rest/v2"
NS = {"e": "urn:ebay:apis:eBLBaseComponents"}
SHOW_PULL_HOURS = 3
UA = "onc-crosslist-sync/1.0 (+https://on-cards.com)"  # Whatnot's firewall blocks the default Python user agent
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "..", "data", "crosslist-config.json")
STATE = os.environ.get("CROSSLIST_STATE", os.path.join(HERE, "..", "crosslist-state.json"))
SUMMARY = os.environ.get("GITHUB_STEP_SUMMARY")

NOW = datetime.now(timezone.utc)
RUN = os.environ.get("GITHUB_RUN_ID", NOW.strftime("%Y%m%d%H%M%S"))
lines, problems = [], []


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def say(msg):
    print(msg)
    lines.append(msg)


def problem(msg):
    print("::error::" + msg)
    problems.append(msg)


# ---------------------------------------------------------------- Whatnot
class Whatnot:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, body=None, query=None, tries=4):
        url = WN_API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(tries):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": "Bearer " + self.token, "Accept": "application/json",
                "Content-Type": "application/json", "User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    time.sleep(0.6)  # stay well under the per-seller rate limit
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < tries - 1:
                    time.sleep(float(e.headers.get("Retry-After") or 1) * (2 ** attempt))
                    continue
                if e.code == 404:
                    return None
                raise RuntimeError(f"Whatnot {method} {path} -> HTTP {e.code}: "
                                   + e.read().decode("utf-8", "replace")[:300])

    def pages(self, path, query=None):
        cursor = None
        while True:
            q = dict(query or {}, limit=50)
            if cursor:
                q["cursor"] = cursor
            page = self.call("GET", path, query=q) or {}
            yield from page.get("data", [])
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def variant(self, product_id):
        for v in self.pages(f"/products/{product_id}/variants"):
            return v
        return None

    def set_qty(self, variant_id, qty, why):
        res = self.call("POST", "/inventory/set", {"items": [{
            "variant_id": variant_id, "quantity": qty,
            "idempotency_key": f"crosslist-{RUN}-{variant_id}-{qty}-{why}"[:255]}]})
        status = ((res or {}).get("results") or [{}])[0].get("status")
        if status not in ("applied", "already_applied"):
            raise RuntimeError(f"Whatnot inventory set for {variant_id} returned {status}")


# ---------------------------------------------------------------- eBay
class Ebay:
    def __init__(self, cid, secret, refresh):
        cid, secret, refresh = cid.strip(), secret.strip(), refresh.strip().strip('"')
        if "%5E" in refresh or "%23" in refresh:  # copied URL-encoded
            refresh = urllib.parse.unquote(refresh)
        auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh}).encode()
        req = urllib.request.Request(EBAY_API + "/identity/v1/oauth2/token", data=body, headers={
            "Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                self.token = json.load(r)["access_token"]
        except urllib.error.HTTPError as e:
            shape = ("starts like a refresh token (v^1.1#)" if refresh.startswith("v^1.1#") else
                     f"does NOT start with v^1.1# (starts with {refresh[:3]!r}, {len(refresh)} chars)")
            raise RuntimeError(f"eBay sign-in (refresh token) failed: HTTP {e.code} "
                               + e.read().decode("utf-8", "replace")[:200] + f". The saved token {shape}.")

    def trading(self, call, inner):
        xml = (f'<?xml version="1.0" encoding="utf-8"?><{call}Request xmlns="urn:ebay:apis:eBLBaseComponents">'
               f"{inner}</{call}Request>").encode()
        req = urllib.request.Request(EBAY_API + "/ws/api.dll", data=xml, headers={
            "X-EBAY-API-SITEID": "0", "X-EBAY-API-COMPATIBILITY-LEVEL": "1349",
            "X-EBAY-API-CALL-NAME": call, "X-EBAY-API-IAF-TOKEN": self.token,
            "Content-Type": "text/xml", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            root = ET.fromstring(r.read())
        ack = root.findtext("e:Ack", "", NS)
        if ack not in ("Success", "Warning"):
            errs = "; ".join(f"{e.findtext('e:ErrorCode', '', NS)} {e.findtext('e:ShortMessage', '', NS)}"
                             for e in root.findall("e:Errors", NS))
            raise RuntimeError(f"eBay {call} failed: {errs}")
        return root

    def item(self, item_id):
        root = self.trading("GetItem", f"<ItemID>{int(item_id)}</ItemID><IncludeItemSpecifics>true</IncludeItemSpecifics>")
        it = root.find("e:Item", NS)
        ss = it.find("e:SellingStatus", NS)
        specifics = {}
        for nv in it.findall("e:ItemSpecifics/e:NameValueList", NS):
            specifics[nv.findtext("e:Name", "", NS).strip().lower()] = nv.findtext("e:Value", "", NS).strip()
        ltype = it.findtext("e:ListingType", "", NS)
        bin_ = float(it.findtext("e:BuyItNowPrice", "0", NS) or 0)
        if ltype != "Chinese":  # fixed price: the price IS the Buy It Now
            bin_ = float(ss.findtext("e:CurrentPrice", "0", NS) or it.findtext("e:StartPrice", "0", NS) or 0)
        return {
            "bin": bin_,
            "pictures": [u.text for u in it.findall("e:PictureDetails/e:PictureURL", NS) if u.text],
            "sport": specifics.get("sport", ""),
            "category": it.findtext("e:PrimaryCategory/e:CategoryName", "", NS),
            "available": int(it.findtext("e:Quantity", "1", NS) or 1) - int(ss.findtext("e:QuantitySold", "0", NS) or 0),
            "ending_reason": it.findtext("e:ListingDetails/e:EndingReason", "", NS),
            "title": it.findtext("e:Title", "", NS),
            "type": it.findtext("e:ListingType", "", NS),          # Chinese = auction, FixedPriceItem
            "status": ss.findtext("e:ListingStatus", "", NS),       # Active, Completed, Ended
            "bids": int(ss.findtext("e:BidCount", "0", NS) or 0),
            "sold": int(ss.findtext("e:QuantitySold", "0", NS) or 0),
            "qty": int(it.findtext("e:Quantity", "1", NS) or 1),
        }

    def snapshot(self):
        """ONE GetMyeBaySelling call per page (active + sold + unsold lists) instead of one GetItem per card,
        so eBay API use stays ~300 calls/day no matter how many cards are linked."""
        snap, page = {}, 1
        money = lambda el, path: float(el.findtext(path, "0", NS) or 0)
        while True:
            root = self.trading("GetMyeBaySelling",
                "<ActiveList><Include>true</Include><Pagination><EntriesPerPage>200</EntriesPerPage>"
                f"<PageNumber>{page}</PageNumber></Pagination></ActiveList>"
                + ("<SoldList><Include>true</Include><DurationInDays>60</DurationInDays><Pagination><EntriesPerPage>200</EntriesPerPage></Pagination></SoldList>"
                   "<UnsoldList><Include>true</Include><DurationInDays>60</DurationInDays><Pagination><EntriesPerPage>200</EntriesPerPage></Pagination></UnsoldList>"
                   if page == 1 else ""))
            items = root.findall("e:ActiveList/e:ItemArray/e:Item", NS)
            for it in items:
                ss = it.find("e:SellingStatus", NS)
                ltype = it.findtext("e:ListingType", "", NS)
                bin_ = money(it, "e:BuyItNowPrice") if ltype == "Chinese" else money(ss, "e:CurrentPrice") if ss is not None else 0
                snap[it.findtext("e:ItemID", "", NS)] = {
                    "title": it.findtext("e:Title", "", NS), "type": ltype, "status": "Active",
                    "bids": int((ss.findtext("e:BidCount", "0", NS) if ss is not None else "0") or 0), "sold": 0,
                    "bin": bin_, "available": int(it.findtext("e:QuantityAvailable", "1", NS) or 1)}
            if page == 1:
                for t in root.findall(".//e:SoldList//e:Transaction", NS):
                    iid = t.findtext("e:Item/e:ItemID", "", NS)
                    if iid:
                        snap[iid] = {"title": t.findtext("e:Item/e:Title", "", NS), "type": "", "status": "Completed",
                                     "bids": 0, "sold": 1, "bin": 0, "available": 0}
                for it in root.findall("e:UnsoldList/e:ItemArray/e:Item", NS):
                    iid = it.findtext("e:ItemID", "", NS)
                    if iid and iid not in snap:
                        snap[iid] = {"title": it.findtext("e:Title", "", NS), "type": it.findtext("e:ListingType", "", NS),
                                     "status": "Completed", "bids": 0, "sold": 0, "bin": 0, "available": 0}
            pages = int(root.findtext("e:ActiveList/e:PaginationResult/e:TotalNumberOfPages", "1", NS) or 1)
            if page >= pages or not items:
                return snap
            page += 1

    def active_ids(self):
        ids, page = [], 1
        while True:
            root = self.trading("GetMyeBaySelling", "<ActiveList><Include>true</Include><Pagination>"
                                f"<EntriesPerPage>200</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination></ActiveList>")
            items = root.findall("e:ActiveList/e:ItemArray/e:Item", NS)
            ids += [i.findtext("e:ItemID", "", NS) for i in items]
            pages = int(root.findtext("e:ActiveList/e:PaginationResult/e:TotalNumberOfPages", "1", NS) or 1)
            if page >= pages or not items:
                return ids
            page += 1

    def end(self, item_id):
        self.trading("EndItem", f"<ItemID>{int(item_id)}</ItemID><EndingReason>NotAvailable</EndingReason>")


SINGLE_CARD_PROFILE = "710722"  # Whatnot "Single card (toploaded)" shipping profile
SPORT_CATEGORIES = {  # Whatnot taxonomy ids (github.com/Whatnot-Inc/product-taxonomy)
    "baseball": "586", "football": "590", "basketball": "593", "soccer": "574", "hockey": "599",
    "ice hockey": "599", "wrestling": "602", "mma": "596", "ufc": "596", "tennis": "2666"}


def norm(title):
    return " ".join((title or "").lower().split())


def category_for(e):
    sport = (e.get("sport") or "").lower()
    if sport in SPORT_CATEGORIES:
        return SPORT_CATEGORIES[sport]
    text = (e.get("title", "") + " " + e.get("category", "")).lower()
    if "pok" in text and "mon" in text:
        return "2"
    for k, v in SPORT_CATEGORIES.items():
        if k in text:
            return v
    return "139"  # Other Sports Cards


# ---------------------------------------------------------------- main
def main():
    with open(CONFIG) as f:
        config = json.load(f)
    live = bool(config.get("live")) and os.environ.get("DRY_RUN", "") not in ("1", "true")
    mode = "LIVE" if live else "TEST MODE (no changes made)"
    say(f"## Cross-list sync: {mode}, {iso(NOW)}")

    try:
        with open(STATE) as f:
            state = json.load(f)
    except Exception:
        state = {}
    links = {l["ebay_id"]: l for l in state.get("links", [])}
    # First run: only look back a day for Whatnot orders.
    last_orders = parse_ts(state.get("last_order_check")) or NOW - timedelta(days=1)
    last_products = parse_ts(state.get("last_product_scan")) or datetime(2020, 1, 1, tzinfo=timezone.utc)

    env = {k: os.environ.get(k) for k in ("WHATNOT_API_TOKEN", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET",
                                          "EBAY_REFRESH_TOKEN")}
    # Preferred: the encrypted long-lasting key saved by the "Connect eBay" workflow (ebay_connect.py).
    enc_path = os.environ.get("EBAY_REFRESH_ENC")
    if enc_path and os.path.exists(enc_path) and os.environ.get("EBAY_SYNC_CLIENT_SECRET"):
        import subprocess
        with open(enc_path) as f:
            saved = json.load(f)
        refresh = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-base64", "-A",
                                  "-pass", "env:ENC_KEY"], input=saved["enc"].encode(), capture_output=True, check=True,
                                 env=dict(os.environ, ENC_KEY=os.environ["EBAY_SYNC_CLIENT_SECRET"].strip())).stdout.decode().strip()
        print("::add-mask::" + refresh)
        env["EBAY_REFRESH_TOKEN"] = refresh
        exp = parse_ts(saved.get("expires", "") + "T00:00:00Z") if saved.get("expires") else None
        if exp and exp - NOW < timedelta(days=30):
            print(f"::warning::The eBay connection expires {saved['expires']}. Re-run 'Connect eBay' soon.")
            say(f"- WARNING: eBay connection expires {saved['expires']}; re-run Connect eBay")
    # The refresh token only works with the keyset (App ID + Cert ID) it was made from. If that's a
    # different eBay app than the website's listing sync uses, its keys are saved as EBAY_SYNC_*.
    if os.environ.get("EBAY_SYNC_CLIENT_ID") and os.environ.get("EBAY_SYNC_CLIENT_SECRET"):
        env["EBAY_CLIENT_ID"] = os.environ["EBAY_SYNC_CLIENT_ID"]
        env["EBAY_CLIENT_SECRET"] = os.environ["EBAY_SYNC_CLIENT_SECRET"]
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise SystemExit("Missing repository secrets: " + ", ".join(missing))
    wn = Whatnot(env["WHATNOT_API_TOKEN"])

    if os.environ.get("DIAGNOSE_EBAY"):  # which keyset (if any) accepts the refresh token; prints no secrets
        rt = env["EBAY_REFRESH_TOKEN"].strip()
        say(f"- Refresh token length {len(rt)} chars (eBay refresh tokens are short, ~100-200; a 1,500+ char one is the 2-hour User Token)")
        for label, a, b in (("EBAY_SYNC_* keyset", os.environ.get("EBAY_SYNC_CLIENT_ID"), os.environ.get("EBAY_SYNC_CLIENT_SECRET")),
                            ("website EBAY_CLIENT_* keyset", os.environ.get("EBAY_CLIENT_ID"), os.environ.get("EBAY_CLIENT_SECRET"))):
            if not a or not b:
                say(f"- {label}: not set"); continue
            kind = "Sandbox" if "-SBX-" in a else ("Production" if "-PRD-" in a else "unknown type")
            try:
                Ebay(a, b, rt); say(f"- {label} ({kind}): ACCEPTED the refresh token")
            except Exception as e:
                say(f"- {label} ({kind}): rejected ({str(e)[40:140]})")

    if not live:  # Prove both keys work (read-only) so test runs mean something even before any cards are linked.
        me = wn.call("GET", "/users/me") or {}
        say(f"- Whatnot key OK: signed in as {me.get('username')}")
        eb_check = Ebay(env["EBAY_CLIENT_ID"], env["EBAY_CLIENT_SECRET"], env["EBAY_REFRESH_TOKEN"])
        say("- eBay key OK: signed in")
        try:
            with open(os.path.join(HERE, "..", "data", "ebay-listings.json")) as f:
                sample = (json.load(f).get("items") or [])[:4]
        except Exception:
            sample = []
        for s in sample:
            e = eb_check.item(s["id"])
            say(f"- eBay {s['id']}: {e['type']}, {e['status']}, bids {e['bids']}, sold {e['sold']} ({e['title'][:60]})")

    did = []

    def act(desc, fn):
        if live:
            fn()
            did.append(desc)
            say("- DONE: " + desc)
        else:
            say("- WOULD: " + desc)

    # 1. Find Whatnot products linked to eBay listings (external_id "ebay:<item id>"), newest first.
    newest = last_products
    for p in wn.pages("/products"):
        created = parse_ts(p.get("created_at"))
        if created and created <= last_products:
            break
        newest = max(newest, created or newest)
        ext = (p.get("external_id") or "").strip()
        if ext.lower().startswith("ebay:") and ext[5:].strip().isdigit():
            eid = ext[5:].strip()
            if eid not in links:
                v = wn.variant(p["id"])
                if not v:
                    problem(f"Whatnot product {p['id']} ({p.get('title')}) has no variant; can't sync it")
                    continue
                links[eid] = {"ebay_id": eid, "wn_product": p["id"], "wn_variant": v["id"],
                              "title": p.get("title", ""), "state": "active", "since": iso(NOW)}
                say(f"- New link: eBay {eid} <-> Whatnot {p['id']} ({p.get('title', '')})")
    # 1b. New eBay listings with a Buy It Now price and no bids -> copy them to the Whatnot shop
    #     at BIN x markup (Carl: +8% to cover Whatnot's higher fees). Skips ones already linked or tried.
    try:
        eb = Ebay(env["EBAY_CLIENT_ID"], env["EBAY_CLIENT_SECRET"], env["EBAY_REFRESH_TOKEN"])
        eb_error = None
    except Exception as e:
        eb, eb_error = None, e
    markup = float(config.get("whatnot_markup", 1.08))
    skipped = state.setdefault("not_crosslisted", {})
    snap = None
    if eb:
        try:
            snap = eb.snapshot()
        except Exception as e:
            eb, eb_error = None, e
    if eb and config.get("auto_crosslist", True):
        for eid, quick in snap.items():
            if quick["status"] != "Active" or not eid or eid in links or eid in skipped:
                continue
            # Relisted card? (eBay gives a relist a new item number.) Reuse its existing Whatnot listing
            # instead of making a second one (Carl, Nov 3 2026). Match by title among cards no longer on eBay.
            same = [l for l in links.values() if l["state"] in ("whatnot_only", "ebay_ended")
                    and norm(l["title"]) == norm(quick["title"])]
            if same:
                old = same[0]

                def relink(old=old, eid=eid):
                    wn.call("PATCH", f"/products/{old['wn_product']}", {"external_id": f"ebay:{eid}"})
                    wn.call("PATCH", f"/products/{old['wn_product']}/variants", {"variants": [{
                        "id": old["wn_variant"], "external_id": f"ebay:{eid}"}]})
                    links.pop(old["ebay_id"], None)
                    links[eid] = dict(old, ebay_id=eid, previous_ebay_id=old["ebay_id"], state="hidden_nobin", since=iso(NOW))
                act(f"reconnect relisted eBay {eid} ({quick['title'][:60]}) to its existing Whatnot listing "
                    f"(was eBay {old['ebay_id']})", relink)
                if not live:
                    skipped[eid] = "would be relinked"
                continue
            if quick["bin"] <= 0 and quick["bids"] == 0:
                continue  # starting-bid-only auction: nothing to price from (re-checked free each run via the snapshot)
            e = eb.item(eid) if quick["bin"] > 0 and quick["bids"] == 0 and quick["available"] == 1 else quick
            e.setdefault("pictures", ["x"])
            why = None
            if e["bin"] <= 0:
                why = "no Buy It Now price"
            elif e["bids"] > 0:
                why = "already has bids"
            elif e["available"] != 1:
                why = f"quantity {e['available']} (only single cards are cross-listed)"
            elif not e["pictures"]:
                why = "no photo"
            if why:
                if e["bids"] == 0 and e["bin"] <= 0:
                    pass  # a starting-bid-only auction might get a BIN later; check again next run
                else:
                    skipped[eid] = why
                say(f"- Not cross-listing eBay {eid} ({e['title'][:50]}): {why}")
                continue
            price = f"{round(e['bin'] * markup, 2):.2f}"
            cat = category_for(e)
            desc = f"Cross-list: eBay {eid}, Whatnot ${price} (eBay BIN ${e['bin']:.2f} x {markup})"

            def create(e=e, eid=eid, price=price, cat=cat):
                p = wn.call("POST", "/products", {
                    "title": e["title"][:200], "category": {"id": cat}, "dimension_profile": {"id": SINGLE_CARD_PROFILE},
                    "external_id": f"ebay:{eid}", "description": e["title"],
                    "media": [{"url": u} for u in e["pictures"][:6]]})
                v = wn.variant(p["id"])
                wn.call("PATCH", f"/products/{p['id']}/variants", {"variants": [{
                    "id": v["id"], "external_id": f"ebay:{eid}",
                    "pricing": {"buy_it_now": {"price": {"amount": price, "currency": "USD"}}}}]})
                wn.set_qty(v["id"], 1, "new")
                wn.call("POST", f"/merchandising/products/{p['id']}", {"transaction_type": "buy_it_now", "reserve_for_live": False})
                links[eid] = {"ebay_id": eid, "wn_product": p["id"], "wn_variant": v["id"], "title": e["title"],
                              "state": "active", "since": iso(NOW)}
            act(f"add eBay {eid} ({e['title'][:60]}) to the Whatnot shop at ${price} [{desc}]", create)
    say(f"Linked cards: {len(links)}")

    open_links = {k: l for k, l in links.items()
                  if l["state"] in ("active", "hidden_bid", "hidden_error", "hidden_nobin", "pulled_for_show")}
    if not open_links:
        save(state, links, newest, NOW, live)
        return

    # 2. Whatnot copies' current quantities.
    for l in open_links.values():
        v = wn.call("GET", f"/products/{l['wn_product']}/variants/{l['wn_variant']}")
        l["_wn_qty"] = ((v or {}).get("inventory") or {}).get("available_quantity")
        l["_wn_price"] = ((((v or {}).get("pricing") or {}).get("buy_it_now") or {}).get("price") or {}).get("amount")
        if v is None:
            problem(f"Whatnot copy of eBay {l['ebay_id']} ({l['title']}) no longer exists")

    # 3. New Whatnot orders since the last check: did any linked card sell there?
    sold_on_wn = set()
    by_product = {l["wn_product"]: l for l in open_links.values()}
    order_cutoff = last_orders - timedelta(minutes=15)  # overlap so nothing slips between runs
    for o in wn.pages("/orders"):
        if parse_ts(o["created_at"]) < order_cutoff:
            break
        if o.get("status") in ("failed", "cancelled"):
            continue
        for it in wn.pages(f"/orders/{o['id']}/items"):
            l = by_product.get(it.get("product_id"))
            if l:
                sold_on_wn.add(l["ebay_id"])

    # 4. eBay status for each open link. If eBay can't be checked, hide every Whatnot copy.
    try:
        if eb_error:
            raise eb_error
        status = {eid: (snap.get(eid) or eb.item(eid)) for eid in open_links}  # GetItem only if not in the 60-day lists
    except Exception as e:
        problem(f"Couldn't check eBay ({e}). Hiding Whatnot copies until eBay can be checked.")
        for l in open_links.values():
            if l["state"] == "active" and l.get("_wn_qty"):
                act(f"set Whatnot copy of {l['title']} to 0 (eBay unreachable)",
                    lambda l=l: wn.set_qty(l["wn_variant"], 0, "ebay-error"))
                l["state"] = "hidden_error"
        save(state, links, newest, None, live)  # don't advance the order check; recheck next run
        return

    # 5. Upcoming/live shows, for pulling show cards off eBay.
    soon = set()
    for s in list(wn.pages("/shows", {"status": "live"})) + list(wn.pages("/shows", {"status": "scheduled"})):
        start = parse_ts(s.get("scheduled_start_at"))
        if s.get("status") == "live" or (start and start <= NOW + timedelta(hours=SHOW_PULL_HOURS)):
            soon.add(s["id"])

    # 6. Decide.
    for eid, l in open_links.items():
        e, title = status[eid], l["title"]
        ebay_open = e["status"] == "Active"
        ebay_sold = e["sold"] > 0
        wn_qty = l.get("_wn_qty")

        if eid in sold_on_wn:
            if ebay_sold:
                problem(f"DOUBLE SALE: {title} sold on Whatnot AND eBay {eid}. Cancel one order (Whatnot: reason 'item sold').")
                l["state"] = "conflict"
            elif ebay_open and e["bids"] > 0:
                problem(f"{title} sold on Whatnot but eBay auction {eid} has {e['bids']} bid(s). eBay won't allow ending it; decide which buyer gets it.")
                l["state"] = "conflict"
            else:
                if ebay_open:
                    act(f"end eBay listing {eid} ({title}): sold on Whatnot", lambda: eb.end(eid))
                else:
                    say(f"- {title} sold on Whatnot; eBay {eid} already closed")
                l["state"] = "sold_whatnot"
            continue

        if ebay_sold:
            if wn_qty:
                act(f"set Whatnot copy of {title} to 0: sold on eBay", lambda l=l: wn.set_qty(l["wn_variant"], 0, "ebay-sold"))
            l["state"] = "sold_ebay"
            continue

        if not ebay_open:  # eBay listing ended without selling
            reason_ended = ""
            if l["state"] != "pulled_for_show":
                try:
                    reason_ended = eb.item(eid).get("ending_reason", "")
                except Exception:
                    reason_ended = ""
            if reason_ended:  # Carl ended it himself -> it comes off Whatnot too (Carl, Nov 3 2026)
                if wn_qty:
                    act(f"set Whatnot copy of {title} to 0: eBay listing {eid} was ended ({reason_ended})",
                        lambda l=l: wn.set_qty(l["wn_variant"], 0, "ebay-ended"))
                l["state"] = "ebay_ended"
                continue
            if l["state"] in ("hidden_bid", "hidden_error", "hidden_nobin") and wn_qty == 0:
                act(f"put Whatnot copy of {title} back (eBay {eid} ended unsold)", lambda l=l: wn.set_qty(l["wn_variant"], 1, "ebay-unsold"))
            else:
                say(f"- eBay {eid} ({title}) ended unsold; Whatnot copy left as is")
            l["state"] = "whatnot_only"
            continue

        if e["bids"] > 0:
            if wn_qty:
                act(f"set Whatnot copy of {title} to 0: eBay auction has {e['bids']} bid(s)", lambda l=l: wn.set_qty(l["wn_variant"], 0, "ebay-bid"))
            l["state"] = "hidden_bid"
            continue

        if e["bin"] <= 0:  # open on eBay but no Buy It Now to price from -> keep it off Whatnot
            if wn_qty:
                act(f"set Whatnot copy of {title} to 0: eBay {eid} has no Buy It Now price", lambda l=l: wn.set_qty(l["wn_variant"], 0, "ebay-nobin"))
            l["state"] = "hidden_nobin"
            continue

        # eBay listing open, no bids, not sold on either site. Keep the Whatnot price = eBay BIN x markup.
        if e["bin"] > 0 and l.get("_wn_price") is not None:
            want = round(e["bin"] * markup, 2)
            if abs(float(l["_wn_price"]) - want) >= 0.01:
                act(f"update Whatnot price of {title}: ${float(l['_wn_price']):.2f} -> ${want:.2f} (eBay BIN ${e['bin']:.2f})",
                    lambda l=l, want=want: wn.call("PATCH", f"/products/{l['wn_product']}/variants", {"variants": [{
                        "id": l["wn_variant"], "pricing": {"buy_it_now": {"price": {"amount": f"{want:.2f}", "currency": "USD"}}}}]}))
        if l["state"] in ("hidden_error", "hidden_nobin", "hidden_bid"):
            if wn_qty == 0:
                act(f"put Whatnot copy of {title} back (eBay {eid} open with Buy It Now, no bids)",
                    lambda l=l: wn.set_qty(l["wn_variant"], 1, "ebay-ok"))
            l["state"] = "active"
        elif wn_qty == 0 and l["state"] == "active":
            problem(f"Whatnot copy of {title} is at 0 but no Whatnot order was found. Check it; eBay {eid} left open.")

        if soon:
            m = wn.call("GET", f"/merchandising/products/{l['wn_product']}") or {}
            if m.get("active_in_shows"):
                shows = {s.get("id") for s in wn.pages(f"/merchandising/products/{l['wn_product']}/shows")}
                if shows & soon:
                    act(f"end eBay listing {eid} ({title}): it's in a Whatnot show starting soon", lambda: eb.end(eid))
                    l["state"] = "pulled_for_show"

    for l in links.values():
        l.pop("_wn_qty", None); l.pop("_wn_price", None)
    counts = {}
    for l in links.values():
        counts[l["state"]] = counts.get(l["state"], 0) + 1
    say("States: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    save(state, links, newest, NOW, live)
    # A backup run (2-hour Cloudflare timer or GitHub's own schedule) should normally find nothing to do,
    # because eBay/Whatnot notifications start the sync right away. If it DID have to change something,
    # an instant notification was missed: fail the run so GitHub emails Carl (Carl, Nov 3 2026).
    reason = os.environ.get("SYNC_REASON", "")
    if did and reason in ("timer", "schedule"):
        problem("Instant sync missed this; the backup run caught it: " + "; ".join(did)
                + ". Everything is fixed now; this is just a heads-up that a notification didn't arrive.")


def save(state, links, newest_product, order_check, live):
    for l in links.values():
        l.pop("_wn_qty", None); l.pop("_wn_price", None)
    state["links"] = sorted(links.values(), key=lambda l: l["ebay_id"])
    state["last_product_scan"] = iso(newest_product)
    if order_check:
        state["last_order_check"] = iso(order_check)
    state["last_run"] = iso(NOW)
    if live:
        with open(STATE, "w") as f:
            json.dump(state, f, indent=2)
            f.write("\n")
    else:
        say("Test mode: state not saved.")


if __name__ == "__main__":
    code = 0
    try:
        main()
    except SystemExit as e:
        if e.code not in (None, 0):
            problem(str(e.code))
            code = 1
    except Exception as e:
        problem(f"{type(e).__name__}: {e}")
        code = 1
    if problems:
        code = 1
    if SUMMARY:
        with open(SUMMARY, "a") as f:
            f.write("\n".join(lines + [f"**Problem:** {p}" for p in problems]) + "\n")
    sys.exit(code)

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
        root = self.trading("GetItem", f"<ItemID>{int(item_id)}</ItemID>")
        it = root.find("e:Item", NS)
        ss = it.find("e:SellingStatus", NS)
        return {
            "title": it.findtext("e:Title", "", NS),
            "type": it.findtext("e:ListingType", "", NS),          # Chinese = auction, FixedPriceItem
            "status": ss.findtext("e:ListingStatus", "", NS),       # Active, Completed, Ended
            "bids": int(ss.findtext("e:BidCount", "0", NS) or 0),
            "sold": int(ss.findtext("e:QuantitySold", "0", NS) or 0),
            "qty": int(it.findtext("e:Quantity", "1", NS) or 1),
        }

    def end(self, item_id):
        self.trading("EndItem", f"<ItemID>{int(item_id)}</ItemID><EndingReason>NotAvailable</EndingReason>")


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
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise SystemExit("Missing repository secrets: " + ", ".join(missing))
    wn = Whatnot(env["WHATNOT_API_TOKEN"])

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

    def act(desc, fn):
        if live:
            fn()
            say("- DONE: " + desc)
        else:
            say("- WOULD: " + desc)

    # 1. Find Whatnot products linked to eBay listings (external_id "ebay:<item id>"), newest first.
    newest = last_products
    for p in wn.pages("/products"):
        created = parse_ts(p.get("created_at"))
        if created and created <= last_products and links:
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
    say(f"Linked cards: {len(links)}")

    open_links = {k: l for k, l in links.items()
                  if l["state"] in ("active", "hidden_bid", "hidden_error", "pulled_for_show")}
    if not open_links:
        save(state, links, newest, NOW, live)
        return

    # 2. Whatnot copies' current quantities.
    for l in open_links.values():
        v = wn.call("GET", f"/products/{l['wn_product']}/variants/{l['wn_variant']}")
        l["_wn_qty"] = ((v or {}).get("inventory") or {}).get("available_quantity")
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
        eb = Ebay(env["EBAY_CLIENT_ID"], env["EBAY_CLIENT_SECRET"], env["EBAY_REFRESH_TOKEN"])
        status = {eid: eb.item(eid) for eid in open_links}
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
            if l["state"] in ("hidden_bid", "hidden_error") and wn_qty == 0:
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

        # eBay listing open, no bids, not sold on either site.
        if l["state"] == "hidden_error" and wn_qty == 0:
            act(f"put Whatnot copy of {title} back (eBay reachable again)", lambda l=l: wn.set_qty(l["wn_variant"], 1, "ebay-ok"))
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
        l.pop("_wn_qty", None)
    counts = {}
    for l in links.values():
        counts[l["state"]] = counts.get(l["state"], 0) + 1
    say("States: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    save(state, links, newest, NOW, live)


def save(state, links, newest_product, order_check, live):
    for l in links.values():
        l.pop("_wn_qty", None)
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

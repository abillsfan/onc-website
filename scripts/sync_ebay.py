#!/usr/bin/env python3
"""Pull ONC's active eBay listings (seller cwright181) and write data/ebay-listings.json.

Runs from GitHub Actions (.github/workflows/ebay-sync.yml). Needs two repository secrets
from the eBay developer Production keyset:
  EBAY_CLIENT_ID      (App ID)
  EBAY_CLIENT_SECRET  (Cert ID)
Uses eBay's Browse API with an application token, so no eBay sign-in or user token is needed.
If eBay errors out, the script exits with a failure and leaves the existing file alone.
"""
import base64, json, os, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

SELLER = "cwright181"
SELLER_URL = "https://www.ebay.com/sch/i.html?_ssn=" + SELLER
# Browse search needs a category; these cover sports cards/memorabilia and trading card games (Pokemon etc.)
CATEGORIES = ["64482", "212", "261328", "2536", "183454"]
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "ebay-listings.json")
API = "https://api.ebay.com"


def token(cid, secret):
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                   "scope": "https://api.ebay.com/oauth/api_scope"}).encode()
    req = urllib.request.Request(API + "/identity/v1/oauth2/token", data=body, headers={
        "Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"]


def search(tok, cat):
    items, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"category_ids": cat, # Browse search drops pure auctions unless buyingOptions is listed explicitly
                                    "filter": "sellers:{%s},buyingOptions:{AUCTION|FIXED_PRICE|BEST_OFFER}" % SELLER,
                                    "limit": 200, "offset": offset})
        req = urllib.request.Request(API + "/buy/browse/v1/item_summary/search?" + q, headers={
            "Authorization": "Bearer " + tok, "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        page = data.get("itemSummaries", [])
        items += page
        offset += len(page)
        if not page or offset >= data.get("total", 0) or offset >= 10000:
            return items


def get_item(tok, legacy_id):
    q = urllib.parse.urlencode({"legacy_item_id": legacy_id})
    req = urllib.request.Request(API + "/buy/browse/v1/item/get_item_by_legacy_id?" + q, headers={
        "Authorization": "Bearer " + tok, "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return None
        if e.code == 400:  # e.g. item group / ended listing
            return None
        raise


def money(p):
    if not p or p.get("value") in (None, ""):
        return None
    return "${:,.2f}".format(float(p["value"]))


def convert(s):
    opts = s.get("buyingOptions", [])
    auction = "AUCTION" in opts
    bin_ = "FIXED_PRICE" in opts
    lid = s.get("legacyItemId") or s.get("itemId", "").split("|")[1 if "|" in s.get("itemId", "") else 0]
    ends = s.get("itemEndDate")
    if not ends:  # Good 'Til Cancelled fixed-price listings may have no end date
        ends = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    img = (s.get("image") or {}).get("imageUrl") or ((s.get("thumbnailImages") or [{}])[0]).get("imageUrl", "")
    if auction:
        price = money(s.get("currentBidPrice")) or money(s.get("price"))
        buy_it_now = money(s.get("price")) if bin_ and s.get("currentBidPrice") else None
    else:
        price = money(s.get("price"))
        buy_it_now = price
    return {
        "id": str(lid),
        "title": s.get("title", ""),
        "url": "https://www.ebay.com/itm/" + str(lid),
        "img": img,
        "price": price,
        "bids": int(s.get("bidCount") or 0),
        "buy_it_now": buy_it_now,
        "fixed": not auction,
        "ends": ends,
    }


def main():
    cid, secret = os.environ.get("EBAY_CLIENT_ID"), os.environ.get("EBAY_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("Missing EBAY_CLIENT_ID / EBAY_CLIENT_SECRET secrets")
    try:
        with open(OUT) as f:
            old = json.load(f)
    except Exception:
        old = {}
    old_ends = {i.get("id"): i.get("ends") for i in old.get("items", [])}
    tok = token(cid, secret)
    seen, items = set(), []
    for cat in CATEGORIES:
        found = search(tok, cat)
        print(f"::notice::category {cat}: {len(found)} listings")
        for s in found:
            it = convert(s)
            if not s.get("itemEndDate") and old_ends.get(it["id"]):
                it["ends"] = old_ends[it["id"]]  # keep placeholder end date stable between runs
            if it["id"] and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
    # Backstop: listings already on the site that search missed (e.g. just listed, not indexed yet)
    now = datetime.now(timezone.utc)
    for oid in old_ends:
        if not oid or oid in seen:
            continue
        s = get_item(tok, oid)
        if not s:
            continue
        end = s.get("itemEndDate")
        avail = (s.get("estimatedAvailabilities") or [{}])[0].get("estimatedAvailabilityStatus", "IN_STOCK")
        if (end and datetime.fromisoformat(end.replace("Z", "+00:00")) <= now) or avail == "OUT_OF_STOCK":
            continue
        s.setdefault("legacyItemId", oid)
        it = convert(s)
        if not end and old_ends.get(oid):
            it["ends"] = old_ends[oid]
        seen.add(it["id"])
        items.append(it)
        print(f"::notice::kept {oid} via direct lookup")
    items.sort(key=lambda i: i["ends"])
    if old.get("items") == items:
        print(f"No changes ({len(items)} listings)")
        return
    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "seller_url": SELLER_URL, "items": items}
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    print(f"Wrote {len(items)} listings")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400].replace("\n", " ")
        print(f"::error::eBay API {e.code} at {e.url.split('?')[0]}: {body}")
        sys.exit(1)
    except SystemExit as e:
        if e.code not in (None, 0):
            print(f"::error::{e.code}")
        raise
    except Exception as e:
        print(f"::error::{type(e).__name__}: {e}")
        sys.exit(1)

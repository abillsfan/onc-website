import json, os, urllib.parse, urllib.request, urllib.error, sys
sys.path.insert(0, os.path.dirname(__file__))
from sync_ebay import token, API
tok = token(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"])
H = {"Authorization": "Bearer " + tok, "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}
def get(path, q):
    req = urllib.request.Request(API + path + "?" + urllib.parse.urlencode(q), headers=H)
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
    except urllib.error.HTTPError as e: return {"HTTP": e.code, "body": e.read().decode()[:300]}
for lid in ["327423286649", "327423300350", "327418798947"]:
    d = get("/buy/browse/v1/item/get_item_by_legacy_id", {"legacy_item_id": lid})
    print("::notice::item %s -> %s" % (lid, json.dumps({k: d.get(k) for k in ["HTTP","body","categoryId","buyingOptions","itemEndDate","itemCreationDate","seller","estimatedAvailabilities"]})[:900]))
for q in [{"q": "Trey Lance", "filter": "sellers:{cwright181}"}, {"q": "Guerrero", "filter": "sellers:{cwright181}"},
          {"category_ids": "261328", "filter": "sellers:{cwright181}"}, {"category_ids": "261328", "filter": "sellers:{cwright181},buyingOptions:{AUCTION|FIXED_PRICE|BEST_OFFER}"}]:
    d = get("/buy/browse/v1/item_summary/search", dict(q, limit=50))
    print("::notice::search %s -> total %s ids %s %s" % (q, d.get("total"), [s.get("legacyItemId") for s in d.get("itemSummaries", [])], d.get("body","")[:200]))

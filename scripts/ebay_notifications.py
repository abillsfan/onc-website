#!/usr/bin/env python3
"""Point eBay's instant notifications (sold, bid, new/changed/ended listing) at the Cloudflare hook,
so the cross-list sync starts within seconds. Run by .github/workflows/hook-deploy.yml after deploying.

Env: HOOK_URL (the worker's https URL), EBAY_SYNC_CLIENT_ID / EBAY_SYNC_CLIENT_SECRET, EBAY_REFRESH_ENC.
"""
import json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from crosslist_sync import Ebay, NS  # noqa: E402

EVENTS = ["FixedPriceTransaction", "AuctionCheckoutComplete", "ItemSold", "BidReceived", "EndOfAuction",
          "ItemListed", "ItemRevised", "ItemClosed", "ItemUnsold"]


def main():
    url = os.environ["HOOK_URL"].rstrip("/") + "/ebay"
    secret = os.environ["EBAY_SYNC_CLIENT_SECRET"].strip()
    with open(os.environ["EBAY_REFRESH_ENC"]) as f:
        enc = json.load(f)["enc"]
    refresh = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-base64", "-A",
                              "-pass", "env:ENC_KEY"], input=enc.encode(), capture_output=True, check=True,
                             env=dict(os.environ, ENC_KEY=secret)).stdout.decode().strip()
    print("::add-mask::" + refresh)
    eb = Ebay(os.environ["EBAY_SYNC_CLIENT_ID"], secret, refresh)
    events = "".join(f"<NotificationEnable><EventType>{e}</EventType><EventEnable>Enable</EventEnable></NotificationEnable>"
                     for e in EVENTS)
    eb.trading("SetNotificationPreferences",
               "<ApplicationDeliveryPreferences>"
               f"<ApplicationURL>{url}</ApplicationURL><ApplicationEnable>Enable</ApplicationEnable>"
               "<DeviceType>Platform</DeviceType><PayloadVersion>1349</PayloadVersion>"
               "</ApplicationDeliveryPreferences>"
               f"<UserDeliveryPreferenceArray>{events}</UserDeliveryPreferenceArray>")
    root = eb.trading("GetNotificationPreferences", "<PreferenceLevel>Application</PreferenceLevel>")
    got_url = root.findtext("e:ApplicationDeliveryPreferences/e:ApplicationURL", "", NS)
    state = root.findtext("e:ApplicationDeliveryPreferences/e:ApplicationEnable", "", NS)
    root = eb.trading("GetNotificationPreferences", "<PreferenceLevel>User</PreferenceLevel>")
    on = [n.findtext("e:EventType", "", NS) for n in root.findall("e:UserDeliveryPreferenceArray/e:NotificationEnable", NS)
          if n.findtext("e:EventEnable", "", NS) == "Enable"]
    print(f"eBay notifications -> {got_url} ({state}); events on: {', '.join(on)}")
    if got_url != url or state != "Enable":
        sys.exit("eBay didn't save the notification address")


if __name__ == "__main__":
    main()

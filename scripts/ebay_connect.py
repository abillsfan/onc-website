#!/usr/bin/env python3
"""One-time eBay connection for the cross-list sync (run by .github/workflows/ebay-connect.yml).

eBay's developer site only shows a 2-hour user token, so this gets the ~18-month refresh token the
proper way: Carl signs in with the OAuth link, eBay sends him to a "Thank You" page whose address has
a one-time code (good for 5 minutes, useless without our app's secret). He pastes that address into
the workflow's Run form; this script trades the code for a refresh token and saves it ENCRYPTED
(AES-256, key = EBAY_SYNC_CLIENT_SECRET, which only GitHub secrets hold) as ebay-refresh.enc on the
crosslist-state branch. The sync decrypts it each run. No token is ever printed.
"""
import base64, json, os, subprocess, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

RUNAME = "Carl_Wright-CarlWrig-CardVa-zvcmfuxih"  # eBay redirect name (not a secret)
OUT = os.environ.get("EBAY_ENC_OUT", "ebay-refresh.enc")


def main():
    raw = (os.environ.get("SIGNIN_ADDRESS") or "").strip()
    cid, secret = os.environ.get("EBAY_SYNC_CLIENT_ID", "").strip(), os.environ.get("EBAY_SYNC_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        sys.exit("EBAY_SYNC_CLIENT_ID / EBAY_SYNC_CLIENT_SECRET secrets are missing")
    if "code=" in raw:
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(raw).query).get("code", [""])[0]
    else:
        code = urllib.parse.unquote(raw)
    if not code.startswith("v^1.1#"):
        sys.exit("That doesn't look like the eBay sign-in address. Paste the whole address of the eBay 'Thank You' page.")
    print("::add-mask::" + code)
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": RUNAME}).encode()
    req = urllib.request.Request("https://api.ebay.com/identity/v1/oauth2/token", data=body, headers={
        "Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "onc-crosslist-sync/1.0 (+https://on-cards.com)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        if "expired" in msg or "invalid_grant" in msg:
            msg += " (The code only works once and for 5 minutes. Sign in again and paste the new address right away.)"
        sys.exit(f"eBay rejected the sign-in code: HTTP {e.code} {msg}")
    refresh = tok.get("refresh_token")
    if not refresh:
        sys.exit("eBay didn't return a refresh token")
    print("::add-mask::" + refresh)
    print("::add-mask::" + tok.get("access_token", "x"))
    enc = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-base64",
                          "-A", "-pass", "env:ENC_KEY"], input=refresh.encode(), capture_output=True,
                         env=dict(os.environ, ENC_KEY=secret), check=True).stdout.decode().strip()
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("refresh_token_expires_in", 0)))
    with open(OUT, "w") as f:
        json.dump({"enc": enc, "expires": expires.strftime("%Y-%m-%d"), "saved": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, f, indent=1)
        f.write("\n")
    print(f"eBay connected. Long-lasting key saved (encrypted). It expires {expires:%B %d, %Y}.")


if __name__ == "__main__":
    main()

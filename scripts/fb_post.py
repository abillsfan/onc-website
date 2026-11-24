#!/usr/bin/env python3
"""Post queued reels to the Outta Nowhere Cards Facebook Page through Facebook's own Graph API (free, no Zapier).

Queue: fb-outbox/queue/<id>.json   {"id", "kind": "game"|"hit"|..., "file_url", "title", "description"}
Done:  fb-outbox/done/<id>.json    same + {"status": "posted"|"skipped"|"failed", "fb_video_id", "posted_at", "method", "error"}

Safety: at most MAX_PER_DAY posts in any rolling 24 hours (Facebook's own Reels limit is 30), never the same video
file or caption twice. `--check` only tests the key and writes fb-outbox/status.json.
Needs the FB_PAGE_TOKEN secret (a Page access token for the Outta Nowhere Cards Page).
"""
import datetime as dt, glob, json, os, sys, time, urllib.error, urllib.parse, urllib.request

PAGE_ID = "1252006641328350"
API = os.environ.get("FB_API", "https://graph.facebook.com/v27.0")
RUPLOAD = os.environ.get("FB_RUPLOAD", "https://rupload.facebook.com/video-upload/v27.0")
MAX_PER_DAY = 6
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fb-outbox")
TOKEN = os.environ.get("FB_PAGE_TOKEN", "").strip()  # Page key, or Carl's long-lived user key (turned into a Page key below)
SECRET_GIVEN = TOKEN


def now():
    return dt.datetime.now(dt.timezone.utc)


def call(method, url, params=None, headers=None):
    data = None
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode(params)
    elif params is not None:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            err = json.loads(body).get("error", {})
            msg = f"{err.get('message')} (code {err.get('code')}/{err.get('error_subcode')})"
        except Exception:
            msg = body[:300]
        raise RuntimeError(f"HTTP {e.code}: {msg}") from None


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write("\n")


def page_token():
    """The secret may hold a Page key, or a user key (60 days) from which the Page key is fetched each run."""
    global TOKEN
    me = call("GET", f"{API}/me", {"fields": "id,name", "access_token": TOKEN})
    if me.get("id") == PAGE_ID:
        return me, None
    page = call("GET", f"{API}/{PAGE_ID}", {"fields": "id,name,access_token", "access_token": TOKEN})
    if not page.get("access_token"):
        raise RuntimeError("This Facebook account can't post to the Outta Nowhere Cards Page")
    expires = None
    try:
        dbg = call("GET", f"{API}/debug_token", {"input_token": TOKEN, "access_token": TOKEN}).get("data", {})
        if dbg.get("expires_at"):
            expires = dt.datetime.fromtimestamp(dbg["expires_at"], dt.timezone.utc)
    except Exception:
        pass
    TOKEN = page["access_token"]
    return {"id": page["id"], "name": page.get("name")}, expires


def check():
    st = {"checked_at": now().isoformat(timespec="seconds")}
    try:
        if not TOKEN:
            raise RuntimeError("FB_PAGE_TOKEN secret is missing")
        me, expires = page_token()
        if expires:
            st["key_expires"] = expires.date().isoformat()
            days = (expires - now()).days
            if days < 14:
                st["warning"] = f"The Facebook key in GitHub expires in {days} days ({expires.date()}). Ask Claude to renew it."
        st.update(ok=me.get("id") == PAGE_ID, page_id=me.get("id"), page_name=me.get("name"))
        if me.get("id") != PAGE_ID:
            st["error"] = "This key is not a Page key for Outta Nowhere Cards (it belongs to %s)." % me.get("name")
    except Exception as e:
        msg = str(e)
        for t in (TOKEN, SECRET_GIVEN):
            if t:
                msg = msg.replace(t, "***")
        st.update(ok=False, error=msg)
    write(os.path.join(ROOT, "status.json"), st)
    print(json.dumps(st, indent=1))
    return st["ok"]


def wait_until_done(video_id, limit=600):
    t0 = time.time()
    last = {}
    while time.time() - t0 < limit:
        last = call("GET", f"{API}/{video_id}", {"fields": "status", "access_token": TOKEN}).get("status", {})
        vs = last.get("video_status")
        if vs in ("ready", "published") or (last.get("publishing_phase") or {}).get("status") == "complete":
            return "ready", last
        if vs == "error" or any((last.get(p) or {}).get("status") == "error" for p in ("uploading_phase", "processing_phase", "publishing_phase")):
            return "error", last
        time.sleep(15)
    return "timeout", last


def post_reel(item):
    start = call("POST", f"{API}/{PAGE_ID}/video_reels", {"upload_phase": "start", "access_token": TOKEN})
    vid = start["video_id"]
    call("POST", f"{RUPLOAD}/{vid}", {}, headers={"Authorization": f"OAuth {TOKEN}", "file_url": item["file_url"]})
    call("POST", f"{API}/{PAGE_ID}/video_reels", {
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
        "description": item.get("description", ""), "title": item.get("title", ""), "access_token": TOKEN})
    state, detail = wait_until_done(vid)
    if state == "error":
        raise RuntimeError("Facebook couldn't publish the reel: " + json.dumps(detail)[:400])
    return vid, "reel" + ("" if state == "ready" else " (still processing)")


def post_video(item):
    r = call("POST", f"{API}/{PAGE_ID}/videos", {
        "file_url": item["file_url"], "title": item.get("title", ""), "description": item.get("description", ""),
        "access_token": TOKEN})
    return r["id"], "video"


def main():
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    done = []
    for p in glob.glob(os.path.join(ROOT, "done", "*.json")):
        try:
            done.append(json.load(open(p)))
        except Exception:
            pass
    posted = [d for d in done if d.get("status") == "posted"]
    queue = sorted(glob.glob(os.path.join(ROOT, "queue", "*.json")))
    if not queue:
        print("Nothing queued.")
        return
    if not TOKEN:
        print("::error::FB_PAGE_TOKEN secret is missing; nothing posted.")
        sys.exit(1)
    page_token()
    for path in queue:
        item = json.load(open(path))
        item.setdefault("id", os.path.basename(path)[:-5])
        out = dict(item, processed_at=now().isoformat(timespec="seconds"))
        recent = [d for d in posted if d.get("posted_at") and now() - dt.datetime.fromisoformat(d["posted_at"]) < dt.timedelta(hours=24)]
        if not item.get("file_url") or not item.get("description"):
            out.update(status="failed", error="request needs file_url and description")
        elif any(d.get("file_url") == item["file_url"] or d.get("description") == item["description"] for d in posted):
            out.update(status="skipped", error="duplicate: this video or caption was already posted")
        elif len(recent) >= MAX_PER_DAY:
            print(f"Daily safety cap reached ({MAX_PER_DAY} posts in 24h); leaving {item['id']} queued.")
            continue
        else:
            try:
                try:
                    vid, method = post_reel(item)
                except Exception as e:
                    print(f"Reel upload didn't work ({e}); posting as a regular Page video instead.")
                    vid, method = post_video(item)
                out.update(status="posted", fb_video_id=vid, method=method, posted_at=now().isoformat(timespec="seconds"))
                posted.append(out)
            except Exception as e:
                out.update(status="failed", error=str(e).replace(TOKEN, "***").replace(SECRET_GIVEN, "***"))
        write(os.path.join(ROOT, "done", f"{item['id']}.json"), out)
        os.remove(path)
        print(f"{item['id']}: {out['status']} {out.get('fb_video_id') or out.get('error') or ''}")


if __name__ == "__main__":
    main()

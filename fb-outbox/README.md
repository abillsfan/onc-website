# Facebook outbox

Free Facebook posting for the Outta Nowhere Cards Page (replaces Zapier). Run by `.github/workflows/fb-post.yml`
with `scripts/fb_post.py`.

**To post a reel:** commit and push a file `fb-outbox/queue/<id>.json` (id like `2026-11-24-game` or
`2026-11-24-hit-ohtani`) to `main`:

```json
{"id": "2026-11-24-game", "kind": "game", "file_url": "https://raw.githubusercontent.com/abillsfan/onc-media/main/reels/<file>.mp4",
 "title": "Short title", "description": "The caption, exactly as it should appear"}
```

Within a few minutes the workflow posts it as a Reel (falls back to a regular Page video if the Reel upload fails),
then moves it to `fb-outbox/done/<id>.json` with `status` (`posted`, `skipped`, `failed`), `fb_video_id`,
`posted_at` and any `error`. `git pull` and read that file to get the result.

Safety: at most 6 posts in any 24 hours (Facebook allows 30 Reels), and a video file or caption that was already
posted is skipped. `fb-outbox/status.json` shows whether the Facebook key works (updated each run, or when
`fb-outbox/check.txt` changes).

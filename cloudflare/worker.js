// onc-crosslist-hook: starts the eBay + Whatnot cross-list sync (GitHub workflow crosslist-sync.yml).
//  * POST /whatnot  - Whatnot webhooks (orders.created etc.)      -> start the sync right away
//  * POST /ebay     - eBay Platform Notifications (sold, bid, new listing) -> start the sync right away
//  * every 2 hours (cron trigger)                                  -> start the sync (backup; alerts if it finds a miss)
// A poke only STARTS the sync; the sync itself reads both sites' real data, so a fake poke can't
// change anything. Pokes are throttled to one per 10 seconds; GitHub queues at most one run behind
// the current one, so bursts collapse and the sync stays well inside eBay's daily API limit.
const WORKFLOW = "https://api.github.com/repos/abillsfan/onc-website/actions/workflows/crosslist-sync.yml/dispatches";
const THROTTLE_SECONDS = 10; // a run started this recently will still read the newer data

async function startSync(env, why, force) {
  const cache = caches.default;
  const key = new Request("https://onc-crosslist-hook.internal/last-start");
  if (!force && (await cache.match(key))) return "throttled";
  await cache.put(key, new Response(why, { headers: { "Cache-Control": `max-age=${THROTTLE_SECONDS}` } }));
  const r = await fetch(WORKFLOW, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "onc-crosslist-hook",
    },
    body: JSON.stringify({ ref: "main", inputs: { reason: why.startsWith("timer") ? "timer" : why.slice(0, 90) } }),
  });
  if (r.status !== 204) console.log("GitHub dispatch failed", r.status, await r.text());
  return `started (${r.status})`;
}

export default {
  async fetch(req, env, ctx) {
    const path = new URL(req.url).pathname;
    if (req.method === "GET") return new Response("onc-crosslist-hook ok");
    if (req.method !== "POST" || (path !== "/whatnot" && path !== "/ebay")) {
      return new Response("not found", { status: 404 });
    }
    const body = await req.text();
    let why = path.slice(1);
    const m = body.match(/<(?:\w+:)?NotificationEventName>([^<]+)</) || body.match(/"type"\s*:\s*"([^"]+)"/);
    if (m) why += ":" + m[1];
    ctx.waitUntil(startSync(env, why, false).then((s) => console.log(why, s)));
    return new Response("ok"); // both senders only need a quick 200
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(startSync(env, "timer", true));
  },
};

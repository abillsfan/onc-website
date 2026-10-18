/* =========================================================
   OUTTA NOWHERE CARDS — site settings
   Paste your real links here. Every button on the site updates.
   ========================================================= */
var ONC_LINKS = {
  whatnot:  "https://www.whatnot.com/user/outta_nowhere_cards",
  facebook: "https://www.facebook.com/profile.php?id=61593196346193"
};

(function () {
  // Apply links to every button
  document.querySelectorAll(".js-whatnot").forEach(function (a) { a.href = ONC_LINKS.whatnot; });
  document.querySelectorAll(".js-facebook").forEach(function (a) { a.href = ONC_LINKS.facebook; });

  // On Deck: show the lineup card until its show is over, then "stay tuned"
  var lineup = document.querySelector(".ondeck-lineup");
  var tbd = document.querySelector(".ondeck-tbd");
  if (lineup && tbd) {
    var until = Date.parse(lineup.getAttribute("data-until") || "");
    var showLineup = !isNaN(until) && Date.now() < until;
    lineup.hidden = !showLineup;
    tbd.hidden = showLineup;
  }

  // Live on eBay: cards from data/ebay-listings.json, with countdowns; ended auctions drop off
  var ebaySec = document.getElementById("ebay");
  var ebayGrid = document.getElementById("ebay-grid");
  if (ebaySec && ebayGrid && window.fetch) {
    var tz = "America/Chicago";
    var fmtEnd = function (d) {
      try {
        return d.toLocaleString("en-US", { timeZone: tz, weekday: "short", hour: "numeric", minute: "2-digit" }) + " CT";
      } catch (e) { return d.toLocaleString(); }
    };
    var left = function (ms) {
      var m = Math.floor(ms / 60000), d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60), mm = m % 60;
      if (d > 0) return d + "d " + h + "h left";
      if (h > 0) return h + "h " + mm + "m left";
      return mm + "m left";
    };
    var el = function (tag, cls, text) {
      var n = document.createElement(tag);
      if (cls) n.className = cls;
      if (text != null) n.textContent = text;
      return n;
    };
    var cards = [];
    var tick = function () {
      var now = Date.now(), live = 0;
      cards.forEach(function (c) {
        var ms = c.end - now;
        if (ms <= 0) { c.node.hidden = true; return; }
        live++;
        c.left.textContent = left(ms);
        c.node.classList.toggle("ending", ms < 24 * 3600e3);
      });
      ebaySec.hidden = live === 0;
    };
    fetch("data/ebay-listings.json?t=" + Math.floor(Date.now() / 600000))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var items = (data.items || []).slice().sort(function (a, b) { return Date.parse(a.ends) - Date.parse(b.ends); });
        items.forEach(function (it) {
          var end = new Date(it.ends);
          if (isNaN(end) || end <= Date.now()) return;
          var a = el("a", "ebay-card");
          a.href = it.url; a.target = "_blank"; a.rel = "noopener";
          a.setAttribute("aria-label", it.title + " on eBay");
          var pic = el("div", "ebay-img");
          var img = el("img"); img.src = it.img; img.alt = it.title; img.loading = "lazy"; img.referrerPolicy = "no-referrer";
          pic.appendChild(img); a.appendChild(pic);
          var body = el("div", "ebay-body");
          body.appendChild(el("h3", null, it.title));
          var price = el("div", "ebay-price");
          price.appendChild(el("span", "lbl", it.bids > 0 ? "Current bid" : "Starting bid"));
          price.appendChild(el("strong", null, it.price));
          price.appendChild(el("span", "bids", (it.bids || 0) + (it.bids === 1 ? " bid" : " bids")));
          body.appendChild(price);
          if (it.buy_it_now) body.appendChild(el("div", "ebay-bin", "Buy It Now " + it.buy_it_now));
          var ends = el("div", "ebay-ends");
          var lft = el("strong", null, "");
          ends.appendChild(lft);
          ends.appendChild(document.createTextNode(" · Ends " + fmtEnd(end)));
          body.appendChild(ends);
          var cta = el("span", "btn btn-whatnot btn-sm");
          cta.appendChild(el("span", null, "Bid on eBay"));
          body.appendChild(cta);
          a.appendChild(body);
          ebayGrid.appendChild(a);
          cards.push({ node: a, end: end.getTime(), left: lft });
        });
        var up = document.getElementById("ebay-updated");
        if (up && data.updated) {
          var u = new Date(data.updated);
          if (!isNaN(u)) up.textContent = "Bids shown as of " + u.toLocaleString("en-US", { timeZone: tz, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) + " CT. Live bids on eBay.";
        }
        tick();
        setInterval(tick, 30000);
      })
      .catch(function () { /* leave section hidden */ });
  }

  // Release calendar: hide releases whose date has passed (Central time), then empty weeks
  var rcItems = document.querySelectorAll("[data-release-date]");
  if (rcItems.length) {
    var todayCT;
    try { todayCT = new Date().toLocaleDateString("en-CA", { timeZone: "America/Chicago" }); }
    catch (e) { todayCT = new Date().toISOString().slice(0, 10); }
    rcItems.forEach(function (el) { if (el.getAttribute("data-release-date") < todayCT) el.hidden = true; });
    document.querySelectorAll(".rc-week").forEach(function (w) {
      if (!w.querySelector("li[data-release-date]:not([hidden])")) w.hidden = true;
    });
  }

  // Score ticker: today's NFL, college football and MLB games (ESPN public scoreboard feed).
  // Hides itself when there are no games today or the feed is unavailable.
  (function () {
    var header = document.querySelector(".nav");
    if (!header || !window.fetch || !window.Promise) return;
    var leagues = [["NFL", "football/nfl"], ["NCAAF", "football/college-football"], ["MLB", "baseball/mlb"]];
    var tz = "America/Chicago";
    var dayCT = function (d) {
      try { return d.toLocaleDateString("en-CA", { timeZone: tz }); } catch (e) { return d.toISOString().slice(0, 10); }
    };
    var bar = document.createElement("div");
    bar.className = "ticker"; bar.hidden = true;
    bar.setAttribute("role", "region"); bar.setAttribute("aria-label", "Today's scores");
    var track = document.createElement("div"); track.className = "ticker-track";
    bar.appendChild(track); header.appendChild(bar);
    var timer = null;
    var txt = function (tag, cls, t) { var n = document.createElement(tag); if (cls) n.className = cls; n.textContent = t; return n; };
    var team = function (c, showScore) {
      var w = document.createElement("span");
      w.className = "tk-team" + (c.winner ? " win" : "");
      var rank = c.curatedRank && c.curatedRank.current && c.curatedRank.current <= 25 ? "#" + c.curatedRank.current + " " : "";
      w.appendChild(txt("span", "tk-abbr", rank + ((c.team && c.team.abbreviation) || "")));
      if (showScore) w.appendChild(txt("span", "tk-score", c.score != null ? String(c.score) : ""));
      return w;
    };
    var render = function (results) {
      var today = dayCT(new Date()), items = [], live = false;
      results.forEach(function (r) {
        if (!r[1] || !r[1].events) return;
        r[1].events.forEach(function (ev) {
          var st = ev.status && ev.status.type ? ev.status.type : {};
          var isToday = dayCT(new Date(ev.date)) === today;
          if (!isToday && st.state !== "in") return;
          var comp = ev.competitions && ev.competitions[0];
          if (!comp || !comp.competitors || comp.competitors.length < 2) return;
          var away = comp.competitors.filter(function (c) { return c.homeAway === "away"; })[0] || comp.competitors[1];
          var home = comp.competitors.filter(function (c) { return c.homeAway === "home"; })[0] || comp.competitors[0];
          var started = st.state === "in" || st.state === "post";
          var it = document.createElement("span");
          it.className = "tk-item" + (st.state === "in" ? " live" : "");
          it.appendChild(txt("span", "tk-league", r[0]));
          it.appendChild(team(away, started));
          it.appendChild(txt("span", "tk-at", started ? "" : "@"));
          it.appendChild(team(home, started));
          var status;
          if (st.state === "in") { status = st.shortDetail || "Live"; live = true; }
          else if (st.state === "post") { status = st.shortDetail || "Final"; }
          else {
            try { status = new Date(ev.date).toLocaleTimeString("en-US", { timeZone: tz, hour: "numeric", minute: "2-digit" }) + " CT"; }
            catch (e) { status = st.shortDetail || ""; }
          }
          it.appendChild(txt("span", "tk-status", status));
          items.push({ node: it, order: st.state === "in" ? 0 : st.state === "pre" ? 1 : 2, t: Date.parse(ev.date) || 0 });
        });
      });
      items.sort(function (a, b) { return a.order - b.order || a.t - b.t; });
      track.textContent = "";
      if (!items.length) { bar.hidden = true; }
      else {
        var src = txt("a", "tk-src", "Scores: ESPN");
        src.href = "https://www.espn.com/"; src.target = "_blank"; src.rel = "noopener";
        [0, 1].forEach(function (copy) {            // two copies for a seamless loop
          var g = document.createElement("span"); g.className = "tk-group";
          if (copy) g.setAttribute("aria-hidden", "true");
          items.forEach(function (i) { g.appendChild(copy ? i.node.cloneNode(true) : i.node); });
          g.appendChild(copy ? src.cloneNode(true) : src);
          track.appendChild(g);
        });
        bar.hidden = false;
        track.style.animationDuration = Math.max(25, items.length * 5) + "s";
      }
      clearTimeout(timer);
      timer = setTimeout(load, live ? 60000 : 600000);
    };
    var load = function () {
      Promise.all(leagues.map(function (l) {
        return fetch("https://site.api.espn.com/apis/site/v2/sports/" + l[1] + "/scoreboard")
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) { return [l[0], j]; })
          .catch(function () { return [l[0], null]; });
      })).then(render);
    };
    load();
  })();

  // Footer year
  var y = document.getElementById("year");
  if (y) y.textContent = new Date().getFullYear();

  // Mobile menu
  var toggle = document.querySelector(".nav-toggle");
  var links = document.querySelector(".nav-links");
  if (toggle && links) {
    toggle.addEventListener("click", function () {
      var open = links.classList.toggle("open");
      toggle.classList.toggle("open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    links.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () {
        links.classList.remove("open");
        toggle.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  // Reveal-on-scroll
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
      });
    }, { threshold: 0.12 });
    document.querySelectorAll(".pillar, .break-card, .steps li, .fb-poster, .fb-copy")
      .forEach(function (el) { el.classList.add("reveal"); io.observe(el); });
  }
})();

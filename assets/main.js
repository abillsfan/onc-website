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

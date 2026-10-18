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
    document.querySelectorAll(".pillar, .break-card, .steps li, .trust-item, .fb-poster, .fb-copy")
      .forEach(function (el) { el.classList.add("reveal"); io.observe(el); });
  }
})();

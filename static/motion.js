/* ClearPay motion: rolling numbers, cursor light, reveal on scroll, invoice scan, approval stamp, loading line.
   Page-to-page morphing is pure CSS (view transitions in style.css). Purely presentational: every element
   keeps its real text for screen readers, and with reduced motion only the instant parts run. */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var finePointer = window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  // ---------- 1. rolling numbers (₹34.65L, 12, 98%) ----------
  function roll(el, delay) {
    var text = el.textContent.trim();
    if (!/\d/.test(text) || el.children.length || el.dataset.odo) return;
    el.dataset.odo = "1";
    el.setAttribute("aria-label", text);
    var wrap = document.createElement("span");
    wrap.className = "odo";
    wrap.setAttribute("aria-hidden", "true");
    var strips = [], i = 0;
    Array.prototype.forEach.call(text, function (ch) {
      if (/\d/.test(ch)) {
        var cell = document.createElement("span"), strip = document.createElement("span");
        cell.className = "odo-d"; strip.className = "odo-s";
        for (var k = 0; k < 20; k++) {                      // two turns of 0-9, so each digit visibly spins
          var d = document.createElement("span"); d.textContent = String(k % 10); strip.appendChild(d);
        }
        cell.appendChild(strip); wrap.appendChild(cell);
        strips.push({ strip: strip, cell: cell, n: 10 + Number(ch), i: i++ });
      } else {
        var c = document.createElement("span"); c.className = "odo-c"; c.textContent = ch; wrap.appendChild(c);
      }
    });
    el.textContent = "";
    el.appendChild(wrap);
    setTimeout(function () {
      requestAnimationFrame(function () {
        wrap.classList.add("go");
        strips.forEach(function (s) {
          s.strip.style.transitionDelay = (s.i * 70) + "ms";
          s.strip.style.transform = "translateY(" + (-1.15 * s.n) + "em)";
          setTimeout(function () { s.cell.classList.add("done"); }, 1400 + s.i * 70);
        });
      });
    }, delay || 0);
  }
  if (!reduce) {
    var numbers = document.querySelectorAll(".stat .value, .an-big");
    var seen = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { seen.unobserve(e.target); roll(e.target, 120 + (Number(e.target.dataset.odoOrder) || 0) * 90); }
      });
    }, { threshold: 0.4 }) : null;
    Array.prototype.forEach.call(numbers, function (el, n) {
      el.dataset.odoOrder = String(n % 4);
      if (seen) seen.observe(el); else roll(el, 120);
    });
  }

  // ---------- 2. cursor light on cards ----------
  if (finePointer && !reduce) {
    var lit = null, frame = 0, last = null;
    document.addEventListener("pointermove", function (e) {
      last = e;
      if (frame) return;
      frame = requestAnimationFrame(function () {
        frame = 0;
        var card = last.target.closest && last.target.closest(".panel:not(.login-card), .an-card");
        if (card !== lit) { if (lit) lit.classList.remove("is-lit"); lit = card; if (lit) lit.classList.add("is-lit"); }
        if (lit) {
          var r = lit.getBoundingClientRect();
          lit.style.setProperty("--mx", (last.clientX - r.left) + "px");
          lit.style.setProperty("--my", (last.clientY - r.top) + "px");
        }
      });
    }, { passive: true });
    document.addEventListener("pointerleave", function () { if (lit) { lit.classList.remove("is-lit"); lit = null; } });
  }

  // ---------- 3. reveal on scroll: only blocks that start below the fold ----------
  if (!reduce && "IntersectionObserver" in window) {
    var reveal = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        reveal.unobserve(e.target);
        e.target.classList.add("rv-in");
        e.target.querySelectorAll("table").forEach(function (t) {
          t.classList.add("rv-rows");
          t.querySelectorAll("tbody tr").forEach(function (tr, i) { tr.style.setProperty("--r", Math.min(i, 18)); });
        });
      });
    }, { threshold: 0.08, rootMargin: "0px 0px -40px 0px" });
    var fold = window.innerHeight;
    document.querySelectorAll(".wrap > *, .grid-2 > *, .an-lower > *").forEach(function (el) {
      if (el.matches(".flash-src, script, .tabs") || el.closest(".rv")) return;
      if (el.getBoundingClientRect().top > fold - 20) { el.classList.add("rv"); reveal.observe(el); }
    });
  }

  // ---------- 4. invoice page: scan the original, then lock onto each problem ----------
  if (!reduce) {
    document.querySelectorAll(".doc").forEach(function (doc) {
      var start = function () {
        doc.querySelectorAll(".hl").forEach(function (hl) {
          var top = parseFloat(hl.style.top) || 0;                // where the beam reaches this box (1.6s sweep)
          hl.style.setProperty("--d", (0.2 + Math.min(top, 100) / 100 * 1.45).toFixed(2) + "s");
        });
        doc.classList.add("scan");
      };
      var img = doc.querySelector("img");
      if (img && !img.complete) img.addEventListener("load", start, { once: true }); else start();
    });
  }

  // ---------- 5. approve / reject: a stamp lands, then the form goes ----------
  function stamp(ok, label) {
    var stage = document.createElement("div"), s = document.createElement("div");
    stage.className = "stamp-stage";
    s.className = "stamp-x " + (ok ? "ok" : "bad");
    var ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg"), use = document.createElementNS(ns, "use");
    svg.setAttribute("class", "ic"); svg.setAttribute("aria-hidden", "true");
    use.setAttribute("href", (document.body.dataset.icons || "") + "#" + (ok ? "check" : "x"));
    svg.appendChild(use); s.appendChild(svg);
    s.appendChild(document.createTextNode(label));
    stage.appendChild(s);
    document.body.appendChild(stage);
  }
  document.addEventListener("submit", function (e) {
    var b = e.submitter, f = e.target;
    if (e.defaultPrevented || reduce || !b || f.dataset.stamped) return;
    var ok = b.classList.contains("btn-approve"), bad = b.classList.contains("btn-reject");
    if (!ok && !bad) return;
    var action = (b.getAttribute("formaction") || f.getAttribute("action") || "");
    if (ok && !/\/approve$/.test(action) && !/\/approve\b/.test(action)) return;   // only real invoice decisions
    if (bad && !/\/reject\b/.test(action)) return;
    e.preventDefault();
    f.dataset.stamped = "1";
    stamp(ok, ok ? "Approved" : "Rejected");
    setTimeout(function () { if (f.requestSubmit) f.requestSubmit(b); else b.click(); }, 720);
  });

  // ---------- 6. loading line while the next page loads ----------
  var bar = document.createElement("div");
  bar.className = "nav-progress";
  document.body.appendChild(bar);
  var go = function () { if (!reduce) requestAnimationFrame(function () { bar.classList.add("run"); }); };
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target === "_blank" || a.hasAttribute("download") || a.origin !== location.origin) return;
    if (a.pathname === location.pathname && a.search === location.search) return;       // same page / hash link
    if (/\.(csv|pdf|png|jpg)$/i.test(a.pathname)) return;                               // downloads stay put
    go();
  });
  document.addEventListener("submit", function (e) {
    setTimeout(function () { if (!e.defaultPrevented) go(); }, 0);
  });
  window.addEventListener("pageshow", function () { bar.classList.remove("run"); });   // back/forward cache
})();

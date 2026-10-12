/* ClearPay sign-in page motion: the invoice showcase loop and the card tilt.
   Purely decorative. With reduced motion it shows one finished check and stays still. */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------- showcase: an invoice being checked, three real ClearPay outcomes on a loop ----------
  var SCENES = [
    { inv: "INV-0423", vendor: "Alpha Office Supplies", po: "PO-2041", total: "₹1,18,000.00", bank: "•••• 4521",
      badRow: -1, badBank: false, stamp: "Matched to PO",
      log: [["Read invoice", "0.4s"], ["Vendor on file", "0.7s"], ["Maths checks out", "0.9s"],
            ["4 lines matched to PO-2041", "1.6s"], ["Bank account unchanged", "2.0s"], ["Sent for approval", "2.7s"]] },
    { inv: "INV-7781", vendor: "Northwind Traders", po: "PO-1187", total: "₹2,36,500.00", bank: "•••• 9907",
      badRow: -1, badBank: true, stamp: "Flagged · bank changed",
      log: [["Read invoice", "0.4s"], ["Vendor on file", "0.6s"], ["Maths checks out", "0.9s"],
            ["4 lines matched to PO-1187", "1.6s"], ["Bank account differs from record", "2.0s", 1], ["Held for AP review", "2.7s", 1]] },
    { inv: "INV-3310", vendor: "Kaveri Packaging", po: "PO-2276", total: "₹64,900.00", bank: "•••• 1186",
      badRow: 2, badBank: false, stamp: "Flagged · price above PO",
      log: [["Read invoice", "0.3s"], ["Vendor on file · MSME", "0.6s"], ["Maths checks out", "0.8s"],
            ["Line 3: ₹310 vs PO ₹250", "1.3s", 1], ["Bank account unchanged", "2.0s"], ["Vendor email drafted", "2.7s", 1]] }
  ];
  var scene = document.getElementById("ls-scene");
  if (scene) {                                      // hidden on narrow screens, where it costs nothing
    var $ = function (id) { return document.getElementById(id); };
    var rows = scene.querySelectorAll(".ls-rows li"), bank = scene.querySelector(".ls-bank"), log = $("ls-log"), stamp = $("ls-stamp");
    var i = 0;
    var show = function (s) {
      $("ls-inv").textContent = s.inv; $("ls-vendor").textContent = s.vendor; $("ls-po").textContent = s.po;
      $("ls-total").textContent = s.total; $("ls-bank").textContent = s.bank;
      rows.forEach(function (li, n) { li.classList.toggle("bad", n === s.badRow); li.querySelector(".ls-tick").classList.toggle("bad", n === s.badRow); });
      bank.classList.toggle("bad", s.badBank); bank.querySelector(".ls-tick").classList.toggle("bad", s.badBank);
      var bad = s.badRow >= 0 || s.badBank;
      stamp.textContent = s.stamp; stamp.classList.toggle("bad", bad);
      log.replaceChildren();
      s.log.forEach(function (step, n) {
        var li = document.createElement("li"), dot = document.createElement("i"), txt = document.createElement("span"), t = document.createElement("small");
        txt.textContent = step[0]; t.textContent = step[1];
        if (step[2]) li.className = "bad";
        li.style.animationDelay = (0.3 + n * 0.42) + "s";
        li.append(dot, txt, t); log.appendChild(li);
      });
      scene.classList.remove("run", "out");
      void scene.offsetWidth;                       // restart the CSS animations
      scene.classList.add("run");
    };
    show(SCENES[0]);
    if (!reduce) {
      setInterval(function () {
        scene.classList.add("out");
        setTimeout(function () { i = (i + 1) % SCENES.length; show(SCENES[i]); }, 450);
      }, 6200);
    }
  }

  // ---------- fit to the screen: on a short window, scale down instead of cutting anything off ----------
  var wrap = document.querySelector(".login-wrap"), col = document.getElementById("login-col"),
      stage = document.querySelector(".login-stage"), inner = document.getElementById("ls-inner");
  var fitOne = function (el, avail) {
    el.style.scale = "";
    var need = el.offsetHeight;                      // layout height; `scale` doesn't change it
    var s = need > avail && avail > 0 ? Math.max(0.6, avail / need) : 1;
    el.style.scale = s < 1 ? s.toFixed(3) : "";
  };
  var padY = function (el) { var cs = getComputedStyle(el); return parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom); };
  var fit = function () {
    if (wrap && col) fitOne(col, wrap.clientHeight - padY(wrap));
    if (stage && inner && getComputedStyle(stage).display !== "none") fitOne(inner, stage.clientHeight - padY(stage));
  };
  var queued = 0;
  var refit = function () { if (!queued) queued = requestAnimationFrame(function () { queued = 0; fit(); }); };
  fit();
  window.addEventListener("resize", refit);
  window.addEventListener("load", refit);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(refit);

  // ---------- sign-in card: gentle tilt and a spotlight that follows the pointer ----------
  var tilt = document.getElementById("login-tilt");
  if (tilt && !reduce && window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
    var card = tilt.querySelector(".login-card"), frame = 0;
    tilt.addEventListener("pointermove", function (e) {
      if (frame) return;
      frame = requestAnimationFrame(function () {
        frame = 0;
        var r = tilt.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
        tilt.classList.add("is-tracking");
        tilt.style.setProperty("--ry", ((x - 0.5) * 5).toFixed(2) + "deg");
        tilt.style.setProperty("--rx", ((0.5 - y) * 4).toFixed(2) + "deg");
        card.style.setProperty("--mx", (x * 100).toFixed(1) + "%");
        card.style.setProperty("--my", (y * 100).toFixed(1) + "%");
      });
    });
    tilt.addEventListener("pointerleave", function () {
      tilt.classList.remove("is-tracking");
      tilt.style.setProperty("--rx", "0deg"); tilt.style.setProperty("--ry", "0deg");
    });
  }

  // ---------- opening titles: once per browser session (theme.js decides before first paint; CSS runs them) ----------
  if (document.getElementById("ls-intro") && document.documentElement.classList.contains("intro-play")) {
    try { sessionStorage.setItem("cp-intro", "1"); } catch (e) {}
  }

  // ---------- headline: split into words so they arrive one by one (the gradient phrase moves as one) ----------
  var h2 = document.querySelector(".ls-copy h2");
  if (h2 && !reduce) {
    var wi = 0, newWord = function () {
      var w = document.createElement("span"); w.className = "ls-w"; w.style.setProperty("--i", wi++); return w;
    };
    Array.prototype.slice.call(h2.childNodes).forEach(function (node) {
      if (node.nodeType === 3) {
        var frag = document.createDocumentFragment();
        node.textContent.split(/(\s+)/).forEach(function (part) {
          if (!part) return;
          if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
          var w = newWord(); w.textContent = part; frag.appendChild(w);
        });
        h2.replaceChild(frag, node);
      } else if (node.nodeType === 1) {
        var w = newWord(); h2.replaceChild(w, node); w.appendChild(node);
      }
    });
  }

  // ---------- depth: the showcase layers and the aurora drift with the pointer ----------
  var sceneEl = document.getElementById("ls-scene"), aurora = document.querySelector(".ls-aurora");
  if (!reduce && window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
    var pf = 0, pe = null;
    window.addEventListener("pointermove", function (e) {
      pe = e;
      if (pf) return;
      pf = requestAnimationFrame(function () {
        pf = 0;
        var px = (pe.clientX / window.innerWidth - .5) * 2, py = (pe.clientY / window.innerHeight - .5) * 2;
        [sceneEl, aurora].forEach(function (el) {
          if (el) { el.style.setProperty("--px", px.toFixed(3)); el.style.setProperty("--py", py.toFixed(3)); }
        });
      });
    }, { passive: true });
  }

  // ---------- sign-in card: magnetic button, ripples, and a glow while it signs in ----------
  var cardEl = document.querySelector(".login-card");
  if (cardEl && !reduce) {
    var btn = cardEl.querySelector(".btn-primary");
    if (btn && window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
      cardEl.addEventListener("pointermove", function (e) {
        var r = btn.getBoundingClientRect(), dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
        var rx = r.width / 2 + 28, ry = r.height / 2 + 22, inside = Math.abs(dx) < rx && Math.abs(dy) < ry;
        btn.style.setProperty("--bx", inside ? (dx / rx * 6).toFixed(1) + "px" : "0px");
        btn.style.setProperty("--by", inside ? (dy / ry * 4).toFixed(1) + "px" : "0px");
      });
      cardEl.addEventListener("pointerleave", function () { btn.style.setProperty("--bx", "0px"); btn.style.setProperty("--by", "0px"); });
    }
    cardEl.addEventListener("pointerdown", function (e) {
      var b = e.target.closest && e.target.closest(".btn-primary, .btn-google");
      if (!b) return;
      var r = b.getBoundingClientRect(), dot = document.createElement("span");
      dot.className = "ls-ripple"; dot.style.left = (e.clientX - r.left) + "px"; dot.style.top = (e.clientY - r.top) + "px";
      b.appendChild(dot); setTimeout(function () { dot.remove(); }, 750);
    });
    var sendOff = function () { cardEl.classList.add("is-sending"); };
    cardEl.querySelectorAll("form").forEach(function (f) {
      f.addEventListener("submit", function (e) { setTimeout(function () { if (!e.defaultPrevented) sendOff(); }, 0); });
    });
    var google = cardEl.querySelector(".btn-google");
    if (google) google.addEventListener("click", sendOff);
    window.addEventListener("pageshow", function (e) {                 // back button: the card calms down again
      if (e.persisted) cardEl.classList.remove("is-sending");
    });
  }

  // ---------- Caps Lock warning on the password field (the most common reason for a wrong password) ----------
  var pw = document.getElementById("p"), caps = document.getElementById("caps-warn");
  if (pw && caps) {
    var capsCheck = function (e) { if (e.getModifierState) caps.hidden = !e.getModifierState("CapsLock"); };
    pw.addEventListener("keydown", capsCheck); pw.addEventListener("keyup", capsCheck);
    pw.addEventListener("blur", function () { caps.hidden = true; });
  }
})();

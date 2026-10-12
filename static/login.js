/* ClearPay sign-in page: the interactive invoice showcase, the card's motion, and signing in without a reload
   (a correct password flips the card to a welcome, then the card becomes the dashboard header).
   With reduced motion the showcase shows one finished check and nothing moves on its own. */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------- showcase: an invoice being checked. Four real ClearPay outcomes play on a loop; visitors can pick one,
  // ---------- and drag the invoice (or press "See the PO") to flip it over to its purchase order. ----------
  // Figures add up: line totals make the subtotal, and the total is the subtotal plus 18% GST.
  var CLEAN_LINES = [["A4 paper, reams", 200, 250], ["Toner cartridges", 10, 2100], ["Desk organisers", 40, 375], ["Whiteboard markers", 175, 80]];
  var SCENES = [
    { inv: "INV-0423", vendor: "Alpha Office Supplies", po: "PO-2041", total: "₹1,18,000.00", poTotal: "₹1,18,000.00", bank: "•••• 4521",
      badRow: -1, badBank: false, dup: false, stamp: "Matched to PO", lines: CLEAN_LINES, foot: "Every line matches the invoice",
      say: "Clean invoice: every check passes and it goes for approval.",
      log: [["Read invoice", "0.4s"], ["Vendor on file", "0.7s"], ["Maths checks out", "0.9s"],
            ["4 lines matched to PO-2041", "1.6s"], ["Bank account unchanged", "2.0s"], ["Sent for approval", "2.7s"]] },
    { inv: "INV-7781", vendor: "Northwind Traders", po: "PO-1187", total: "₹2,36,000.00", poTotal: "₹2,36,000.00", bank: "•••• 9907",
      badRow: -1, badBank: true, dup: false, stamp: "Flagged · bank changed", foot: "Every line matches; the bank account doesn't",
      lines: [["Steel brackets", 50, 1800], ["Hinge assemblies", 20, 3500], ["Fastener kits", 100, 250], ["Sealant tubes", 30, 500]],
      say: "Bank account changed: the invoice matches its PO, but the payee account differs from the vendor record, so ClearPay holds it for AP review.",
      log: [["Read invoice", "0.4s"], ["Vendor on file", "0.6s"], ["Maths checks out", "0.9s"],
            ["4 lines matched to PO-1187", "1.6s"], ["Bank account differs from record", "2.0s", 1], ["Held for AP review", "2.7s", 1]] },
    { inv: "INV-3310", vendor: "Kaveri Packaging", po: "PO-2276", total: "₹68,440.00", poTotal: "₹64,900.00", bank: "•••• 1186",
      badRow: 2, badBank: false, dup: false, stamp: "Flagged · price above PO", foot: "Line 3 is billed at ₹310; the PO says ₹250",
      lines: [["Corrugated boxes", 1000, 30], ["Bubble wrap rolls", 40, 200], ["Pallet wrap", 50, 250], ["Packing tape", 100, 45]],
      say: "Price above PO: line 3 is billed at 310 rupees against 250 on the purchase order, so ClearPay flags it and drafts an email to the vendor.",
      log: [["Read invoice", "0.3s"], ["Vendor on file · MSME", "0.6s"], ["Maths checks out", "0.8s"],
            ["Line 3: ₹310 vs PO ₹250", "1.3s", 1], ["Bank account unchanged", "2.0s"], ["Vendor email drafted", "2.7s", 1]] },
    { inv: "INV-0423", vendor: "Alpha Office Supplies", po: "PO-2041", total: "₹1,18,000.00", poTotal: "₹1,18,000.00", bank: "•••• 4521",
      badRow: -1, badBank: false, dup: true, stamp: "Rejected · duplicate", lines: CLEAN_LINES, foot: "This PO was already billed by INV-0423",
      say: "Duplicate: the same invoice number and amount came in before, so ClearPay rejects it and nothing is paid twice.",
      log: [["Read invoice", "0.4s"], ["Vendor on file", "0.6s"], ["Same number and amount seen before", "1.0s", 1],
            ["Already billed on 3 Oct", "1.5s", 1], ["Not sent for payment", "2.2s", 1]] }
  ];
  var scene = document.getElementById("ls-scene");
  if (scene) {                                      // hidden on narrow screens, where it costs nothing
    var $ = function (id) { return document.getElementById(id); };
    var rows = scene.querySelectorAll(".ls-rows li"), bank = scene.querySelector(".ls-bank"), log = $("ls-log"), stamp = $("ls-stamp");
    var head = scene.querySelector(".ls-content .ls-doc-head"), doc = scene.querySelector(".ls-doc-main");
    var chips = document.querySelectorAll(".ls-chip[data-scene]"), flipBtn = $("ls-flip-btn"), live = $("ls-live");
    var i = 0, heldUntil = 0;
    var rupees = function (n) { return "₹" + n.toLocaleString("en-IN"); };
    var show = function (n) {
      var s = SCENES[n]; i = n;
      $("ls-inv").textContent = s.inv; $("ls-vendor").textContent = s.vendor; $("ls-po").textContent = s.po;
      $("ls-total").textContent = s.total; $("ls-bank").textContent = s.bank;
      rows.forEach(function (li, k) { li.classList.toggle("bad", k === s.badRow); li.querySelector(".ls-tick").classList.toggle("bad", k === s.badRow); });
      bank.classList.toggle("bad", s.badBank); bank.querySelector(".ls-tick").classList.toggle("bad", s.badBank);
      head.classList.toggle("bad", s.dup);
      var bad = s.badRow >= 0 || s.badBank || s.dup;
      stamp.textContent = s.stamp; stamp.classList.toggle("bad", bad);
      log.replaceChildren();
      s.log.forEach(function (step, k) {
        var li = document.createElement("li"), dot = document.createElement("i"), txt = document.createElement("span"), t = document.createElement("small");
        txt.textContent = step[0]; t.textContent = step[1];
        if (step[2]) li.className = "bad";
        li.style.animationDelay = (0.3 + k * 0.42) + "s";
        li.append(dot, txt, t); log.appendChild(li);
      });
      // the back of the card: the purchase order, line by line, each one tied to its invoice line
      $("ls-po-no").textContent = s.po; $("ls-po-total").textContent = s.poTotal; $("ls-po-vendor").textContent = s.vendor;
      var pl = $("ls-po-lines"); pl.replaceChildren();
      s.lines.forEach(function (ln, k) {
        var li = document.createElement("li"), d = document.createElement("span"), q = document.createElement("span"), t = document.createElement("i");
        d.textContent = ln[0]; q.textContent = ln[1].toLocaleString("en-IN") + " × " + rupees(ln[2]);
        t.className = "ls-tick"; if (k === s.badRow) { li.className = "bad"; t.classList.add("bad"); }
        li.style.setProperty("--k", k);
        li.append(d, q, t); pl.appendChild(li);
      });
      var foot = $("ls-po-foot"); foot.textContent = s.foot; foot.classList.toggle("bad", bad);
      chips.forEach(function (c) { c.setAttribute("aria-pressed", String(Number(c.dataset.scene) === n)); });
      scene.classList.remove("run", "out");
      void scene.offsetWidth;                       // restart the CSS animations
      scene.classList.add("run");
    };
    var go = function (n) {
      scene.classList.add("out");
      setTimeout(function () { show(n); }, reduce ? 0 : 380);
    };
    show(0);
    chips.forEach(function (c) {
      c.addEventListener("click", function () {
        heldUntil = Date.now() + 30000;             // a visitor is exploring: stop the loop for a while
        var n = Number(c.dataset.scene);
        go(n);
        if (live) live.textContent = SCENES[n].say;
      });
    });
    if (!reduce) {
      setInterval(function () {
        if (Date.now() < heldUntil || flipped || document.hidden) return;
        go((i + 1) % SCENES.length);
      }, 6200);
    }

    // ---- flip the invoice over to its purchase order: drag it, or press "See the PO" ----
    var angle = 0, flipped = false, drag = null;
    var setAngle = function (a, animate) {
      angle = a;
      doc.classList.toggle("is-dragging", !animate);
      doc.style.setProperty("--flip", a.toFixed(1) + "deg");
      var m = ((a % 360) + 360) % 360, back = m > 90 && m < 270;
      if (back !== doc.classList.contains("show-po")) doc.classList.toggle("show-po", back);
    };
    var settle = function (toBack) {
      flipped = toBack;
      setAngle(toBack ? Math.round((angle - 180) / 360) * 360 + 180 : Math.round(angle / 360) * 360, true);   // nearest resting side
      if (flipBtn) {
        flipBtn.setAttribute("aria-pressed", String(toBack));
        flipBtn.lastChild.textContent = toBack ? "See the invoice" : "See the PO";
      }
      if (live) live.textContent = toBack ? "Showing purchase order " + SCENES[i].po + ": " + SCENES[i].foot + "." : "Showing the invoice.";
    };
    if (flipBtn) flipBtn.addEventListener("click", function () { heldUntil = Date.now() + 30000; settle(!flipped); });
    if (!reduce) {
      doc.addEventListener("pointerdown", function (e) {
        if (e.button !== 0) return;
        drag = { x: e.clientX, start: angle, moved: false };
        doc.setPointerCapture(e.pointerId);
      });
      doc.addEventListener("pointermove", function (e) {
        if (!drag) return;
        var dx = e.clientX - drag.x;
        if (Math.abs(dx) > 4) drag.moved = true;
        if (drag.moved) setAngle(drag.start + dx * 0.6, false);
      });
      var end = function () {
        if (!drag) return;
        var moved = drag.moved; drag = null;
        heldUntil = Date.now() + 30000;
        if (!moved) { settle(!flipped); return; }   // a plain click flips too
        var m = ((angle % 360) + 360) % 360;
        settle(m > 90 && m < 270);
      };
      doc.addEventListener("pointerup", end);
      doc.addEventListener("pointercancel", end);
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

  // ---------- sign in without a reload ----------
  // Wrong password: the card shakes and says why, nothing else is cleared. Right password: the lock opens, the card
  // flips to "Welcome back, <name>", and then (Chrome, Edge) the card itself becomes the dashboard's header.
  // Anything unexpected falls back to the classic full-page form post, so signing in can never get stuck here.
  var signForm = document.getElementById("login-form") || document.getElementById("mfa-form");
  var signCard = document.querySelector(".login-card"), welcomeEl = document.getElementById("login-welcome");
  if (signForm && signCard && window.fetch && window.FormData) {
    var iconHref = (document.body.dataset.icons || "") + "#";
    var svgIcon = function (name) {
      var ns = "http://www.w3.org/2000/svg", s = document.createElementNS(ns, "svg"), u = document.createElementNS(ns, "use");
      s.setAttribute("class", "ic ic-sm"); s.setAttribute("aria-hidden", "true"); u.setAttribute("href", iconHref + name);
      s.appendChild(u); return s;
    };
    var text = function (t) { var s = document.createElement("span"); s.textContent = t; return s; };
    var msgSlot = signCard.querySelector(".login-msg");
    var fail = function (msg, btn, original) {
      delete signForm.dataset.busy;
      signCard.classList.remove("is-sending");
      if (btn) {
        btn.disabled = false; btn.classList.remove("is-busy"); btn.removeAttribute("aria-busy");
        btn.replaceChildren.apply(btn, original);
      }
      var box = document.createElement("div");
      box.className = "login-alert bad"; box.setAttribute("role", "alert");
      box.append(svgIcon("circle-alert"), text(msg || "That didn't work. Try again."));
      if (msgSlot) msgSlot.replaceChildren(box);
      signCard.classList.remove("shake"); void signCard.offsetWidth; signCard.classList.add("shake");
      var secret = signForm.querySelector("#p, #code");
      if (secret) { secret.value = ""; secret.focus(); }
    };
    var welcome = function (res, btn) {
      var go = function () {
        signCard.style.viewTransitionName = "cp-hero";            // morphs into the dashboard's greeting header
        location.href = res.next;
      };
      if (reduce || !welcomeEl) { go(); return; }
      if (btn) {                                                   // 1. the lock opens
        btn.classList.remove("is-busy"); btn.classList.add("is-done");
        btn.replaceChildren(svgIcon("lock-open"), text("Signed in"));
      }
      var av = document.getElementById("lw-avatar");
      av.textContent = res.initials || ""; av.style.setProperty("--av", res.color || "var(--accent)");
      document.getElementById("lw-title").textContent = "Welcome back, " + (res.first || "");
      setTimeout(function () { signCard.classList.add("flip-out"); }, 480);                 // 2. the card turns...
      setTimeout(function () {                                                            // ...to the welcome side
        signCard.classList.remove("flip-out"); welcomeEl.hidden = false;
        signCard.classList.add("is-welcome", "flip-in");
      }, 480 + 240);
      setTimeout(go, 480 + 240 + 1250);                                                     // 3. off to the dashboard
    };
    signForm.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;                              // app.js stopped a double submit
      var btn = e.submitter || signForm.querySelector("[type=submit]");
      var original = btn ? Array.prototype.map.call(btn.childNodes, function (n) { return n.cloneNode(true); }) : [];
      e.preventDefault();
      var stale = msgSlot && msgSlot.querySelector(".login-alert.bad");   // a retry: the old error steps aside
      if (stale) { var p = document.createElement("p"); p.textContent = "Checking…"; msgSlot.replaceChildren(p); }
      fetch(signForm.getAttribute("action") || (location.pathname + location.search), {
        method: "POST", body: new FormData(signForm), credentials: "same-origin",
        headers: { "X-ClearPay-Async": "1", "Accept": "application/json" }
      }).then(function (r) {
        if (!r.ok || (r.headers.get("Content-Type") || "").indexOf("json") < 0) throw new Error("not json");
        return r.json();
      }).then(function (res) {
        if (res.ok && res.name !== undefined) welcome(res, btn);
        else if (res.ok || res.restart) location.href = res.next;  // two-step code next, or start over (e.g. locked)
        else fail(res.error, btn, original);
      }).catch(function () { delete signForm.dataset.busy; signForm.submit(); });
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

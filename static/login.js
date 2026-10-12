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

  // ---------- live verification network behind the page ----------
  // Drifting nodes link up when close; pulses hop node to node like checks running; the pointer gathers the
  // nearby nodes; a click sends a shockwave; pressing Sign in streams the whole field into the card.
  var cv = document.getElementById("ls-net");
  var net = null;
  if (cv && cv.getContext && !reduce) {
    net = (function () {
      var ctx = cv.getContext("2d"), W = 0, H = 0, dpr = 1, nodes = [], pulses = [], rings = [];
      var mouse = { x: -9999, y: -9999, on: false }, surge = null, LINK = 132, running = true, lastPulse = 0;
      var size = function () {
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        W = cv.clientWidth; H = cv.clientHeight;
        cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        var want = Math.max(36, Math.min(120, Math.round(W * H / 15000)));
        while (nodes.length < want) nodes.push({ x: Math.random() * W, y: Math.random() * H, vx: (Math.random() - .5) * .3, vy: (Math.random() - .5) * .3,
                                                 r: 1 + Math.random() * 1.1, glow: 0 });
        nodes.length = want;
      };
      var near = function (a) {
        var best = [];
        nodes.forEach(function (b) { if (b !== a && Math.hypot(a.x - b.x, a.y - b.y) < LINK) best.push(b); });
        return best.length ? best[(Math.random() * best.length) | 0] : null;
      };
      var spawn = function (from, hops) {
        var to = near(from);
        if (to) pulses.push({ a: from, b: to, t: 0, hops: hops, sp: .018 + Math.random() * .012 });
      };
      var tick = function (now) {
        if (!running) return;
        ctx.clearRect(0, 0, W, H);
        // move
        nodes.forEach(function (n) {
          if (surge) {
            var dx = surge.x - n.x, dy = surge.y - n.y, d = Math.hypot(dx, dy) || 1;
            n.vx += dx / d * .55; n.vy += dy / d * .55; n.vx *= .93; n.vy *= .93; n.glow = Math.max(n.glow, .5);
            if (d < 30) { n.x = Math.random() < .5 ? -10 : W + 10; n.y = Math.random() * H; n.vx = n.vy = 0; }
          } else {
            if (mouse.on) {
              var mx = mouse.x - n.x, my = mouse.y - n.y, md = Math.hypot(mx, my);
              if (md < 200 && md > 1) { n.vx += mx / md * .012; n.vy += my / md * .012; n.glow = Math.max(n.glow, 1 - md / 200); }
            }
            n.vx *= .992; n.vy *= .992;
            var sp = Math.hypot(n.vx, n.vy);
            if (sp < .06) { n.vx += (Math.random() - .5) * .04; n.vy += (Math.random() - .5) * .04; }
            if (sp > 1.4) { n.vx *= .9; n.vy *= .9; }
          }
          n.x += n.vx; n.y += n.vy;
          if (n.x < -20) n.x = W + 20; else if (n.x > W + 20) n.x = -20;
          if (n.y < -20) n.y = H + 20; else if (n.y > H + 20) n.y = -20;
          n.glow *= .965;
        });
        // shockwaves push nodes outward as the ring passes
        rings = rings.filter(function (g) {
          var age = Math.max(0, (now - g.t0) / 1300);          // the frame time can be a hair before the click
          if (age >= 1) return false;
          var R = (1 - Math.pow(1 - age, 3)) * g.max;
          nodes.forEach(function (n) {
            var dx = n.x - g.x, dy = n.y - g.y, d = Math.hypot(dx, dy) || 1;
            if (Math.abs(d - R) < 26) { n.vx += dx / d * 1.1 * (1 - age); n.vy += dy / d * 1.1 * (1 - age); n.glow = 1; }
          });
          ctx.beginPath(); ctx.arc(g.x, g.y, R, 0, Math.PI * 2);
          ctx.strokeStyle = "rgba(" + g.rgb + "," + (0.5 * (1 - age)).toFixed(3) + ")"; ctx.lineWidth = 1.5 + 2 * (1 - age); ctx.stroke();
          return true;
        });
        // links between close nodes, and from nodes to the pointer
        ctx.lineWidth = 1;
        for (var i = 0; i < nodes.length; i++) {
          var a = nodes[i];
          for (var j = i + 1; j < nodes.length; j++) {
            var b = nodes[j], dx = a.x - b.x, dy = a.y - b.y;
            if (dx > LINK || dx < -LINK || dy > LINK || dy < -LINK) continue;
            var d = Math.sqrt(dx * dx + dy * dy);
            if (d >= LINK) continue;
            var al = (1 - d / LINK) * (.16 + .4 * Math.max(a.glow, b.glow));
            ctx.strokeStyle = "rgba(122,162,255," + al.toFixed(3) + ")";
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
          if (mouse.on && !surge) {
            var cd = Math.hypot(mouse.x - a.x, mouse.y - a.y);
            if (cd < 170) {
              ctx.strokeStyle = "rgba(94,224,200," + ((1 - cd / 170) * .35).toFixed(3) + ")";
              ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(mouse.x, mouse.y); ctx.stroke();
            }
          }
        }
        // nodes
        nodes.forEach(function (n) {
          ctx.fillStyle = n.glow > .05 ? "rgba(94,224,200," + (.45 + .55 * n.glow).toFixed(3) + ")" : "rgba(169,194,255,.62)";
          ctx.beginPath(); ctx.arc(n.x, n.y, n.r + n.glow * 1.6, 0, Math.PI * 2); ctx.fill();
        });
        // pulses: a bright packet with a short tail, chaining a few hops
        if (now - lastPulse > (surge ? 60 : 520) && nodes.length) {
          lastPulse = now; spawn(nodes[(Math.random() * nodes.length) | 0], surge ? 1 : 2 + ((Math.random() * 3) | 0));
        }
        pulses = pulses.filter(function (p) {
          p.t += p.sp * (surge ? 2.2 : 1);
          var t = Math.min(p.t, 1), x = p.a.x + (p.b.x - p.a.x) * t, y = p.a.y + (p.b.y - p.a.y) * t;
          var t0 = Math.max(0, t - .22), x0 = p.a.x + (p.b.x - p.a.x) * t0, y0 = p.a.y + (p.b.y - p.a.y) * t0;
          var gr = ctx.createLinearGradient(x0, y0, x, y);
          gr.addColorStop(0, "rgba(94,224,200,0)"); gr.addColorStop(1, "rgba(94,224,200,.9)");
          ctx.strokeStyle = gr; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x, y); ctx.stroke(); ctx.lineWidth = 1;
          ctx.fillStyle = "rgba(220,255,248,.95)"; ctx.shadowColor = "#5EE0C8"; ctx.shadowBlur = 12;
          ctx.beginPath(); ctx.arc(x, y, 2, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;
          if (p.t >= 1) { p.b.glow = 1; if (p.hops > 1) spawn(p.b, p.hops - 1); return false; }
          return true;
        });
        requestAnimationFrame(tick);
      };
      size();
      window.addEventListener("resize", size);
      document.addEventListener("visibilitychange", function () {
        var was = running; running = !document.hidden;
        if (running && !was) requestAnimationFrame(tick);
      });
      window.addEventListener("pointermove", function (e) { mouse.x = e.clientX; mouse.y = e.clientY; mouse.on = true; }, { passive: true });
      document.documentElement.addEventListener("pointerleave", function () { mouse.on = false; });
      requestAnimationFrame(tick);
      return {
        shock: function (x, y, rgb) { rings.push({ x: x, y: y, t0: performance.now(), rgb: rgb || "122,162,255", max: Math.max(W, H) * .7 }); },
        surge: function (x, y) { surge = { x: x, y: y }; },
        calm: function () { surge = null; }
      };
    })();
    // a click on the background sends a ripple through the network
    window.addEventListener("pointerdown", function (e) {
      if (e.target.closest && e.target.closest(".login-card, a, button, input, label")) return;
      net.shock(e.clientX, e.clientY);
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

  // ---------- sign-in card: magnetic button, ripples, and the send-off ----------
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
    var center = function () { var r = cardEl.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
    var sendOff = function () {
      cardEl.classList.add("is-sending");
      if (net) { var c = center(); net.surge(c[0], c[1]); net.shock(c[0], c[1], "94,224,200"); }
    };
    cardEl.querySelectorAll("form").forEach(function (f) {
      f.addEventListener("submit", function (e) { setTimeout(function () { if (!e.defaultPrevented) sendOff(); }, 0); });
    });
    var google = cardEl.querySelector(".btn-google");
    if (google) google.addEventListener("click", sendOff);
    window.addEventListener("pageshow", function (e) {                 // back button: the card and field calm down again
      if (e.persisted) { cardEl.classList.remove("is-sending"); if (net) net.calm(); }
    });
    // a wrong password lands with a red wave from the card; a success notice with a green one
    if (net && (cardEl.classList.contains("has-error") || cardEl.querySelector(".login-alert.ok"))) {
      var bad = cardEl.classList.contains("has-error");
      setTimeout(function () { var c = center(); net.shock(c[0], c[1], bad ? "240,106,95" : "63,191,133"); }, bad ? 700 : 300);
    }
  }
})();

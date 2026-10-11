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
})();

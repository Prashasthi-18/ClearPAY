/* ClearPay quick search (Ctrl+K / Cmd+K, or "/"): jump to any page in your menu, open an invoice by number,
   vendor or PO, search the invoice list, or ask ClearPay a question. Keyboard first: arrows, Enter, Esc. */
(function () {
  "use strict";
  var body = document.body, btn = document.getElementById("cmdk-btn");
  if (!btn || !body.dataset.paletteUrl) return;
  var mac = /Mac|iPhone|iPad/.test(navigator.platform || "");
  var kbd = btn.querySelector("kbd"); if (kbd && mac) kbd.textContent = "⌘K";

  // a question typed into the palette arrives on the Ask page as ?q=...: ask it straight away
  var askForm = document.getElementById("ask-form"), askQ = document.getElementById("ask-q");
  var preset = new URLSearchParams(location.search).get("q");
  if (askForm && askQ && preset) { askQ.value = preset.slice(0, 500); setTimeout(function () { askForm.requestSubmit(); }, 250); }

  var icons = body.dataset.icons || "";
  var svg = function (name) {
    return '<svg class="ic ic-sm" aria-hidden="true"><use href="' + icons + "#" + name + '"></use></svg>';
  };
  var esc = function (s) { return String(s).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); };

  // pages: exactly the links in this person's sidebar, so roles are respected for free
  var pages = Array.prototype.map.call(document.querySelectorAll(".side a.nav-link-x"), function (a) {
    var use = a.querySelector("use"), ic = use ? (use.getAttribute("href") || "").split("#")[1] : "file-text";
    return { kind: "page", label: a.textContent.replace(/\s+\d+\s*$/, "").trim(), href: a.href, icon: ic || "file-text" };
  });
  pages.push({ kind: "page", label: "My account", href: (document.querySelector('a[href$="/account"]') || {}).href || "/account", icon: "user" });
  var actions = [
    { kind: "action", label: "Switch to dark theme", icon: "moon", run: function () { window.ClearPayTheme && ClearPayTheme.set("dark"); } },
    { kind: "action", label: "Switch to light theme", icon: "sun", run: function () { window.ClearPayTheme && ClearPayTheme.set("light"); } }
  ];
  var invoices = null, loading = false;
  var load = function () {
    if (invoices || loading) return;
    loading = true;
    fetch(body.dataset.paletteUrl, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (rows) { invoices = rows; loading = false; if (open) render(); })
      .catch(function () { invoices = []; loading = false; });
  };

  // ---------- the dialog ----------
  var wrap = document.createElement("div");
  wrap.className = "cmdk"; wrap.hidden = true;
  wrap.innerHTML =
    '<div class="cmdk-scrim" data-close></div>' +
    '<div class="cmdk-box" role="dialog" aria-modal="true" aria-label="Quick search">' +
      '<div class="cmdk-in">' + svg("search") +
        '<input id="cmdk-q" type="text" autocomplete="off" spellcheck="false" placeholder="Invoice number, vendor, PO, page, or a question…" ' +
        'role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-autocomplete="list">' +
        '<kbd>Esc</kbd></div>' +
      '<div class="cmdk-list" id="cmdk-list" role="listbox"></div>' +
      '<div class="cmdk-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span><kbd>Esc</kbd> close</span></div>' +
    "</div>";
  body.appendChild(wrap);
  var input = wrap.querySelector("#cmdk-q"), list = wrap.querySelector("#cmdk-list");
  var open = false, items = [], active = 0, lastFocus = null;

  var score = function (text, q) {
    text = text.toLowerCase();
    if (!q) return 1;
    if (text.indexOf(q) === 0) return 3;
    if (text.indexOf(q) > 0) return 2;
    var i = 0;                                         // letters in order ("alp30" finds "ALP-3001")
    for (var k = 0; k < text.length && i < q.length; k++) if (text[k] === q[i]) i++;
    return i === q.length ? 1 : 0;
  };
  var build = function (q) {
    var out = [], ql = q.trim().toLowerCase();
    if (invoices) {
      invoices.map(function (r) {
        return { r: r, s: Math.max(score(r.n, ql) * 2, score(r.v, ql), score(r.po, ql), score(r.amt.replace(/[₹,]/g, ""), ql.replace(/[₹,]/g, ""))) };
      }).filter(function (x) { return x.s > 0; })
        .sort(function (a, b) { return b.s - a.s; }).slice(0, ql ? 7 : 5)
        .forEach(function (x) {
          out.push({ kind: "invoice", group: ql ? "Invoices" : "Recent invoices", label: x.r.n, sub: [x.r.v, x.r.po].filter(Boolean).join(" · "),
                     right: x.r.amt, status: x.r.st, sc: x.r.s, href: body.dataset.homeUrl.replace(/\/?$/, "/") + "invoice/" + x.r.id, icon: "receipt" });
        });
    } else if (loading) out.push({ kind: "note", group: "Invoices", label: "Loading invoices…", icon: "loader-circle" });
    pages.filter(function (p) { return score(p.label, ql) > 0; }).slice(0, ql ? 5 : 8)
      .forEach(function (p) { out.push({ kind: "page", group: "Go to", label: p.label, href: p.href, icon: p.icon }); });
    actions.filter(function (a) { return ql && score(a.label, ql) > 0; })
      .forEach(function (a) { out.push({ kind: "action", group: "Actions", label: a.label, run: a.run, icon: a.icon }); });
    if (ql) {
      out.push({ kind: "search", group: "Search", label: "Search all invoices for “" + q.trim() + "”", icon: "search",
                 href: body.dataset.homeUrl + "?q=" + encodeURIComponent(q.trim()) + "#invoices" });
      if (body.dataset.askUrl) out.push({ kind: "ask", group: "Search", label: "Ask ClearPay: “" + q.trim() + "”", icon: "sparkles",
                 href: body.dataset.askUrl + "?q=" + encodeURIComponent(q.trim()) });
    }
    return out;
  };
  var render = function () {
    items = build(input.value);
    if (active >= items.length) active = Math.max(0, items.length - 1);
    var html = "", group = "";
    items.forEach(function (it, i) {
      if (it.group !== group) { group = it.group; html += '<div class="cmdk-group">' + esc(group) + "</div>"; }
      html += '<div class="cmdk-item' + (i === active ? " on" : "") + (it.kind === "note" ? " note" : "") + '" role="option" id="cmdk-' + i +
        '" aria-selected="' + (i === active) + '" data-i="' + i + '" style="--k:' + i + '">' +
        '<span class="cmdk-ic">' + svg(it.icon) + "</span>" +
        '<span class="cmdk-main"><b>' + esc(it.label) + "</b>" + (it.sub ? "<small>" + esc(it.sub) + "</small>" : "") + "</span>" +
        (it.status ? '<span class="badge-s b-' + esc((it.sc || "neutral").toLowerCase().replace(/ /g, "_")) + '">' + esc(it.status) + "</span>" : "") +
        (it.right ? '<span class="cmdk-amt">' + esc(it.right) + "</span>" : "") +
        (it.kind !== "note" ? '<span class="cmdk-go">' + svg("arrow-left") + "</span>" : "") + "</div>";
    });
    list.innerHTML = html || '<div class="cmdk-empty">Nothing matches. Press Enter to search all invoices.</div>';
    input.setAttribute("aria-activedescendant", items.length ? "cmdk-" + active : "");
    var on = list.querySelector(".cmdk-item.on"); if (on) on.scrollIntoView({ block: "nearest" });
  };
  var choose = function (i) {
    var it = items[i]; if (!it || it.kind === "note") return;
    if (it.run) { it.run(); close(); return; }
    close(true);
    var bar = document.querySelector(".nav-progress"); if (bar) bar.classList.add("run");
    location.href = it.href;
  };
  var show = function () {
    if (open) return;
    open = true; lastFocus = document.activeElement;
    wrap.hidden = false; requestAnimationFrame(function () { wrap.classList.add("in"); });
    input.value = ""; active = 0; load(); render();
    setTimeout(function () { input.focus(); }, 20);
  };
  var close = function (leaving) {
    if (!open) return;
    open = false; wrap.classList.remove("in");
    setTimeout(function () { if (!open) wrap.hidden = true; }, 180);
    if (!leaving && lastFocus && lastFocus.focus) lastFocus.focus();
  };
  btn.addEventListener("click", show);
  wrap.addEventListener("click", function (e) {
    if (e.target.closest("[data-close]")) { close(); return; }
    var row = e.target.closest(".cmdk-item"); if (row) choose(Number(row.dataset.i));
  });
  wrap.addEventListener("mousemove", function (e) {
    var row = e.target.closest(".cmdk-item");
    if (row && Number(row.dataset.i) !== active) { active = Number(row.dataset.i); list.querySelectorAll(".cmdk-item").forEach(function (r) {
      var on = Number(r.dataset.i) === active; r.classList.toggle("on", on); r.setAttribute("aria-selected", on); }); }
  });
  input.addEventListener("input", function () { active = 0; render(); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); if (!items.length) return;
      do { active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length; } while (items[active].kind === "note" && items.length > 1);
      render();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (items.length) choose(active);
      else if (input.value.trim()) location.href = body.dataset.homeUrl + "?q=" + encodeURIComponent(input.value.trim()) + "#invoices";
    } else if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Tab") e.preventDefault();      // focus stays in the dialog
  });
  document.addEventListener("keydown", function (e) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || "")) || e.target.isContentEditable;
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); open ? close() : show(); }
    else if (e.key === "/" && !typing && !open) { e.preventDefault(); show(); }
  });
})();

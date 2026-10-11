// ClearPay front-end. No inline scripts (the CSP blocks them) and no innerHTML with server text:
// everything from the server is set with textContent, so invoice text can't inject markup.
(function () {
  "use strict";
  var body = document.body;
  var ICONS = body.dataset.icons || "/static/icons.svg";
  var CSRF = body.dataset.csrf || "";
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function icon(name, cls) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "ic " + (cls || ""));
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS(ns, "use");
    use.setAttribute("href", ICONS + "#" + name);
    svg.appendChild(use);
    return svg;
  }
  function post(url, data) {
    var fd = new FormData();
    Object.keys(data).forEach(function (k) { fd.append(k, data[k]); });
    return fetch(url, { method: "POST", body: fd, credentials: "same-origin",
                        headers: { "X-CSRF-Token": CSRF, "Accept": "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
  }

  // ---------- toasts (bottom-right) ----------
  var region = document.getElementById("toasts");
  var TOAST_ICONS = { approved: "circle-check", rejected: "circle-x", needs_approval: "clock", attention: "triangle-alert",
                      failed: "circle-alert", security: "shield-alert", ok: "circle-check", error: "circle-alert",
                      mail: "mail-check", info: "info" };
  function toast(o) {
    if (!region) return;
    var kind = o.kind || "info";
    var t = el("div", "toast-x t-" + kind);
    t.setAttribute("role", kind === "error" || kind === "failed" ? "alert" : "status");
    var ic = el("span", "t-ic"); ic.appendChild(icon(TOAST_ICONS[kind] || "bell", "ic-sm" + (kind === "mail" ? " pop-in" : "")));
    var b = el("div", "t-body");
    b.appendChild(el("div", "t-title", o.title));
    if (o.text) b.appendChild(el("div", "t-text", o.text));
    if (o.meta) b.appendChild(el("div", "t-meta", o.meta));
    if (o.href) { var a = el("a", "t-act", o.action || "View invoice"); a.href = o.href; b.appendChild(a); }
    var x = el("button", "t-close"); x.type = "button"; x.setAttribute("aria-label", "Dismiss"); x.appendChild(icon("x", "ic-sm"));
    t.appendChild(ic); t.appendChild(b); t.appendChild(x);
    function close() {
      if (t.dataset.closing) return; t.dataset.closing = "1";
      t.classList.add("out"); setTimeout(function () { t.remove(); }, reduceMotion ? 0 : 220);
    }
    x.addEventListener("click", close);
    region.appendChild(t);
    var ms = o.timeout === undefined ? (kind === "error" || kind === "failed" ? 0 : 7000) : o.timeout;
    if (ms) {
      var timer = setTimeout(close, ms);
      t.addEventListener("mouseenter", function () { clearTimeout(timer); });
      t.addEventListener("mouseleave", function () { timer = setTimeout(close, 2500); });
    }
    while (region.children.length > 4) region.firstChild.remove();
    return t;
  }
  window.ClearPayToast = toast;

  // server flash messages become toasts
  document.querySelectorAll(".flash-src").forEach(function (f) {
    var kind = f.dataset.toastKind || "ok", text = f.dataset.toastText || f.textContent.trim();
    if (kind === "mail") toast({ kind: "mail", title: "Email sent", text: text });
    else if (kind === "error") toast({ kind: "error", title: "That didn't work", text: text });
    else toast({ kind: "ok", title: text, timeout: 5000 });
  });

  // ---------- popovers: notifications + account menu ----------
  function closePops(except) {
    document.querySelectorAll("[data-pop]").forEach(function (p) {
      if (p === except) return;
      var btn = p.querySelector("[aria-expanded]"), menu = p.querySelector(".menu");
      if (btn && btn.getAttribute("aria-expanded") === "true") { btn.setAttribute("aria-expanded", "false"); menu.classList.remove("open"); }
    });
  }
  document.querySelectorAll("[data-pop]").forEach(function (p) {
    var btn = p.querySelector("[aria-expanded]"), menu = p.querySelector(".menu");
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = btn.getAttribute("aria-expanded") !== "true";
      closePops(p);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      menu.classList.toggle("open", open);
      if (open && menu.id === "np") loadNotes(0);
      if (open) { var first = menu.querySelector("a, button"); if (first) setTimeout(function () { first.focus(); }, 50); }
    });
    menu.addEventListener("click", function (e) { e.stopPropagation(); });
  });
  document.addEventListener("click", function () { closePops(null); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      var open = document.querySelector("[data-pop] [aria-expanded='true']");
      closePops(null); if (open) open.focus();
    }
  });

  // ---------- notifications ----------
  var bell = document.getElementById("bell"), count = document.getElementById("bell-count"), bellSr = document.getElementById("bell-sr");
  var list = document.getElementById("np-list");
  var lastId = parseInt(body.dataset.lastNote || "0", 10) || 0;
  var notesUrl = body.dataset.notesUrl, readUrl = body.dataset.notesReadUrl;
  function setCount(n) {
    if (!count) return;
    var before = parseInt(count.textContent || "0", 10) || 0;
    count.textContent = n; if (bellSr) bellSr.textContent = n;
    if (n > 0) count.removeAttribute("hidden"); else count.setAttribute("hidden", "");
    if (n > before && bell && !reduceMotion) { bell.classList.remove("ring"); void bell.offsetWidth; bell.classList.add("ring"); }
  }
  function noteItem(n) {
    var a = el("a", "note-item k-" + n.kind + (n.read ? " read" : " unread")); a.href = n.url;
    var ic = el("span", "ni-ic"); ic.appendChild(icon(n.icon, "ic-sm"));
    var b = el("span", "ni-body");
    b.appendChild(el("span", "ni-title d-block", n.title));
    b.appendChild(el("span", "ni-text d-block", n.body));
    b.appendChild(el("span", "ni-time d-block", n.time));
    a.appendChild(ic); a.appendChild(b);
    return a;
  }
  function renderNotes(items) {
    if (!list) return;
    list.replaceChildren();
    if (!items.length) {
      var e = el("div", "empty"); e.appendChild(icon("bell")); e.appendChild(el("span", "", "You're all caught up."));
      list.appendChild(e); return;
    }
    items.forEach(function (n) { list.appendChild(noteItem(n)); });
  }
  function loadNotes(after) {
    if (!notesUrl) return Promise.resolve();
    return fetch(notesUrl + "?after=" + after, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        setCount(d.unread);
        renderNotes(d.recent);
        (d.new || []).forEach(function (n) {
          toast({ kind: n.kind, title: n.title, text: n.body, meta: n.hm, href: n.invoice_id ? n.url : null,
                  action: n.invoice_id ? "View invoice" : null });
        });
        if (d.last_id > lastId) lastId = d.last_id;
      }).catch(function () {});
  }
  if (notesUrl) {
    var poll = function () { if (document.visibilityState === "visible") loadNotes(lastId); };
    setInterval(poll, 15000);
    document.addEventListener("visibilitychange", poll);
  }
  var readAll = document.getElementById("np-readall");
  if (readAll) readAll.addEventListener("click", function () {
    post(readUrl, { id: "all" }).then(function (d) {
      setCount(d.unread);
      list.querySelectorAll(".note-item.unread").forEach(function (a) { a.classList.remove("unread"); a.classList.add("read"); });
    }).catch(function () { toast({ kind: "error", title: "Couldn't mark notifications as read", text: "Check your connection and try again." }); });
  });

  // ---------- confirm dialogs for destructive actions ----------
  function confirmDialog(title, text, okLabel) {
    return new Promise(function (resolve) {
      var back = el("div", "modal-x"), card = el("div", "modal-card");
      card.setAttribute("role", "alertdialog"); card.setAttribute("aria-modal", "true");
      var h = el("h3", "", title); h.id = "cp-modal-title"; card.setAttribute("aria-labelledby", h.id);
      card.appendChild(h);
      if (text) card.appendChild(el("p", "", text));
      var row = el("div", "actions");
      var cancel = el("button", "btn btn-outline", "Cancel"); cancel.type = "button";
      var ok = el("button", "btn btn-reject", okLabel || "Confirm"); ok.type = "button";
      row.appendChild(cancel); row.appendChild(ok); card.appendChild(row); back.appendChild(card);
      document.body.appendChild(back);
      var prev = document.activeElement;
      setTimeout(function () { cancel.focus(); }, 30);
      function done(v) {
        back.classList.add("out"); setTimeout(function () { back.remove(); if (prev) prev.focus(); }, reduceMotion ? 0 : 180);
        document.removeEventListener("keydown", key); resolve(v);
      }
      function key(e) {
        if (e.key === "Escape") done(false);
        if (e.key === "Tab") { e.preventDefault(); (document.activeElement === ok ? cancel : ok).focus(); }
      }
      document.addEventListener("keydown", key);
      cancel.addEventListener("click", function () { done(false); });
      ok.addEventListener("click", function () { done(true); });
      back.addEventListener("click", function (e) { if (e.target === back) done(false); });
    });
  }
  document.querySelectorAll("button[data-confirm]").forEach(function (b) {
    b.addEventListener("click", function (e) {
      if (b.dataset.confirmed) { delete b.dataset.confirmed; return; }
      e.preventDefault();
      confirmDialog(b.dataset.confirm, b.dataset.confirmText, b.dataset.confirmOk).then(function (yes) {
        if (!yes) return;
        b.dataset.confirmed = "1";
        if (b.form && b.form.requestSubmit) b.form.requestSubmit(b); else b.click();
      });
    });
  });
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (f.dataset.confirmed) return;
      e.preventDefault();
      confirmDialog(f.dataset.confirm, "", "Continue").then(function (yes) { if (yes) { f.dataset.confirmed = "1"; f.requestSubmit(); } });
    });
  });

  // ---------- busy state for submit buttons (sign in, send email, ...) ----------
  document.querySelectorAll("form").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;
      var b = e.submitter;
      if (!b || !b.dataset.busyText) return;
      if (f.dataset.busy) { e.preventDefault(); return; }       // no double submits
      f.dataset.busy = "1";
      if (b.name) { var h = el("input"); h.type = "hidden"; h.name = b.name; h.value = b.value; f.appendChild(h); }
      setTimeout(function () {
        b.classList.add("is-busy"); b.disabled = true; b.setAttribute("aria-busy", "true");
        b.replaceChildren(icon(b.dataset.busyIcon === "send" ? "send" : "loader-circle",
                               "ic-sm " + (b.dataset.busyIcon === "send" ? "mail-fly" : "spin")), el("span", "", b.dataset.busyText));
      }, 0);
    });
  });

  // ---------- sign-in page ----------
  var loginForm = document.getElementById("login-form");
  if (loginForm) {
    var u = document.getElementById("u"), remember = document.getElementById("remember");
    try {
      var saved = localStorage.getItem("cp-username");
      if (saved) { u.value = saved; remember.checked = true; u.removeAttribute("autofocus"); document.getElementById("p").focus(); }
    } catch (e) {}
    loginForm.addEventListener("submit", function () {
      try { if (remember.checked) localStorage.setItem("cp-username", u.value); else localStorage.removeItem("cp-username"); } catch (e) {}
    });
    var fb = document.getElementById("forgot-btn"), fp = document.getElementById("forgot");
    fb.addEventListener("click", function () {
      var open = fp.hasAttribute("hidden");
      if (open) fp.removeAttribute("hidden"); else fp.setAttribute("hidden", "");
      fb.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  document.querySelectorAll("[data-pw-toggle]").forEach(function (b) {
    var input = document.getElementById(b.dataset.pwToggle);
    b.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      b.setAttribute("aria-pressed", show ? "true" : "false");
      b.setAttribute("aria-label", show ? "Hide password" : "Show password");
      b.replaceChildren(icon(show ? "eye-off" : "eye"));
    });
  });

  // ---------- theme (Account > Appearance) ----------
  var themeForm = document.getElementById("theme-form");
  if (themeForm) {
    var status = document.getElementById("theme-status");
    themeForm.querySelectorAll("input[name=theme]").forEach(function (r) {
      r.addEventListener("change", function () {
        if (window.ClearPayTheme) window.ClearPayTheme.set(r.value);
        document.documentElement.setAttribute("data-theme-pref", r.value);
        // getAttribute: the form has a field named "action", which hides the form's .action property
        post(themeForm.getAttribute("action"), { action: "theme", theme: r.value })
          .then(function () { status.textContent = "Saved to your account."; })
          .catch(function () { status.textContent = "Couldn't save to your account; this browser will remember it."; });
      });
    });
  }

  // ---------- dashboard live feed (times in IST from the server) ----------
  var feed = document.getElementById("feed");
  if (feed) {
    var lastKey = null;
    var loadFeed = function () {
      fetch(feed.dataset.url, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (rows) {
        var key = rows.length ? rows[0].t + rows[0].step + rows[0].id : "";
        if (key === lastKey) return;
        var firstLoad = lastKey === null; lastKey = key;
        feed.replaceChildren();
        if (!rows.length) { var e = el("div", "empty"); e.appendChild(icon("activity")); e.appendChild(el("span", "", "No agent activity yet. Upload an invoice to start.")); feed.appendChild(e); return; }
        rows.forEach(function (r, i) {
          var row = el("div", "feed-row" + (!firstLoad && i === 0 ? " fresh" : ""));
          row.appendChild(el("span", "t", r.t + " IST"));
          row.appendChild(el("span", "tool", r.step));
          var msg = el("span", "");
          var a = el("a", "", (r.invoice || "#" + r.id) + " "); a.href = "/invoice/" + r.id;
          msg.appendChild(a); msg.appendChild(document.createTextNode(r.reason || ""));
          row.appendChild(msg); feed.appendChild(row);
        });
      }).catch(function () {});
    };
    loadFeed(); setInterval(loadFeed, 5000);
  }

  // ---------- upload: drag & drop ----------
  var drop = document.getElementById("drop");
  if (drop) {
    var input = document.getElementById("files"), files = document.getElementById("filelist");
    var show = function () { files.textContent = Array.prototype.map.call(input.files, function (f) { return f.name; }).join(", "); };
    ["dragenter", "dragover"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
    ["dragleave", "drop"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); }); });
    drop.addEventListener("drop", function (e) { input.files = e.dataTransfer.files; show(); });
    input.addEventListener("change", show);
  }

  // ---------- upload: live processing ----------
  document.querySelectorAll(".live[data-id]").forEach(function (box) {
    var id = box.dataset.id, steps = box.querySelector(".live-steps"), badge = box.querySelector(".live-status");
    var reason = box.querySelector(".live-reason"), link = box.querySelector(".live-link"), seen = 0;
    var tick = function () {
      fetch("/invoice/" + id + "/status", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
        d.steps.forEach(function (s, i) {
          if (i < seen) return;
          var row = el("div", "step feed-row-x");
          if (!reduceMotion) row.style.animation = "row-in 240ms cubic-bezier(.2,.7,.2,1) both";
          var dot = el("div", "dot " + s.result);
          if (s.result === "ok") dot.appendChild(icon("check"));
          else if (s.result === "problem" || s.result === "blocked") dot.appendChild(icon("triangle-alert"));
          else dot.textContent = String(i + 1);
          var b = el("div", ""); b.appendChild(el("div", "tool", s.step)); b.appendChild(el("div", "msg", s.reason));
          b.appendChild(el("div", "who", s.t + " IST"));
          row.appendChild(dot); row.appendChild(b); steps.appendChild(row);
        });
        seen = d.steps.length;
        badge.textContent = d.status_label || d.status;
        badge.className = "badge-s live-status b-" + d.status.toLowerCase();
        if (d.status !== "Processing") { reason.textContent = d.reason || ""; link.hidden = false; }
        else setTimeout(tick, 1000);
      }).catch(function () { setTimeout(tick, 2500); });
    };
    tick();
  });

  // ---------- evaluation running ----------
  var ev = document.getElementById("evalstate");
  if (ev) {
    var pollEval = function () {
      fetch(ev.dataset.url, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (s) {
        if (!s.running) { window.location.reload(); return; }
        ev.textContent = "Running the " + (s.set || "") + " set: " + s.done + " of " + (s.total || "?") + " invoices";
        setTimeout(pollEval, 1500);
      }).catch(function () { setTimeout(pollEval, 3000); });
    };
    pollEval();
  }

  // ---------- email composer: address field only for "custom" ----------
  var rec = document.getElementById("recipient");
  if (rec) {
    var toWrap = document.getElementById("to-wrap");
    var sync = function () { toWrap.hidden = rec.value !== "custom"; };
    rec.addEventListener("change", sync); sync();
  }

  // ---------- print / save as PDF ----------
  document.querySelectorAll("[data-print]").forEach(function (b) { b.addEventListener("click", function () { window.print(); }); });
  document.querySelectorAll("[data-copy]").forEach(function (b) {     // copy the text of another element (recovery codes)
    b.addEventListener("click", function () {
      var src = document.getElementById(b.dataset.copy);
      var text = Array.prototype.map.call(src.querySelectorAll("li"), function (li) { return li.textContent; }).join("\n") || src.textContent;
      if (!navigator.clipboard) return;
      navigator.clipboard.writeText(text).then(function () { b.lastChild.textContent = "Copied"; });
    });
  });


  // ---------- chat helpers (Ask ClearPay + Help). Server text is only ever set with textContent. ----------
  function chatMsg(log, who, text, extra) {
    var m = el("div", "chat-msg " + who);
    m.appendChild(el("span", "", text));
    if (extra && extra.note) m.appendChild(el("div", "chat-note", extra.note));
    if (extra && extra.links && extra.links.length) {
      var row = el("div", "chat-links");
      extra.links.forEach(function (l) { var a = el("a", "chip", l.label); a.href = l.url; row.appendChild(a); });
      m.appendChild(row);
    }
    if (extra && extra.mode) m.appendChild(el("div", "chat-meta", extra.mode === "ai" ? "Answered by the AI from ClearPay records" : "Answered straight from ClearPay records"));
    log.appendChild(m);
    log.scrollTop = log.scrollHeight;
    return m;
  }
  function typing(log) {
    var t = el("div", "chat-msg bot typing"); t.appendChild(icon("loader-circle", "ic-sm spin")); t.appendChild(el("span", "", " Looking it up…"));
    log.appendChild(t); log.scrollTop = log.scrollHeight; return t;
  }

  // ---------- Ask ClearPay ----------
  var askBox = document.getElementById("ask");
  if (askBox) {
    var askLog = document.getElementById("ask-log"), askForm = document.getElementById("ask-form"), askQ = document.getElementById("ask-q");
    var chatHistory = [], busy = false;
    var sendAsk = function (q) {
      q = (q || "").trim(); if (!q || busy) return;
      busy = true; chatMsg(askLog, "me", q); askQ.value = "";
      var t = typing(askLog);
      post(askBox.dataset.url, { q: q, history: JSON.stringify(chatHistory.slice(-6)), invoice: askBox.dataset.invoice || "" })
        .then(function (d) {
          t.remove();
          chatMsg(askLog, "bot", d.answer, { note: d.note, links: d.sources, mode: d.mode });
          chatHistory.push({ role: "user", text: q }, { role: "assistant", text: d.answer });
        })
        .catch(function () { t.remove(); chatMsg(askLog, "bot", "Something went wrong. Wait a moment and try again."); })
        .then(function () { busy = false; askQ.focus(); });
    };
    askForm.addEventListener("submit", function (e) { e.preventDefault(); sendAsk(askQ.value); });
    document.querySelectorAll("[data-ask-q]").forEach(function (b) { b.addEventListener("click", function () { sendAsk(b.dataset.askQ); }); });
  }

  // ---------- Help guide (top bar) ----------
  var helpPanel = document.getElementById("help-panel");
  if (helpPanel) {
    var helpLog = document.getElementById("help-log"), helpForm = document.getElementById("help-form"), helpQ = document.getElementById("help-q");
    var helpBusy = false;
    var sendHelp = function (q) {
      q = (q || "").trim(); if (!q || helpBusy) return;
      helpBusy = true; chatMsg(helpLog, "me", q); helpQ.value = "";
      var t = typing(helpLog);
      post(helpPanel.dataset.url, { q: q })
        .then(function (d) { t.remove(); chatMsg(helpLog, "bot", d.answer, { links: d.links }); })
        .catch(function () { t.remove(); chatMsg(helpLog, "bot", "Something went wrong. Try again in a moment."); })
        .then(function () { helpBusy = false; helpQ.focus(); });
    };
    helpForm.addEventListener("submit", function (e) { e.preventDefault(); sendHelp(helpQ.value); });
    document.querySelectorAll("[data-help-q]").forEach(function (b) { b.addEventListener("click", function () { sendHelp(b.dataset.helpQ); }); });
    document.getElementById("help-btn").addEventListener("click", function () { setTimeout(function () { helpQ.focus(); }, 80); });
  }

  // ---------- rules vs AI comparison running ----------
  var cmp = document.getElementById("cmpstate");
  if (cmp) {
    var pollCmp = function () {
      fetch(cmp.dataset.url, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (s) {
        if (!s.running) { window.location.reload(); return; }
        cmp.querySelector("span").textContent = "Running: " + s.done + " of " + (s.total || 40) + " invoices…";
        setTimeout(pollCmp, 2000);
      }).catch(function () { setTimeout(pollCmp, 4000); });
    };
    pollCmp();
  }

  // the sign-in entrance runs once
  if (body.classList.contains("enter")) setTimeout(function () { body.classList.remove("enter"); }, 1200);
})();

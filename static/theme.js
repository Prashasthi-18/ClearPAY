/* ClearPay theme: runs in <head> before first paint, so there is no light/dark flash.
   Signed in: the saved account setting wins (data-theme-pref on <html>). Signed out: this browser's last choice. */
(function () {
  var root = document.documentElement;
  root.classList.add("js");
  var hr = new Date().getHours();             // the sign-in page's sky follows the visitor's clock: dawn, day, dusk, night
  root.classList.add("tod-" + (hr >= 5 && hr < 8 ? "dawn" : hr >= 8 && hr < 17 ? "day" : hr >= 17 && hr < 20 ? "dusk" : "night"));
  try {                                     // sign-in opening titles: once per browser session, never with reduced motion
    if (sessionStorage.getItem("cp-intro")) root.classList.add("intro-seen");
    else if (!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches)) root.classList.add("intro-play");
  } catch (e) {}
  var server = root.getAttribute("data-theme-pref");
  var pref = server || "system";
  try { if (!server) { pref = localStorage.getItem("cp-theme") || "system"; } else { localStorage.setItem("cp-theme", server); } } catch (e) {}
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function apply(p) {
    var dark = p === "dark" || (p === "system" && mq && mq.matches);
    root.setAttribute("data-theme", dark ? "dark" : "light");
    root.setAttribute("data-bs-theme", dark ? "dark" : "light");
  }
  apply(pref);
  if (mq && mq.addEventListener) mq.addEventListener("change", function () { if (pref === "system") apply("system"); });
  window.ClearPayTheme = {
    get: function () { return pref; },
    set: function (p) {
      pref = p;
      try { localStorage.setItem("cp-theme", p); } catch (e) {}
      root.classList.add("theme-switching");
      apply(p);
      setTimeout(function () { root.classList.remove("theme-switching"); }, 320);
    }
  };
})();

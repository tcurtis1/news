/**
 * Source preference — Conservative / Balanced / Liberal.
 * Saved in localStorage + cookie (same pattern as geo).
 * Changing preference reloads the page with ?lean= so SSR/API match.
 */
(function () {
  var KEY = "yoyonews_lean";
  var COOKIE = "yoyonews_lean";
  var VALID = { conservative: 1, balanced: 1, liberal: 1 };
  var TIPS = {
    conservative:
      "Headlines prefer outlets on Feedspot’s conservative news list (Fox, WSJ, NY Post, Newsmax, Breitbart, Daily Wire, and peers). Not an endorsement — switch anytime.",
    balanced:
      "Headlines prefer wire/center outlets (Reuters, AP, BBC, Bloomberg, The Hill, …) plus a light mix of left- and right-leaning sources.",
    liberal:
      "Headlines prefer progressive / liberal outlets (Mother Jones, Vox, MSNBC, CNN Politics, The Nation, Intercept, and peers). Not an endorsement — switch anytime.",
  };

  function normalize(raw) {
    var s = String(raw || "")
      .trim()
      .toLowerCase();
    if (s === "right" || s === "c") return "conservative";
    if (s === "left" || s === "progressive" || s === "l") return "liberal";
    if (s === "center" || s === "centre" || s === "mixed" || s === "b")
      return "balanced";
    return VALID[s] ? s : "balanced";
  }

  function readCookie() {
    try {
      var parts = (document.cookie || "").split(";");
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i].trim();
        if (p.indexOf(COOKIE + "=") === 0) {
          return decodeURIComponent(p.slice(COOKIE.length + 1));
        }
      }
    } catch (e) {}
    return "";
  }

  function writeCookie(value) {
    try {
      var maxAge = 60 * 60 * 24 * 365;
      var secure =
        location.protocol === "https:" ? "; Secure" : "";
      document.cookie =
        COOKIE +
        "=" +
        encodeURIComponent(value) +
        "; path=/; max-age=" +
        maxAge +
        "; SameSite=Lax" +
        secure;
    } catch (e) {}
  }

  function load() {
    try {
      var fromLs = localStorage.getItem(KEY);
      if (fromLs) return normalize(fromLs);
    } catch (e) {}
    var fromCookie = readCookie();
    if (fromCookie) return normalize(fromCookie);
    return "balanced";
  }

  function save(value) {
    value = normalize(value);
    try {
      localStorage.setItem(KEY, value);
    } catch (e) {}
    writeCookie(value);
    return value;
  }

  function currentFromUrl() {
    try {
      var u = new URL(location.href);
      var v = u.searchParams.get("lean");
      if (v) return normalize(v);
    } catch (e) {}
    return null;
  }

  function navigateWithLean(lean) {
    lean = save(lean);
    try {
      var u = new URL(location.href);
      u.searchParams.set("lean", lean);
      // Drop flash params if any
      u.searchParams.delete("err");
      u.searchParams.delete("ok");
      location.href = u.toString();
    } catch (e) {
      location.reload();
    }
  }

  function paint(bar, lean) {
    if (!bar) return;
    bar.setAttribute("data-lean", lean);
    var hint = bar.querySelector("#lean-bar-hint") || bar.querySelector(".lean-bar-hint");
    if (hint && TIPS[lean]) hint.textContent = TIPS[lean];
    bar.querySelectorAll(".lean-seg-btn").forEach(function (btn) {
      var id = btn.getAttribute("data-lean");
      var on = id === lean;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  function init(opts) {
    opts = opts || {};
    var bar = document.getElementById("lean-bar");
    var urlLean = currentFromUrl();
    var lean = normalize(opts.lean || urlLean || load());
    // Sync storage with effective value
    save(lean);
    paint(bar, lean);

    if (bar) {
      bar.querySelectorAll(".lean-seg-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var next = btn.getAttribute("data-lean");
          if (!next || next === lean) return;
          navigateWithLean(next);
        });
      });
    }

    // Expose for MyNews / other scripts
    window.YoyoLeanPref = {
      load: load,
      save: save,
      normalize: normalize,
      current: function () {
        return lean;
      },
      KEY: KEY,
    };
    return lean;
  }

  // Auto-init when bar present
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      var bar = document.getElementById("lean-bar");
      if (bar) init({ lean: bar.getAttribute("data-lean") });
    });
  } else {
    var bar = document.getElementById("lean-bar");
    if (bar) init({ lean: bar.getAttribute("data-lean") });
  }

  window.YoyoLeanPrefInit = init;
})();

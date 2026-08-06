/**
 * Recency filter — Any time / Today / Past week / Past month.
 * Saved in localStorage only (MyNews topics never leave the device anyway).
 * Unlike lean-pref, changing this does not reload the page — callers
 * re-fetch/re-filter via the onChange callback.
 */
(function () {
  var KEY = "yoyonews_days";
  var VALID = { 0: 1, 1: 1, 7: 1, 30: 1 };

  function normalize(raw) {
    var n = parseInt(raw, 10);
    if (isNaN(n) || !VALID[n]) return 0;
    return n;
  }

  function load() {
    try {
      var fromLs = localStorage.getItem(KEY);
      if (fromLs !== null) return normalize(fromLs);
    } catch (e) {}
    return 0;
  }

  function save(value) {
    value = normalize(value);
    try {
      localStorage.setItem(KEY, String(value));
    } catch (e) {}
    return value;
  }

  function paint(bar, days) {
    if (!bar) return;
    bar.setAttribute("data-days", String(days));
    bar.querySelectorAll(".date-seg-btn").forEach(function (btn) {
      var on = normalize(btn.getAttribute("data-days")) === days;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  function init(opts) {
    opts = opts || {};
    var bar = document.getElementById("date-bar");
    var days = normalize(opts.days !== undefined ? opts.days : load());
    save(days);
    paint(bar, days);

    if (bar) {
      bar.querySelectorAll(".date-seg-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var next = normalize(btn.getAttribute("data-days"));
          if (next === days) return;
          days = save(next);
          paint(bar, days);
          if (typeof opts.onChange === "function") opts.onChange(days);
        });
      });
    }

    window.YoyoDatePref = {
      load: load,
      save: save,
      normalize: normalize,
      current: function () {
        return days;
      },
      KEY: KEY,
    };
    return days;
  }

  window.YoyoDatePrefInit = init;
})();

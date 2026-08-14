/**
 * View Preference Manager (List View vs Thumbnail Cards View)
 * Persists choice in localStorage ('yoyo_news_view_v1') and updates DOM.
 */
(function () {
  var KEY = "yoyo_news_view_v1";

  function getPref() {
    try {
      var val = localStorage.getItem(KEY);
      if (val === "list" || val === "cards") return val;
    } catch (e) {}
    return "cards"; // Default view mode: thumbnail cards
  }

  function setPref(mode) {
    if (mode !== "list" && mode !== "cards") return;
    try {
      localStorage.setItem(KEY, mode);
    } catch (e) {}
    applyMode(mode);
  }

  function applyMode(mode) {
    document.documentElement.setAttribute("data-view", mode);
    var targets = document.querySelectorAll(".feed-container, #my-feed, .list, #topic-hits-list");
    targets.forEach(function (t) {
      if (mode === "list") {
        t.classList.add("view-mode-list");
        t.classList.remove("view-mode-cards");
      } else {
        t.classList.add("view-mode-cards");
        t.classList.remove("view-mode-list");
      }
    });

    // Update active state on toggle buttons
    var btnList = document.querySelectorAll(".view-toggle-btn");
    btnList.forEach(function (btn) {
      var isTarget = btn.getAttribute("data-view") === mode;
      btn.classList.toggle("active", isTarget);
      btn.setAttribute("aria-pressed", isTarget ? "true" : "false");
    });
  }

  function init() {
    var mode = getPref();
    applyMode(mode);

    document.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest && ev.target.closest(".view-toggle-btn");
      if (!btn) return;
      var newMode = btn.getAttribute("data-view");
      if (newMode) {
        setPref(newMode);
        if (window.YoyoMyNews && window.YoyoMyNews.refresh) {
          window.YoyoMyNews.refresh();
        }
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.YoyoViewPref = {
    current: getPref,
    set: setPref,
    apply: applyMode
  };
})();

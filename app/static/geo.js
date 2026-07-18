/**
 * Collapsed-by-default location control.
 * Expects markup from templates (geo-bar with #geo-toggle, #geo-panel, etc.).
 */
(function () {
  var STORAGE_KEY = "yoyonews_geo";

  function qs(id) {
    return document.getElementById(id);
  }

  function setCookie(geo) {
    try {
      document.cookie =
        "yoyonews_geo=" +
        encodeURIComponent(geo) +
        "; path=/; max-age=31536000; samesite=lax";
      localStorage.setItem(STORAGE_KEY, geo);
    } catch (e) {}
  }

  function initGeoBar(opts) {
    opts = opts || {};
    var toggle = qs("geo-toggle");
    var panel = qs("geo-panel");
    var country = qs("geo-country");
    var state = qs("geo-state");
    var stateWrap = qs("geo-state-wrap");
    var typeahead = qs("geo-typeahead");
    var applyBtn = qs("geo-apply");
    var geoHidden = qs("geo-hidden");
    var chevron = qs("geo-chevron");
    if (!toggle || !panel) return;

    var current = (
      (geoHidden && geoHidden.value) ||
      opts.currentGeo ||
      "US"
    ).toUpperCase();

    // Mirror explicit ?geo= into cookie/localStorage (search page); optional redirect from LS
    try {
      var params = new URLSearchParams(window.location.search);
      if (params.has("geo")) {
        setCookie(current);
      } else if (opts.redirectIfSaved) {
        var saved = localStorage.getItem(STORAGE_KEY);
        if (saved && saved.toUpperCase() !== current) {
          params.set("geo", saved);
          var base = opts.redirectBase || "/search";
          window.location.replace(
            base + (params.toString() ? "?" + params.toString() : "")
          );
          return;
        }
      }
    } catch (e) {}

    function setOpen(open) {
      if (open) {
        panel.removeAttribute("hidden");
        toggle.setAttribute("aria-expanded", "true");
        if (chevron) chevron.textContent = "▴";
      } else {
        panel.setAttribute("hidden", "");
        toggle.setAttribute("aria-expanded", "false");
        if (chevron) chevron.textContent = "▾";
      }
    }

    function syncStateVisibility() {
      if (!country || !stateWrap) return;
      var show = country.value === "US";
      stateWrap.hidden = !show;
      if (!show && state) state.value = "";
    }

    function resolvedGeo() {
      var typed = ((typeahead && typeahead.value) || "").trim();
      if (typed) return typed;
      if (country && country.value === "US" && state && state.value)
        return state.value;
      return (country && country.value) || current || "US";
    }

    function go(geo) {
      setCookie(geo);
      if (geoHidden) geoHidden.value = geo;
      if (typeof opts.onApply === "function") {
        opts.onApply(geo);
        return;
      }
      var form = qs("search-form");
      var q = "";
      if (form) {
        var qInput = form.querySelector('input[name="q"]');
        q = qInput ? qInput.value : "";
      }
      var url = "/search?geo=" + encodeURIComponent(geo);
      if (q) url += "&q=" + encodeURIComponent(q);
      window.location.href = url;
    }

    toggle.addEventListener("click", function () {
      var open = panel.hasAttribute("hidden");
      setOpen(open);
    });

    if (country) country.addEventListener("change", syncStateVisibility);
    if (applyBtn)
      applyBtn.addEventListener("click", function () {
        go(resolvedGeo());
      });
    if (typeahead) {
      typeahead.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          go(resolvedGeo());
        }
      });
    }
    syncStateVisibility();
    setOpen(false); // always start collapsed
  }

  window.YoyoNewsGeo = {
    init: initGeoBar,
    setCookie: setCookie,
    STORAGE_KEY: STORAGE_KEY,
  };
})();

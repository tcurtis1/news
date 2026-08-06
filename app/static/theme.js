/* Shared light/dark theme preference for yoyosup.com. */
(function () {
  "use strict";
  var KEY = "yoyo_theme_v1";
  var COOKIE = "yoyo_theme";

  function cookieValue() {
    var match = document.cookie.match(/(?:^|; )yoyo_theme=(light|dark)(?:;|$)/);
    return match ? match[1] : "";
  }

  function savedValue() {
    var value = cookieValue();
    if (value) return value;
    try {
      value = localStorage.getItem(KEY) || "";
    } catch (err) {}
    return value === "light" || value === "dark" ? value : "";
  }

  function preferredValue() {
    var saved = savedValue();
    if (saved) return saved;
    return window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function apply(value) {
    document.documentElement.setAttribute("data-theme", value);
    document.documentElement.style.colorScheme = value;
    updateButton(value);
  }

  function remember(value) {
    try {
      localStorage.setItem(KEY, value);
    } catch (err) {}
    var secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie =
      COOKIE + "=" + value +
      "; Max-Age=31536000; Path=/; Domain=.yoyosup.com; SameSite=Lax" +
      secure;
  }

  function updateButton(value) {
    var button = document.querySelector("[data-yoyo-theme-toggle]");
    if (!button) return;
    var dark = value === "dark";
    button.textContent = dark ? "Light" : "Dark";
    button.setAttribute("aria-label", dark ? "Use light theme" : "Use dark theme");
    button.setAttribute("title", dark ? "Use light theme" : "Use dark theme");
    button.setAttribute("aria-pressed", dark ? "true" : "false");
  }

  function mount() {
    if (document.querySelector("[data-yoyo-theme-toggle]")) {
      updateButton(preferredValue());
      return;
    }
    var button = document.createElement("button");
    button.type = "button";
    button.className = "theme-toggle";
    button.setAttribute("data-yoyo-theme-toggle", "1");
    button.addEventListener("click", function () {
      var next =
        document.documentElement.getAttribute("data-theme") === "dark"
          ? "light"
          : "dark";
      remember(next);
      apply(next);
    });
    var row = document.querySelector(".site-header-row");
    if (row) row.appendChild(button);
    else {
      button.classList.add("theme-toggle-floating");
      document.body.appendChild(button);
    }
    updateButton(preferredValue());
  }

  apply(preferredValue());

  if (window.matchMedia) {
    var media = window.matchMedia("(prefers-color-scheme: dark)");
    var onSystemChange = function (event) {
      if (!savedValue()) apply(event.matches ? "dark" : "light");
    };
    if (media.addEventListener) media.addEventListener("change", onSystemChange);
    else if (media.addListener) media.addListener(onSystemChange);
  }

  document.addEventListener("yoyo-chrome-ready", mount);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();

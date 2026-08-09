(function () {
  "use strict";

  var SEEN_KEY = "yoyonews_seen_v1";
  var SESSION_KEY = "yoyonews_session_v1";
  var SESSION_MS = 30 * 60 * 1000;

  function send(name) {
    var body = JSON.stringify({ name: name });
    if (navigator.sendBeacon) {
      navigator.sendBeacon("/api/analytics-event", new Blob([body], { type: "application/json" }));
      return;
    }
    fetch("/api/analytics-event", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      credentials: "same-origin",
      keepalive: true,
    }).catch(function () {});
  }

  window.yoyoNewsEvent = send;

  try {
    var now = Date.now();
    var lastSession = parseInt(localStorage.getItem(SESSION_KEY) || "0", 10);
    if (!lastSession || now - lastSession > SESSION_MS) {
      var returning = localStorage.getItem(SEEN_KEY) === "1";
      send(returning ? "session_returning" : "session_new");
      localStorage.setItem(SEEN_KEY, "1");
    }
    localStorage.setItem(SESSION_KEY, String(now));
  } catch (error) {
    // Storage can be disabled; reading the site must still work normally.
  }

  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a");
    if (!link) return;
    var href = link.getAttribute("href") || "";
    if (href.indexOf("/topic/") === 0) {
      send("topic_open");
      return;
    }
    try {
      var url = new URL(link.href, window.location.href);
      if (url.hostname && url.hostname !== window.location.hostname) send("story_click");
    } catch (error) {}
  });

  document.addEventListener("submit", function (event) {
    if (event.target && event.target.id === "search-form") send("search_submit");
  });
})();

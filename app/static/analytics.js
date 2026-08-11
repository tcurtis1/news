(function () {
  "use strict";

  var SEEN_KEY = "yoyonews_seen_v1";
  var SESSION_KEY = "yoyonews_session_v1";
  var READ_KEY = "yoyonews_read_stories_v1";
  var SESSION_MS = 30 * 60 * 1000;
  var READ_LIMIT = 500;

  function storyKey(href) {
    try {
      var url = new URL(href, window.location.href);
      url.hash = "";
      ["fbclid", "gclid", "mc_cid", "mc_eid"].forEach(function (name) {
        url.searchParams.delete(name);
      });
      Array.from(url.searchParams.keys()).forEach(function (name) {
        if (name.toLowerCase().indexOf("utm_") === 0) url.searchParams.delete(name);
      });
      url.searchParams.sort();
      return url.toString();
    } catch (error) {
      return "";
    }
  }

  function readStories() {
    try {
      var value = JSON.parse(localStorage.getItem(READ_KEY) || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch (error) {
      return {};
    }
  }

  function showRead(link) {
    link.classList.add("is-read");
    link.setAttribute("data-read-label", "Read");
  }

  function applyReadState(root) {
    var stories = readStories();
    var links = [];
    if (root && root.matches && root.matches("a[data-story-link]")) links.push(root);
    if (root && root.querySelectorAll) {
      links = links.concat(Array.from(root.querySelectorAll("a[data-story-link]")));
    }
    links.forEach(function (link) {
      if (stories[storyKey(link.href)]) showRead(link);
    });
  }

  function rememberRead(link) {
    var key = storyKey(link.href);
    if (!key) return;
    showRead(link);
    try {
      var stories = readStories();
      stories[key] = Date.now();
      var keys = Object.keys(stories);
      if (keys.length > READ_LIMIT) {
        keys.sort(function (a, b) { return stories[b] - stories[a]; });
        keys.slice(READ_LIMIT).forEach(function (oldKey) { delete stories[oldKey]; });
      }
      localStorage.setItem(READ_KEY, JSON.stringify(stories));
    } catch (error) {
      // Private browsing or disabled storage: keep the current-page marker only.
    }
  }

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

  function sendPageView() {
    var ref = new URLSearchParams(window.location.search).get("ref") || "direct";
    var body = JSON.stringify({ path: window.location.pathname, ref: ref });
    if (navigator.sendBeacon) {
      navigator.sendBeacon("/api/page-view", new Blob([body], { type: "application/json" }));
      return;
    }
    fetch("/api/page-view", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      credentials: "same-origin",
      keepalive: true,
    }).catch(function () {});
  }

  sendPageView();

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
    if (link.matches("a[data-story-link]")) rememberRead(link);
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

  applyReadState(document);
  if (typeof MutationObserver !== "undefined") {
    new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        Array.from(mutation.addedNodes || []).forEach(function (node) {
          if (node.nodeType === 1) applyReadState(node);
        });
      });
    }).observe(document.documentElement, { childList: true, subtree: true });
  }
})();

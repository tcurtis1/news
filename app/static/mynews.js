/**
 * MyNews — personal topic list in localStorage (no auth).
 * Schema: { v:1, topics: [{ slug, label, addedAt }] }
 */
(function () {
  var KEY = "yoyonews_my_topics";
  var MAX = 20;
  var geo = "US";

  function slugify(text) {
    var t = String(text || "")
      .toLowerCase()
      .trim()
      .replace(/[#@]/g, "")
      .replace(/[^\w\s-]/gu, "")
      .replace(/[-\s]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return (t.slice(0, 80) || "topic").replace(/^-+|-+$/g, "");
  }

  function load() {
    try {
      var raw = localStorage.getItem(KEY);
      if (!raw) return { v: 1, topics: [] };
      var data = JSON.parse(raw);
      if (!data || !Array.isArray(data.topics)) return { v: 1, topics: [] };
      data.topics = data.topics
        .filter(function (t) {
          return t && t.slug && t.label;
        })
        .slice(0, MAX);
      return data;
    } catch (e) {
      return { v: 1, topics: [] };
    }
  }

  function save(data) {
    try {
      localStorage.setItem(KEY, JSON.stringify({ v: 1, topics: data.topics || [] }));
    } catch (e) {}
  }

  function el(id) {
    return document.getElementById(id);
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderChips(topics) {
    var bar = el("my-filter-chips");
    var countEl = el("my-topic-count");
    if (countEl) {
      countEl.textContent =
        topics.length === 0
          ? "No topics yet"
          : topics.length + " topic" + (topics.length === 1 ? "" : "s");
    }
    if (!bar) return;
    if (!topics.length) {
      bar.innerHTML =
        '<span class="my-chip-empty">Add topics below — they stay on this device only.</span>';
      return;
    }
    bar.innerHTML = topics
      .map(function (t, i) {
        return (
          '<span class="my-chip" data-slug="' +
          escapeHtml(t.slug) +
          '">' +
          '<a href="/topic/' +
          encodeURIComponent(t.slug) +
          "?geo=" +
          encodeURIComponent(geo) +
          '">' +
          escapeHtml(t.label) +
          "</a>" +
          '<button type="button" class="my-chip-remove" data-i="' +
          i +
          '" aria-label="Remove ' +
          escapeHtml(t.label) +
          '">×</button>' +
          "</span>"
        );
      })
      .join("");
    bar.querySelectorAll(".my-chip-remove").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var i = parseInt(btn.getAttribute("data-i"), 10);
        var data = load();
        data.topics.splice(i, 1);
        save(data);
        refresh();
      });
    });
  }

  function addTopic(label) {
    label = String(label || "").trim().slice(0, 80);
    if (!label) return false;
    var slug = slugify(label);
    var data = load();
    if (data.topics.some(function (t) {
      return t.slug === slug;
    })) {
      return false;
    }
    if (data.topics.length >= MAX) {
      alert("Max " + MAX + " topics. Remove one first.");
      return false;
    }
    data.topics.push({
      slug: slug,
      label: label,
      addedAt: new Date().toISOString(),
    });
    save(data);
    return true;
  }

  function boardKeys() {
    return [
      "google",
      "bing",
      "youtube",
      "x",
      "polymarket",
      "tiktok",
      "facebook",
      "instagram",
    ];
  }

  function renderFeedCard(topic, payload) {
    var rank = (payload && payload.rank_lookup) || {};
    var plats = rank.platforms || {};
    var hits = (payload && payload.hits) || [];
    var pills = boardKeys()
      .map(function (k) {
        var row = plats[k];
        if (!row || !row.in_top) return "";
        return (
          '<span class="rank-pill plat-' +
          k +
          '">' +
          escapeHtml(row.label || k) +
          " #" +
          row.rank +
          "</span>"
        );
      })
      .filter(Boolean)
      .join("");

    var hitHtml = hits
      .slice(0, 4)
      .map(function (h) {
        return (
          '<li><a href="' +
          escapeHtml(h.url) +
          '" rel="noopener noreferrer" target="_blank">' +
          escapeHtml(h.title) +
          "</a>" +
          (h.source
            ? '<span class="muted-hint"> · ' + escapeHtml(h.source) + "</span>"
            : "") +
          "</li>"
        );
      })
      .join("");

    var summary = rank.summary || "Loading ranks…";
    return (
      '<article class="my-topic-card card" data-slug="' +
      escapeHtml(topic.slug) +
      '">' +
      "<div>" +
      '<h2 class="my-topic-title"><a href="/topic/' +
      encodeURIComponent(topic.slug) +
      "?geo=" +
      encodeURIComponent(geo) +
      '">' +
      escapeHtml(topic.label) +
      "</a></h2>" +
      '<p class="summary">' +
      escapeHtml(summary) +
      ' · <a href="/topic/' +
      encodeURIComponent(topic.slug) +
      "?geo=" +
      encodeURIComponent(geo) +
      '#comments">discuss</a></p>' +
      (pills ? '<div class="rank-pills">' + pills + "</div>" : "") +
      (hitHtml
        ? '<ul class="my-hit-list">' + hitHtml + "</ul>"
        : '<p class="muted-hint">No free-index headlines right now — open the topic page or portals.</p>') +
      "</div></article>"
    );
  }

  async function loadTopicPayload(topic) {
    var url =
      "/api/search?q=" +
      encodeURIComponent(topic.label) +
      "&geo=" +
      encodeURIComponent(geo);
    var res = await fetch(url);
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  async function renderFeed(topics) {
    var feed = el("my-feed");
    var empty = el("my-empty");
    if (!feed) return;
    if (!topics.length) {
      feed.innerHTML = "";
      feed.hidden = true;
      if (empty) empty.hidden = false;
      loadSuggestions();
      return;
    }
    if (empty) empty.hidden = true;
    feed.hidden = false;
    feed.innerHTML =
      '<p class="muted-hint my-loading">Loading your topics for ' +
      escapeHtml(geo) +
      "…</p>";

    var cards = [];
    for (var i = 0; i < topics.length; i++) {
      var t = topics[i];
      try {
        var data = await loadTopicPayload(t);
        cards.push(renderFeedCard(t, data));
      } catch (e) {
        cards.push(
          '<article class="my-topic-card card"><h2>' +
            escapeHtml(t.label) +
            '</h2><p class="summary">Couldn’t load — try again.</p></article>'
        );
      }
    }
    feed.innerHTML = cards.join("");
  }

  async function loadSuggestions() {
    var box = el("my-suggestions");
    if (!box) return;
    box.innerHTML = '<span class="muted-hint">Loading ideas…</span>';
    try {
      var res = await fetch(
        "/api/trends?geo=" + encodeURIComponent(geo)
      );
      var data = await res.json();
      var cons = data.consensus || [];
      var ideas = cons.slice(0, 10).map(function (c) {
        return c.title;
      });
      if (!ideas.length) {
        // fall back to google top
        var g = (data.top10 && data.top10.google) || [];
        ideas = g.slice(0, 8).map(function (x) {
          return x.title;
        });
      }
      if (!ideas.length) {
        box.innerHTML =
          '<span class="muted-hint">No suggestions today — type any topic above.</span>';
        return;
      }
      box.innerHTML = ideas
        .map(function (title) {
          return (
            '<button type="button" class="my-suggest-chip" data-label="' +
            escapeHtml(title) +
            '">+ ' +
            escapeHtml(title) +
            "</button>"
          );
        })
        .join("");
      box.querySelectorAll(".my-suggest-chip").forEach(function (btn) {
        btn.addEventListener("click", function () {
          if (addTopic(btn.getAttribute("data-label"))) refresh();
        });
      });
    } catch (e) {
      box.innerHTML =
        '<span class="muted-hint">Suggestions unavailable — type a topic above.</span>';
    }
  }

  function refresh() {
    var data = load();
    renderChips(data.topics);
    renderFeed(data.topics);
    updateUrlImport();
  }

  function updateUrlImport() {
    // Support ?topics=fed,housing share links (import once, strip from URL)
    try {
      var params = new URLSearchParams(window.location.search);
      var raw = params.get("topics");
      if (!raw) return;
      var parts = raw.split(",").map(function (s) {
        return s.trim();
      });
      var added = false;
      parts.forEach(function (p) {
        if (p && addTopic(p.replace(/-/g, " "))) added = true;
      });
      params.delete("topics");
      var qs = params.toString();
      var path = window.location.pathname + (qs ? "?" + qs : "");
      window.history.replaceState({}, "", path);
      if (added) {
        /* already will refresh */
      }
    } catch (e) {}
  }

  function wireAddForm() {
    var form = el("my-add-form");
    var input = el("my-add-input");
    if (!form || !input) return;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var v = input.value;
      if (addTopic(v)) {
        input.value = "";
        refresh();
      } else if (v.trim()) {
        // duplicate — still clear? keep and flash
        input.select();
      }
    });
  }

  function wireClear() {
    var btn = el("my-clear");
    if (!btn) return;
    btn.addEventListener("click", function () {
      if (!confirm("Remove all saved topics on this device?")) return;
      save({ v: 1, topics: [] });
      refresh();
    });
  }

  function init(opts) {
    opts = opts || {};
    geo = (opts.geo || "US").toUpperCase();
    var geoHidden = el("geo-hidden");
    if (geoHidden && geoHidden.value) geo = geoHidden.value.toUpperCase();

    wireAddForm();
    wireClear();

    // Import ?topics= before first render
    try {
      var params = new URLSearchParams(window.location.search);
      if (params.get("topics")) {
        params
          .get("topics")
          .split(",")
          .forEach(function (p) {
            addTopic(p.trim().replace(/-/g, " "));
          });
        params.delete("topics");
        var qs = params.toString();
        window.history.replaceState(
          {},
          "",
          window.location.pathname + (qs ? "?" + qs : "")
        );
      }
    } catch (e) {}

    refresh();

    if (window.YoyoNewsGeo) {
      window.YoyoNewsGeo.init({
        currentGeo: geo,
        redirectIfSaved: true,
        redirectBase: "/my",
        onApply: function (newGeo) {
          window.location.href =
            "/my?geo=" + encodeURIComponent(newGeo);
        },
      });
    }
  }

  window.YoyoMyNews = { init: init, load: load, KEY: KEY };
})();

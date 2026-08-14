/**
 * MyNews — personal topic list in localStorage (no auth).
 * Schema: { v:1, topics: [{ slug, label, addedAt }] }
 */
(function () {
  var KEY = "yoyonews_my_topics";
  var MAX = 20;
  var geo = "US";
  var lean = "balanced";

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

  function expandLegacyTopics(topics) {
    // Older chips may store "a, b" as one label — split into one phrase per chip
    var out = [];
    var seen = {};
    (topics || []).forEach(function (t) {
      if (!t || !t.label) return;
      var labels =
        String(t.label).indexOf(",") >= 0
          ? parseLabels(t.label)
          : [String(t.label).trim()];
      labels.forEach(function (label) {
        var slug = slugify(label);
        if (!slug || seen[slug]) return;
        seen[slug] = true;
        out.push({
          slug: slug,
          label: label,
          addedAt: t.addedAt || new Date().toISOString(),
        });
      });
    });
    return out.slice(0, MAX);
  }

  function load() {
    try {
      var raw = localStorage.getItem(KEY);
      if (!raw) return { v: 1, topics: [] };
      var data = JSON.parse(raw);
      if (!data || !Array.isArray(data.topics)) return { v: 1, topics: [] };
      var expanded = expandLegacyTopics(
        data.topics.filter(function (t) {
          return t && t.slug && t.label;
        })
      );
      // Persist split if we expanded any comma chips
      var changed =
        expanded.length !== data.topics.length ||
        expanded.some(function (t, i) {
          return !data.topics[i] || data.topics[i].label !== t.label;
        });
      data.topics = expanded;
      if (changed) save(data);
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
        if (window.yoyoNewsEvent) window.yoyoNewsEvent("mynews_topic_remove");
        refresh();
      });
    });
  }

  /**
   * Comma-separated input → separate topics (OR).
   * Each segment is one phrase (multi-word = sentence / AND-ish match).
   * "Federal Reserve, housing, Utah" → three chips, each searched alone.
   */
  function parseLabels(raw) {
    return String(raw || "")
      .split(",")
      .map(function (s) {
        return s.replace(/\s+/g, " ").trim().slice(0, 80);
      })
      .filter(Boolean)
      .slice(0, 10);
  }

  function addTopic(label) {
    label = String(label || "").trim().slice(0, 80);
    if (!label) return false;
    // If user pastes commas into addTopic directly, still split
    if (label.indexOf(",") >= 0) {
      return addTopicsFromInput(label);
    }
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
    if (window.yoyoNewsEvent) window.yoyoNewsEvent("mynews_topic_add");
    return true;
  }

  function addTopicsFromInput(raw) {
    var labels = parseLabels(raw);
    if (!labels.length) return false;
    var any = false;
    var data = load();
    for (var i = 0; i < labels.length; i++) {
      if (data.topics.length >= MAX) {
        if (any) save(data);
        alert("Max " + MAX + " topics. Some were not added.");
        return any;
      }
      var label = labels[i];
      var slug = slugify(label);
      if (data.topics.some(function (t) {
        return t.slug === slug;
      })) {
        continue;
      }
      data.topics.push({
        slug: slug,
        label: label,
        addedAt: new Date().toISOString(),
      });
      any = true;
    }
    if (any) {
      save(data);
      if (window.yoyoNewsEvent) window.yoyoNewsEvent("mynews_topic_add");
    }
    return any;
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

  // Preferred-source hits per topic slug (for in-card "Load more")
  var hitsBySlug = {};

  function hitLi(h) {
    var imgHtml = h.image_url
      ? '<div class="story-thumb"><img class="story-thumb-img" src="' +
        escapeHtml(h.image_url) +
        '" alt="" loading="lazy" onError="this.style.display=\'none\';" /></div>'
      : '';
    return (
      '<li>' +
      imgHtml +
      '<a href="' +
      escapeHtml(h.url) +
      '" rel="noopener noreferrer" target="_blank" data-story-link>' +
      escapeHtml(h.title) +
      "</a>" +
      (h.source
        ? '<span class="muted-hint"> · ' + escapeHtml(h.source) + "</span>"
        : "") +
      (h.author
        ? ' <a class="byline-link" href="/journalist/' +
          encodeURIComponent(slugify(h.author)) +
          '">By ' +
          escapeHtml(h.author) +
          "</a>"
        : "") +
      "</li>"
    );
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

    var PAGE = 8;
    var shown = Math.min(PAGE, hits.length);
    hitsBySlug[topic.slug] = hits;
    var hitHtml = hits.slice(0, shown).map(hitLi).join("");

    var summary = rank.summary || "Loading ranks…";
    var leanNow =
      (window.YoyoLeanPref && YoyoLeanPref.current()) || lean || "balanced";
    var leanLabel =
      (payload && payload.lean_pref_label) ||
      (leanNow === "conservative"
        ? "Conservative"
        : leanNow === "liberal"
          ? "Liberal"
          : "Balanced");
    var topicHref =
      "/topic/" +
      encodeURIComponent(topic.slug) +
      "?geo=" +
      encodeURIComponent(geo) +
      "&lean=" +
      encodeURIComponent(leanNow);
    var moreBtn =
      hits.length > PAGE
        ? '<div class="load-more-wrap my-card-more">' +
          '<button type="button" class="load-more-btn my-load-more" data-slug="' +
          escapeHtml(topic.slug) +
          '" data-shown="' +
          PAGE +
          '">Load 20 more</button>' +
          '<p class="muted-hint my-load-meta">Showing ' +
          shown +
          " of " +
          hits.length +
          " · " +
          escapeHtml(leanLabel) +
          " sources</p></div>"
        : hits.length
          ? '<p class="muted-hint my-load-meta">Showing ' +
            hits.length +
            " · " +
            escapeHtml(leanLabel) +
            " sources</p>"
          : "";
    return (
      '<article class="my-topic-card card" data-slug="' +
      escapeHtml(topic.slug) +
      '">' +
      '<div class="story-thumb">' +
      '<span class="story-thumb-badge">' + escapeHtml(topic.label.slice(0, 14)) + '</span>' +
      '</div>' +
      "<div>" +
      '<h2 class="my-topic-title"><a href="' +
      topicHref +
      '">' +
      escapeHtml(topic.label) +
      "</a></h2>" +
      '<p class="summary">' +
      escapeHtml(summary) +
      " · " +
      escapeHtml(leanLabel) +
      " sources · <a href=\"" +
      topicHref +
      '#comments">discuss</a></p>' +
      (pills ? '<div class="rank-pills">' + pills + "</div>" : "") +
      (hitHtml
        ? '<ul class="my-hit-list">' + hitHtml + "</ul>" + moreBtn
        : '<p class="muted-hint">No preferred-source headlines right now — open the topic page, try another source lean, or widen the recency filter.</p>') +
      "</div></article>"
    );
  }

  function wireMyLoadMore(feedEl) {
    if (!feedEl || feedEl._yoyoLoadMoreWired) return;
    feedEl._yoyoLoadMoreWired = true;
    feedEl.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest && ev.target.closest(".my-load-more");
      if (!btn) return;
      var slug = btn.getAttribute("data-slug") || "";
      var hits = hitsBySlug[slug] || [];
      if (!hits.length) return;
      var shown = parseInt(btn.getAttribute("data-shown") || "8", 10) || 8;
      var step = 20;
      var next = Math.min(hits.length, shown + step);
      var list = btn.closest(".my-topic-card") &&
        btn.closest(".my-topic-card").querySelector(".my-hit-list");
      if (!list) return;
      list.innerHTML = hits.slice(0, next).map(hitLi).join("");
      btn.setAttribute("data-shown", String(next));
      var meta = btn.parentNode && btn.parentNode.querySelector(".my-load-meta");
      if (meta) {
        meta.textContent =
          next >= hits.length
            ? "Showing all " + hits.length + " with current filters"
            : "Showing " + next + " of " + hits.length;
      }
      if (next >= hits.length) {
        btn.disabled = true;
        btn.textContent = "No more headlines";
      }
    });
  }

  // Bump to ignore stale responses when the user refreshes mid-load
  var feedGen = 0;
  // How many topic cards to fetch at once (server-friendly)
  var FETCH_CONCURRENCY = 3;

  async function loadTopicPayload(topic) {
    // One chip = one phrase. lite=1 = preferred headlines + ranks only (fast path).
    var leanNow =
      (window.YoyoLeanPref && YoyoLeanPref.current()) || lean || "balanced";
    var daysNow = (window.YoyoDatePref && YoyoDatePref.current()) || 0;
    var url =
      "/api/search?q=" +
      encodeURIComponent(topic.label) +
      "&geo=" +
      encodeURIComponent(geo) +
      "&lean=" +
      encodeURIComponent(leanNow) +
      "&lite=1";
    if (daysNow) url += "&days=" + encodeURIComponent(daysNow);
    var res = await fetch(url);
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  function skeletonCard(topic) {
    var leanNow =
      (window.YoyoLeanPref && YoyoLeanPref.current()) || lean || "balanced";
    var topicHref =
      "/topic/" +
      encodeURIComponent(topic.slug) +
      "?geo=" +
      encodeURIComponent(geo) +
      "&lean=" +
      encodeURIComponent(leanNow);
    return (
      '<article class="my-topic-card card my-topic-loading" data-slug="' +
      escapeHtml(topic.slug) +
      '" aria-busy="true">' +
      "<div>" +
      '<h2 class="my-topic-title"><a href="' +
      topicHref +
      '">' +
      escapeHtml(topic.label) +
      "</a></h2>" +
      '<p class="summary my-card-status">Loading ranks &amp; headlines…</p>' +
      '<div class="my-skel" aria-hidden="true">' +
      '<div class="my-skel-line"></div>' +
      '<div class="my-skel-line short"></div>' +
      '<div class="my-skel-line"></div>' +
      "</div>" +
      "</div></article>"
    );
  }

  function errorCard(topic, msg) {
    return (
      '<article class="my-topic-card card" data-slug="' +
      escapeHtml(topic.slug) +
      '">' +
      '<h2 class="my-topic-title">' +
      escapeHtml(topic.label) +
      "</h2>" +
      '<p class="summary">' +
      escapeHtml(msg || "Couldn’t load — try again.") +
      "</p>" +
      "</article>"
    );
  }

  function replaceCardBySlug(feed, topic, html) {
    var node = feed.querySelector(
      '.my-topic-card[data-slug="' +
        String(topic.slug).replace(/"/g, "") +
        '"]'
    );
    if (!node) return;
    var wrap = document.createElement("div");
    wrap.innerHTML = html;
    var next = wrap.firstElementChild;
    if (next) node.replaceWith(next);
  }

  async function renderFeed(topics) {
    var feed = el("my-feed");
    var empty = el("my-empty");
    if (!feed) return;
    var gen = ++feedGen;

    if (!topics.length) {
      feed.innerHTML = "";
      feed.hidden = true;
      if (empty) empty.hidden = false;
      loadSuggestions();
      return;
    }
    if (empty) empty.hidden = true;
    feed.hidden = false;
    wireMyLoadMore(feed);

    // Paint every card as a skeleton immediately, then fill as each returns
    feed.innerHTML =
      '<p class="muted-hint my-loading" id="my-load-status">Loading ' +
      topics.length +
      " topic" +
      (topics.length === 1 ? "" : "s") +
      " for " +
      escapeHtml(geo) +
      "…</p>" +
      topics.map(skeletonCard).join("");

    var done = 0;
    var statusEl = el("my-load-status");

    function tickStatus() {
      if (!statusEl || gen !== feedGen) return;
      if (done >= topics.length) {
        statusEl.remove();
        return;
      }
      statusEl.textContent =
        "Loaded " + done + " of " + topics.length + " topics…";
    }

    async function loadOne(topic) {
      if (gen !== feedGen) return;
      try {
        var data = await loadTopicPayload(topic);
        if (gen !== feedGen) return;
        replaceCardBySlug(feed, topic, renderFeedCard(topic, data));
      } catch (e) {
        if (gen !== feedGen) return;
        replaceCardBySlug(
          feed,
          topic,
          errorCard(topic, "Couldn’t load — try again.")
        );
      } finally {
        done += 1;
        tickStatus();
      }
    }

    // Parallel pool — up to FETCH_CONCURRENCY in flight
    var cursor = 0;
    async function worker() {
      while (cursor < topics.length) {
        if (gen !== feedGen) return;
        var idx = cursor++;
        await loadOne(topics[idx]);
      }
    }
    var workers = [];
    var n = Math.min(FETCH_CONCURRENCY, topics.length);
    for (var w = 0; w < n; w++) workers.push(worker());
    await Promise.all(workers);
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
      // Commas → multiple topics (OR); each segment is one phrase
      if (addTopicsFromInput(v)) {
        input.value = "";
        refresh();
      } else if (v.trim()) {
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
      if (window.yoyoNewsEvent) window.yoyoNewsEvent("mynews_clear");
      refresh();
    });
  }

  function init(opts) {
    opts = opts || {};
    geo = (opts.geo || "US").toUpperCase();
    lean = (opts.lean || "balanced").toLowerCase();
    if (window.YoyoLeanPref) {
      lean = YoyoLeanPref.normalize(opts.lean || YoyoLeanPref.load());
    }
    var geoHidden = el("geo-hidden");
    if (geoHidden && geoHidden.value) geo = geoHidden.value.toUpperCase();

    wireAddForm();
    wireClear();

    // Import ?topics=a,b,c before first render (commas already OR)
    try {
      var params = new URLSearchParams(window.location.search);
      if (params.get("topics")) {
        addTopicsFromInput(
          params
            .get("topics")
            .split(",")
            .map(function (p) {
              return p.trim().replace(/-/g, " ");
            })
            .join(",")
        );
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
          var leanNow =
            (window.YoyoLeanPref && YoyoLeanPref.current()) || lean || "balanced";
          window.location.href =
            "/my?geo=" +
            encodeURIComponent(newGeo) +
            "&lean=" +
            encodeURIComponent(leanNow);
        },
      });
    }
  }

  window.YoyoMyNews = { init: init, load: load, refresh: refresh, KEY: KEY };
})();

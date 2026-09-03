(function () {
  "use strict";

  var TEAM_KEY = "yoyonews_my_teams";
  var TEAM_MAX = 12;

  var loadTeams = function () {
    try {
      var data = JSON.parse(localStorage.getItem(TEAM_KEY) || "null");
      if (!data || !Array.isArray(data.teams)) return [];
      return data.teams.filter(function (t) { return t && t.id && t.league; }).slice(-TEAM_MAX);
    } catch (e) {
      return [];
    }
  };

  var saveTeams = function (teams) {
    try {
      localStorage.setItem(TEAM_KEY, JSON.stringify({ v: 1, teams: teams.slice(-TEAM_MAX) }));
    } catch (e) {}
  };

  var teamKeyOf = function (t) {
    return String(t.league || "") + ":" + String(t.id || "");
  };

  var paintStars = function () {
    var keys = {};
    loadTeams().forEach(function (t) { keys[teamKeyOf(t)] = true; });
    document.querySelectorAll(".team-star").forEach(function (btn) {
      var on = Boolean(keys[btn.getAttribute("data-team-key")]);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      var abbr = btn.getAttribute("data-team-abbr") || "team";
      btn.textContent = (on ? "Starred " : "Star ") + abbr;
    });
  };

  var pinStarred = function () {
    var page = document.querySelector("[data-sports-page]");
    if (!page || page.classList.contains("sports-game-page")) return;
    var keys = {};
    loadTeams().forEach(function (t) { keys[teamKeyOf(t)] = true; });
    var section = document.getElementById("your-teams");
    var matches = [];
    if (Object.keys(keys).length) {
      matches = Array.prototype.filter.call(document.querySelectorAll(".score-card[data-game-id]"), function (card) {
        if (card.closest("#your-teams")) return false;
        return Array.prototype.some.call(card.querySelectorAll(".team-star"), function (btn) {
          return keys[btn.getAttribute("data-team-key")];
        });
      });
    }
    if (!matches.length) {
      if (section) section.remove();
      return;
    }
    if (!section) {
      section = document.createElement("section");
      section.id = "your-teams";
      section.className = "sports-section";
      var heading = document.createElement("div");
      heading.className = "sports-section-heading";
      var title = document.createElement("h2");
      title.textContent = "Your teams";
      var note = document.createElement("span");
      note.textContent = "This device only";
      heading.appendChild(title);
      heading.appendChild(note);
      var gridEl = document.createElement("div");
      gridEl.className = "score-grid";
      gridEl.setAttribute("data-your-teams", "");
      section.appendChild(heading);
      section.appendChild(gridEl);
      var nav = page.querySelector(".sports-leagues");
      if (nav && nav.parentNode) nav.parentNode.insertBefore(section, nav.nextSibling);
      else page.insertBefore(section, page.firstChild);
    }
    var grid = section.querySelector("[data-your-teams]");
    grid.replaceChildren();
    matches.forEach(function (card) { grid.appendChild(card.cloneNode(true)); });
  };

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest(".team-star");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    var key = btn.getAttribute("data-team-key");
    var id = btn.getAttribute("data-team-id");
    var league = btn.getAttribute("data-league");
    if (!key || !id || !league) return;
    var teams = loadTeams();
    var next = teams.filter(function (t) { return teamKeyOf(t) !== key; });
    if (next.length === teams.length) {
      next.push({
        id: id,
        name: btn.getAttribute("data-team-name"),
        abbreviation: btn.getAttribute("data-team-abbr"),
        league: league,
      });
      if (window.yoyoNewsEvent) window.yoyoNewsEvent("sports_team_star");
    }
    saveTeams(next);
    pinStarred();
    paintStars();
  });

  var TOPIC_KEY = "yoyonews_my_topics";
  var TOPIC_MAX = 20;

  var slugifyTopic = function (text) {
    var t = String(text || "").toLowerCase().trim().replace(/[#@]/g, "").replace(/[^\w\s-]/gu, "").replace(/[-\s]+/g, "-").replace(/^-+|-+$/g, "");
    return (t.slice(0, 80) || "topic").replace(/^-+|-+$/g, "");
  };

  var addMyNewsTopic = function (label) {
    label = String(label || "").trim();
    if (!label) return false;
    var slug = slugifyTopic(label);
    var data = { v: 1, topics: [] };
    try {
      data = JSON.parse(localStorage.getItem(TOPIC_KEY) || "null") || data;
      if (!data || !Array.isArray(data.topics)) data = { v: 1, topics: [] };
    } catch (e) {
      data = { v: 1, topics: [] };
    }
    if (data.topics.some(function (t) { return t && t.slug === slug; })) return "exists";
    if (data.topics.length >= TOPIC_MAX) return false;
    data.topics.push({ slug: slug, label: label, addedAt: new Date().toISOString() });
    try {
      localStorage.setItem(TOPIC_KEY, JSON.stringify({ v: 1, topics: data.topics.slice(0, TOPIC_MAX) }));
    } catch (e) { return false; }
    if (window.yoyoNewsEvent) window.yoyoNewsEvent("mynews_topic_add");
    return true;
  };

  var paintFollowButtons = function () {
    var slugs = {};
    try {
      var data = JSON.parse(localStorage.getItem(TOPIC_KEY) || "null");
      (data && data.topics ? data.topics : []).forEach(function (t) {
        if (t && t.slug) slugs[t.slug] = true;
      });
    } catch (e) {}
    document.querySelectorAll(".mynews-follow").forEach(function (btn) {
      var topic = btn.getAttribute("data-topic") || "";
      var on = Boolean(slugs[slugifyTopic(topic)]);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.textContent = (on ? "Following " : "Follow ") + topic + " in MyNews";
    });
  };

  var renderHeadlines = function (list, hits) {
    if (!list) return;
    list.replaceChildren();
    (hits || []).forEach(function (hit) {
      if (!hit || !hit.title || !hit.url) return;
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = hit.url;
      a.rel = "noopener noreferrer";
      a.target = "_blank";
      a.textContent = hit.title;
      li.appendChild(a);
      if (hit.source) {
        var src = document.createElement("small");
        src.textContent = hit.source;
        li.appendChild(src);
      }
      list.appendChild(li);
    });
    list.hidden = !list.childElementCount;
  };

  var fetchHeadlines = function (params) {
    return fetch("/api/sports/headlines?" + params, { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (payload) { return payload && payload.headlines ? payload.headlines : []; })
      .catch(function () { return []; });
  };

  var titleHasTeam = function (title, team) {
    return String(title || "").toLowerCase().indexOf(String(team || "").toLowerCase()) !== -1;
  };

  var loadRankChip = function (root, team) {
    if (!team) return;
    fetch("/api/rank?q=" + encodeURIComponent(team), { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (!data) return;
        var plats = data.platforms || {};
        var hit = null;
        Object.keys(plats).forEach(function (key) {
          if (hit) return;
          var row = plats[key];
          if (row && row.in_top && titleHasTeam(row.title, team)) hit = row;
        });
        if (!hit) return;
        var chip = root.querySelector("[data-rank-chip]");
        if (!chip || !chip.hidden) return;
        chip.hidden = false;
        chip.textContent = team + " is on today’s " + (hit.label || "lists") + (hit.rank ? " (#" + hit.rank + ")" : "") + ".";
      })
      .catch(function () {});
  };

  var loadSportsNews = function () {
    var root = document.querySelector("[data-sports-news]");
    if (!root) return;
    paintFollowButtons();
    var list = root.querySelector(".sports-headlines");
    var league = root.getAttribute("data-news-league");
    var away = root.getAttribute("data-news-away");
    var home = root.getAttribute("data-news-home");
    if (league) {
      fetchHeadlines("league=" + encodeURIComponent(league)).then(function (hits) {
        renderHeadlines(list, hits.slice(0, 8));
      });
      return;
    }
    Promise.all([
      fetchHeadlines("q=" + encodeURIComponent(away || "") + "&limit=3"),
      fetchHeadlines("q=" + encodeURIComponent(home || "") + "&limit=3"),
    ]).then(function (groups) {
      var seen = {};
      var merged = [];
      groups.forEach(function (hits) {
        hits.forEach(function (hit) {
          if (!hit || !hit.url || seen[hit.url]) return;
          seen[hit.url] = true;
          merged.push(hit);
        });
      });
      renderHeadlines(list, merged.slice(0, 3));
    });
    loadRankChip(root, away);
    loadRankChip(root, home);
  };

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest(".mynews-follow");
    if (!btn) return;
    ev.preventDefault();
    var topic = btn.getAttribute("data-topic");
    var result = addMyNewsTopic(topic);
    paintFollowButtons();
    if (result === true) {
      btn.textContent = "Following " + topic + " in MyNews";
    }
  });

  pinStarred();
  paintStars();
  loadSportsNews();

  var page = document.querySelector("[data-sports-page]");
  if (!page || !page.dataset.refreshUrl) return;

  document.querySelectorAll("time[data-local-time]").forEach(function (node) {
    var instant = new Date(node.getAttribute("datetime"));
    if (!Number.isNaN(instant.getTime())) {
      node.textContent = new Intl.DateTimeFormat(undefined, {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
      }).format(instant);
    }
  });

  var hasLiveGame = function () {
    return Boolean(document.querySelector(".score-card.is-live, .game-state.state-in_progress")) || page.dataset.liveGame === "1";
  };

  var shareLineFromGame = function (game) {
    if (game && game.share_line) return game.share_line;
    if (!game) return "";
    var away = (game.away_team && game.away_team.abbreviation) || "AWAY";
    var home = (game.home_team && game.home_team.abbreviation) || "HOME";
    var started = Boolean(game.is_live || game.is_final);
    var as = game.away_score_display;
    var hs = game.home_score_display;
    var line = started && as && as !== "—" && hs && hs !== "—"
      ? away + " " + as + " @ " + home + " " + hs
      : away + " @ " + home;
    if (game.status_text) line += ", " + game.status_text;
    return line;
  };

  var smsHref = function (text) {
    var body = encodeURIComponent(text);
    return /iPhone|iPad|iPod/i.test(navigator.userAgent) ? "sms:&body=" + body : "sms:?body=" + body;
  };

  var shareScore = function (btn) {
    var line = btn.getAttribute("data-share-line") || "";
    var card = btn.closest("[data-game-id]");
    var url = btn.getAttribute("data-share-url") || (card ? location.origin + "/sports/game/" + card.getAttribute("data-game-id") : location.href);
    if (!line) return;
    var payload = line + "\n" + url;
    var mark = function () {
      if (window.yoyoNewsEvent) window.yoyoNewsEvent("share_click");
      var prev = btn.getAttribute("data-share-label") || btn.textContent;
      btn.setAttribute("data-share-label", prev);
      btn.textContent = "Copied";
      window.setTimeout(function () { btn.textContent = prev; }, 1600);
    };
    if (navigator.share) {
      navigator.share({ title: line, text: line, url: url }).then(function () {
        if (window.yoyoNewsEvent) window.yoyoNewsEvent("share_click");
      }).catch(function () {});
      return;
    }
    if (/iPhone|iPad|iPod|Android/i.test(navigator.userAgent)) {
      if (window.yoyoNewsEvent) window.yoyoNewsEvent("share_click");
      location.href = smsHref(payload);
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(payload).then(mark).catch(function () {
        location.href = smsHref(payload);
      });
      return;
    }
    location.href = smsHref(payload);
  };

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest("[data-share]");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    shareScore(btn);
  });

  var applyOne = function (root, game) {
    var status = root.querySelector("[data-game-status]");
    var away = root.querySelector("[data-game-away-score]");
    var home = root.querySelector("[data-game-home-score]");
    if (status) {
      status.replaceChildren();
      if (game.is_live) {
        var pill = document.createElement("span");
        pill.className = "live-pill";
        pill.textContent = "Live";
        status.appendChild(pill);
        status.appendChild(document.createTextNode(" " + (game.status_text || "Live")));
      } else {
        status.textContent = game.status_text || "Updated";
      }
      status.className = "game-state state-" + (game.state || "unknown");
    }
    if (away) away.textContent = game.away_score_display || "—";
    if (home) home.textContent = game.home_score_display || "—";
    if (root.classList && root.classList.contains("score-card")) {
      root.classList.toggle("is-live", Boolean(game.is_live));
    }
    var shareBtn = root.querySelector("[data-share]");
    var line = shareLineFromGame(game);
    if (shareBtn && line) shareBtn.setAttribute("data-share-line", line);
  };

  var applyGame = function (game) {
    if (!game || !game.id) return;
    var nodes = document.querySelectorAll('[data-game-id="' + game.id + '"]');
    if (!nodes.length) return;
    nodes.forEach(function (node) { applyOne(node, game); });
  };

  var applyUpdate = function (payload) {
    var games = payload && payload.events ? payload.events : [];
    if (!games.length) return;
    games.forEach(applyGame);
  };

  var refresh = function () {
    if (document.hidden || !hasLiveGame()) return;
    fetch(page.dataset.refreshUrl, { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (payload) {
        if (!payload) return;
        applyUpdate(payload);
        window.dispatchEvent(new CustomEvent("yoyosup:sports-update", { detail: payload }));
      })
      .catch(function () { /* The rendered page remains the honest cached fallback. */ });
  };

  window.setInterval(refresh, 30000);
})();

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

  pinStarred();
  paintStars();

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

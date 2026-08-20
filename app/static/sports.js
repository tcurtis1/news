(function () {
  "use strict";

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

  var applyUpdate = function (payload) {
    var games = payload && payload.events ? payload.events : [];
    if (!games.length) return;
    var game = games[0];
    var card = Array.prototype.find.call(document.querySelectorAll("[data-game-id]"), function (node) {
      return node.getAttribute("data-game-id") === String(game.id);
    });
    var status = document.querySelector("[data-game-status]");
    var away = document.querySelector("[data-game-away-score]");
    var home = document.querySelector("[data-game-home-score]");
    if (card) {
      status = card.querySelector("[data-game-status]");
      away = card.querySelector("[data-game-away-score]");
      home = card.querySelector("[data-game-home-score]");
    }
    if (status) status.textContent = game.status_text || "Updated";
    if (away) away.textContent = game.away_score_display || "—";
    if (home) home.textContent = game.home_score_display || "—";
    if (card) {
      card.classList.toggle("is-live", Boolean(game.is_live));
      card.querySelectorAll(".game-state").forEach(function (node) {
        node.className = "game-state state-" + (game.state || "unknown");
      });
    }
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

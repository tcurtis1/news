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

  var applyGame = function (game) {
    if (!game || !game.id) return;
    var card = document.querySelector('[data-game-id="' + game.id + '"]');
    var status = card ? card.querySelector("[data-game-status]") : document.querySelector("[data-game-status]");
    var away = card ? card.querySelector("[data-game-away-score]") : document.querySelector("[data-game-away-score]");
    var home = card ? card.querySelector("[data-game-home-score]") : document.querySelector("[data-game-home-score]");
    if (status) {
      status.innerHTML = "";
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
    if (card) card.classList.toggle("is-live", Boolean(game.is_live));
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

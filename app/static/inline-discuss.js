/**
 * Expand a comment box under the story you tapped — stay on the feed.
 * Markup: <button type="button" data-discuss data-slug="topic-slug">Discuss</button>
 */
(function () {
  var NAME_KEY = "yoyonews_display_name";

  function savedName() {
    try {
      return localStorage.getItem(NAME_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  function rememberName(value) {
    try {
      var v = (value || "").trim();
      if (v) localStorage.setItem(NAME_KEY, v.slice(0, 40));
      else localStorage.removeItem(NAME_KEY);
    } catch (_) {}
  }

  function closestCard(el) {
    return el.closest("li.card, article.card, .card, li, article");
  }

  function panelHost(btn) {
    var links = btn.closest(".links");
    if (links && links.parentNode) return links.parentNode;
    var summary = btn.closest("p.summary");
    if (summary && summary.parentNode) return summary.parentNode;
    return closestCard(btn);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function likeButton(slug, comment) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "like-btn";
    btn.setAttribute("data-like", "1");
    btn.setAttribute("data-slug", slug);
    btn.setAttribute("data-id", comment.id || "");
    var n = Number(comment.like_count || 0);
    btn.appendChild(document.createTextNode("Like "));
    var count = el("span", "", n ? String(n) : "0");
    count.setAttribute("data-like-count", "1");
    btn.appendChild(count);
    return btn;
  }

  function renderList(listEl, comments, slug) {
    listEl.textContent = "";
    if (!comments || !comments.length) {
      listEl.appendChild(el("div", "empty small", "No comments yet — start the thread."));
      return;
    }
    var ol = el("ol", "comment-list");
    comments.forEach(function (c) {
      var li = el("li", "comment");
      var meta = el("div", "comment-meta");
      meta.appendChild(el("strong", "", c.name || "Anonymous"));
      if (c.created_at) {
        var time = el("time", "", String(c.created_at).slice(0, 16).replace("T", " ") + "Z");
        time.setAttribute("datetime", c.created_at);
        meta.appendChild(time);
      }
      if (c.id) meta.appendChild(likeButton(slug, c));
      li.appendChild(meta);
      li.appendChild(el("p", "comment-body", c.body || ""));
      ol.appendChild(li);
    });
    listEl.appendChild(ol);
  }

  function setDiscussCount(btn, n) {
    var badge = btn.querySelector(".discuss-count");
    n = Number(n || 0);
    if (n > 0) {
      if (!badge) {
        btn.appendChild(document.createTextNode(" "));
        badge = el("span", "discuss-count", String(n));
        btn.appendChild(badge);
      } else {
        badge.textContent = String(n);
      }
    } else if (badge) {
      badge.remove();
    }
  }

  function refreshCounts() {
    var btns = Array.prototype.slice.call(document.querySelectorAll("[data-discuss][data-slug]"));
    var slugs = [];
    btns.forEach(function (btn) {
      var slug = (btn.getAttribute("data-slug") || "").trim();
      if (slug && slugs.indexOf(slug) === -1) slugs.push(slug);
    });
    if (!slugs.length) return;
    fetch("/api/comments/counts?slugs=" + encodeURIComponent(slugs.join(",")))
      .then(function (r) {
        if (!r.ok) throw new Error("bad");
        return r.json();
      })
      .then(function (data) {
        var counts = (data && data.counts) || {};
        btns.forEach(function (btn) {
          var slug = (btn.getAttribute("data-slug") || "").trim();
          if (Object.prototype.hasOwnProperty.call(counts, slug)) {
            setDiscussCount(btn, counts[slug]);
          }
        });
      })
      .catch(function () {});
  }

  function likeComment(btn) {
    var slug = (btn.getAttribute("data-slug") || "").trim();
    var id = (btn.getAttribute("data-id") || "").trim();
    if (!slug || !id || btn.disabled) return;
    btn.disabled = true;
    fetch(
      "/api/topic/" + encodeURIComponent(slug) + "/comments/" + encodeURIComponent(id) + "/like",
      { method: "POST", headers: { Accept: "application/json" } }
    )
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        btn.disabled = false;
        var data = res.data || {};
        if (!data.ok) return;
        var span = btn.querySelector("[data-like-count]");
        if (span) span.textContent = String(data.like_count || 0);
        btn.classList.toggle("is-liked", !!data.liked);
      })
      .catch(function () {
        btn.disabled = false;
      });
  }

  function buildPanel(slug) {
    var panel = el("div", "inline-discuss");
    panel.setAttribute("data-slug", slug);
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-label", "Comments on this story");

    var status = el("p", "inline-discuss-status muted-hint", "Loading comments…");
    var list = el("div", "inline-discuss-list");
    var form = document.createElement("form");
    form.className = "comment-form inline-discuss-form";
    form.setAttribute("data-inline-discuss-form", "1");

    var hp = document.createElement("input");
    hp.type = "text";
    hp.name = "website";
    hp.className = "hp";
    hp.tabIndex = -1;
    hp.autocomplete = "off";
    hp.setAttribute("aria-hidden", "true");

    var fields = el("div", "comment-fields");
    var name = document.createElement("input");
    name.type = "text";
    name.name = "name";
    name.maxLength = 40;
    name.placeholder = "Name (optional)";
    name.autocomplete = "nickname";
    name.value = savedName();

    var body = document.createElement("textarea");
    body.name = "body";
    body.rows = 3;
    body.maxLength = 2000;
    body.required = true;
    body.placeholder = "Add a thought — keep it civil.";

    var submit = document.createElement("button");
    submit.type = "submit";
    submit.textContent = "Post comment";

    fields.appendChild(name);
    fields.appendChild(body);
    fields.appendChild(submit);
    form.appendChild(hp);
    form.appendChild(fields);

    var msg = el("p", "inline-discuss-msg t-snip");
    msg.hidden = true;

    var topic = document.createElement("a");
    topic.className = "muted-hint";
    topic.href = "/topic/" + encodeURIComponent(slug);
    topic.textContent = "Open full topic page";

    panel.appendChild(status);
    panel.appendChild(list);
    panel.appendChild(form);
    panel.appendChild(msg);
    panel.appendChild(topic);

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      submit.disabled = true;
      msg.hidden = true;
      fetch("/api/topic/" + encodeURIComponent(slug) + "/comments", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          name: name.value,
          body: body.value,
          website: hp.value,
        }),
      })
        .then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, data: data };
          });
        })
        .then(function (res) {
          submit.disabled = false;
          var data = res.data || {};
          msg.hidden = false;
          msg.textContent = data.message || (res.ok ? "Comment posted." : "Could not post.");
          msg.className =
            "inline-discuss-msg t-snip " + (data.ok ? "flash ok" : "flash error");
          if (data.ok) {
            rememberName(name.value);
            body.value = "";
            renderList(list, data.comments || [], slug);
            var n = data.comments && data.comments.length ? data.comments.length : 0;
            status.textContent = n + " comment" + (n === 1 ? "" : "s");
            var openBtn = document.querySelector(
              '[data-discuss][data-slug="' + slug.replace(/"/g, "") + '"]'
            );
            if (openBtn) setDiscussCount(openBtn, n);
          }
        })
        .catch(function () {
          submit.disabled = false;
          msg.hidden = false;
          msg.className = "inline-discuss-msg t-snip flash error";
          msg.textContent = "Could not post — try again.";
        });
    });

    return panel;
  }

  function loadComments(panel, slug) {
    var status = panel.querySelector(".inline-discuss-status");
    var list = panel.querySelector(".inline-discuss-list");
    fetch("/api/topic/" + encodeURIComponent(slug) + "/comments", {
      headers: { Accept: "application/json" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("bad");
        return r.json();
      })
      .then(function (data) {
        var comments = data.comments || [];
        status.textContent =
          comments.length + " comment" + (comments.length === 1 ? "" : "s");
        renderList(list, comments, slug);
        var openBtn = document.querySelector(
          '[data-discuss][data-slug="' + slug.replace(/"/g, "") + '"]'
        );
        if (openBtn) setDiscussCount(openBtn, comments.length);
      })
      .catch(function () {
        status.textContent = "Could not load comments.";
      });
  }

  function closeOthers(except) {
    document.querySelectorAll(".inline-discuss").forEach(function (panel) {
      if (panel === except) return;
      panel.hidden = true;
    });
    document.querySelectorAll("[data-discuss][aria-expanded='true']").forEach(function (b) {
      if (except && except.previousElementSibling === b) return;
      var card = closestCard(b);
      if (except && card && card.contains(except)) return;
      b.setAttribute("aria-expanded", "false");
    });
  }

  function toggle(btn) {
    var slug = (btn.getAttribute("data-slug") || "").trim();
    var host = panelHost(btn);
    if (!slug || !host) return;
    var existing = host.querySelector(":scope > .inline-discuss");
    if (existing) {
      var willOpen = existing.hidden;
      closeOthers(existing);
      existing.hidden = !willOpen;
      btn.setAttribute("aria-expanded", willOpen ? "true" : "false");
      if (willOpen) existing.scrollIntoView({ block: "nearest" });
      return;
    }
    var panel = buildPanel(slug);
    host.appendChild(panel);
    closeOthers(panel);
    btn.setAttribute("aria-expanded", "true");
    loadComments(panel, slug);
    panel.scrollIntoView({ block: "nearest" });
  }

  document.addEventListener("click", function (ev) {
    var like = ev.target && ev.target.closest && ev.target.closest("[data-like]");
    if (like) {
      ev.preventDefault();
      likeComment(like);
      return;
    }
    var btn = ev.target && ev.target.closest && ev.target.closest("[data-discuss]");
    if (!btn) return;
    ev.preventDefault();
    toggle(btn);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", refreshCounts);
  } else {
    refreshCounts();
  }

  window.YoyoDiscuss = {
    refreshCounts: refreshCounts,
  };
})();

/**
 * YoyoNews Progressive Photo Stream
 * Automatically fetches, caches, and streams thumbnails into view as you read and scroll.
 */
(function () {
  if (typeof window === "undefined" || !("IntersectionObserver" in window)) return;

  const STORAGE_KEY = "yoyonews_thumbs_v1";
  const BATCH_DEBOUNCE_MS = 30;
  const MAX_BATCH_SIZE = 25;
  const pendingQueue = new Map(); // url -> { card, title, rank }
  let batchTimer = null;
  const inFlight = new Set();
  const memoryCache = new Map();

  // Load persistent thumbnails from localStorage for instant 0ms paints
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (parsed && typeof parsed === "object") {
        for (const [k, v] of Object.entries(parsed)) {
          if (k && v && typeof v === "string") {
            memoryCache.set(k, v);
          }
        }
      }
    }
  } catch (e) {}

  function saveStorageDebounced() {
    try {
      const obj = {};
      let count = 0;
      for (const [k, v] of memoryCache.entries()) {
        obj[k] = v;
        count++;
        if (count >= 400) break; // keep compact
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
    } catch (e) {}
  }

  function flushBatch() {
    batchTimer = null;
    if (pendingQueue.size === 0) return;

    const itemsToFetch = [];
    for (const [url, data] of pendingQueue.entries()) {
      if (!inFlight.has(url)) {
        inFlight.add(url);
        itemsToFetch.push({ url: url, title: data.title });
        if (itemsToFetch.length >= MAX_BATCH_SIZE) break;
      }
    }

    if (itemsToFetch.length === 0) return;

    fetch("/api/thumbnails", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: itemsToFetch }),
    })
      .then((res) => (res.ok ? res.json() : { thumbnails: {} }))
      .then((data) => {
        const thumbs = data.thumbnails || {};
        let newlyCached = false;
        itemsToFetch.forEach((item) => {
          const url = item.url;
          const imgUrl = thumbs[url];
          const pending = pendingQueue.get(url);
          pendingQueue.delete(url);
          inFlight.delete(url);

          if (imgUrl && pending && pending.card) {
            memoryCache.set(url, imgUrl);
            newlyCached = true;
            injectThumbnail(pending.card, imgUrl, pending.rank);
          }
        });

        if (newlyCached) {
          saveStorageDebounced();
        }

        // If more items remain in queue, schedule next batch immediately
        if (pendingQueue.size > 0) {
          batchTimer = setTimeout(flushBatch, 20);
        }
      })
      .catch(() => {
        itemsToFetch.forEach((item) => {
          pendingQueue.delete(item.url);
          inFlight.delete(item.url);
        });
      });
  }

  function injectThumbnail(card, imgUrl, rank) {
    if (!card || card.querySelector(".story-thumb")) return;

    const thumbDiv = document.createElement("div");
    thumbDiv.className = "story-thumb";

    const img = document.createElement("img");
    img.className = "story-thumb-img photo-fade-in";
    img.src = imgUrl;
    img.alt = "";
    img.loading = "lazy";
    img.onerror = function () {
      thumbDiv.style.display = "none";
    };

    thumbDiv.appendChild(img);

    if (rank) {
      const chip = document.createElement("span");
      chip.className = "story-rank-chip";
      chip.textContent = "#" + rank;
      thumbDiv.appendChild(chip);
    }

    const content = card.querySelector(".story-content") || card.firstElementChild;
    if (content && content.parentNode === card) {
      card.insertBefore(thumbDiv, content);
    } else {
      card.prepend(thumbDiv);
    }
  }

  const observer = new IntersectionObserver(
    (entries) => {
      let needsFlush = false;
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const card = entry.target;
          observer.unobserve(card);

          if (card.querySelector(".story-thumb")) return;

          const link = card.querySelector("a[data-story-link], h2 a, h3 a") || card.querySelector("a");
          const url = card.dataset.storyUrl || (link ? link.href : "");
          const title = card.dataset.storyTitle || (link ? link.textContent.trim() : "");
          const rank = card.dataset.storyRank || "";

          if (!url || url.includes("javascript:") || url === "#") return;

          // Check instant memory cache
          if (memoryCache.has(url)) {
            injectThumbnail(card, memoryCache.get(url), rank);
            return;
          }

          pendingQueue.set(url, { card: card, title: title, rank: rank });
          needsFlush = true;
        }
      });

      if (needsFlush) {
        if (batchTimer) clearTimeout(batchTimer);
        batchTimer = setTimeout(flushBatch, BATCH_DEBOUNCE_MS);
      }
    },
    {
      rootMargin: "700px 0px 700px 0px", // pre-fetch stories 700px before user scrolls to them
      threshold: 0.01,
    }
  );

  function scanCards() {
    const cards = document.querySelectorAll(".story-card, .pulse-story, .category-card, .trending-card, li.card");
    cards.forEach((card) => {
      if (card.querySelector(".story-thumb")) return;
      const link = card.querySelector("a[data-story-link], h2 a, h3 a") || card.querySelector("a");
      const url = card.dataset.storyUrl || (link ? link.href : "");
      const rank = card.dataset.storyRank || "";
      if (url && memoryCache.has(url)) {
        injectThumbnail(card, memoryCache.get(url), rank);
        return;
      }
      if (!card.dataset.thumbObserved) {
        card.dataset.thumbObserved = "1";
        observer.observe(card);
      }
    });
  }

  // Scan initially
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scanCards);
  } else {
    scanCards();
  }

  // Listen to Load More buttons or dynamic story appends
  window.addEventListener("load", scanCards);
  document.addEventListener("click", (e) => {
    if (e.target && (e.target.id === "pulse-more" || e.target.classList.contains("load-more-btn"))) {
      setTimeout(scanCards, 50);
      setTimeout(scanCards, 300);
    }
  });

  // Watch DOM mutations for dynamic feeds
  const feedList = document.querySelector(".feed-container, #pulse-stories-list, .list");
  if (feedList && "MutationObserver" in window) {
    const mo = new MutationObserver(() => scanCards());
    mo.observe(feedList, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "style", "class"] });
  }
})();

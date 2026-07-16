/**
 * Remember preferred display name for topic comments (localStorage).
 * Empty name still posts as Anonymous on the server.
 */
(function () {
  var KEY = "yoyonews_display_name";
  var form = document.getElementById("comment-form");
  if (!form) return;
  var nameInput = form.querySelector('input[name="name"]');
  if (!nameInput) return;

  try {
    var saved = localStorage.getItem(KEY);
    if (saved && !nameInput.value) {
      nameInput.value = saved;
    }
  } catch (_) {}

  form.addEventListener("submit", function () {
    try {
      var v = (nameInput.value || "").trim();
      if (v) localStorage.setItem(KEY, v.slice(0, 40));
      else localStorage.removeItem(KEY);
    } catch (_) {}
  });

  // Optional: small hint under the name field
  if (!form.querySelector(".name-remember-hint")) {
    var hint = document.createElement("p");
    hint.className = "t-snip name-remember-hint";
    hint.textContent =
      "Name is optional (blank = Anonymous). We’ll remember it on this device.";
    var fields = form.querySelector(".comment-fields");
    if (fields && fields.parentNode) {
      fields.parentNode.insertBefore(hint, fields.nextSibling);
    }
  }
})();

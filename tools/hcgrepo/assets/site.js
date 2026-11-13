// HCG Repo: copy-to-clipboard for the source URL. No dependencies, no tracking.
(function () {
  "use strict";
  var live = document.getElementById("live");
  function announce(msg) { if (live) { live.textContent = ""; setTimeout(function () { live.textContent = msg; }, 30); } }
  function fallbackCopy(input) {
    input.focus();
    input.select();
    input.setSelectionRange(0, input.value.length);
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    return ok;
  }
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.querySelector(btn.getAttribute("data-copy"));
      if (!input) return;
      var label = btn.textContent;
      function done(ok) {
        btn.textContent = ok ? "Copied" : "Select and copy";
        announce(ok ? "Repository URL copied" : "Copy failed; the URL is selected, copy it manually");
        if (!ok) { input.focus(); input.select(); }
        setTimeout(function () { btn.textContent = label; }, 2000);
      }
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(input.value).then(function () { done(true); }, function () { done(fallbackCopy(input)); });
      } else {
        done(fallbackCopy(input));
      }
    });
  });
})();

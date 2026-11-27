// HCG Repo: copy-to-clipboard for the source URL. No dependencies, no tracking.
(function () {
  "use strict";
  var live = document.getElementById("live");
  function announce(msg) { if (live) { live.textContent = ""; setTimeout(function () { live.textContent = msg; }, 30); } }
  function textOf(el) { return (el.value !== undefined ? el.value : el.textContent).trim(); }
  function selectEl(el) {
    if (el.select) { el.focus(); el.select(); return; }
    var range = document.createRange(); range.selectNodeContents(el);
    var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  }
  function fallbackCopy(input) {
    selectEl(input);
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
        if (!ok) { selectEl(input); }
        setTimeout(function () { btn.textContent = label; }, 2000);
      }
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(textOf(input)).then(function () { done(true); }, function () { done(fallbackCopy(input)); });
      } else {
        done(fallbackCopy(input));
      }
    });
  });

  // Package discovery: search, category, environment, iOS version and access filters over the cards on this page.
  var form = document.querySelector("[data-filters]");
  if (form) {
    var cards = Array.prototype.slice.call(document.querySelectorAll(".pkg-card"));
    var sectionSel = form.querySelector('[data-filter="section"]');
    var sections = {};
    cards.forEach(function (c) { sections[c.getAttribute("data-section")] = true; });
    Object.keys(sections).sort().forEach(function (s) {
      var o = document.createElement("option"); o.value = s; o.textContent = s.replace(/_/g, " "); sectionSel.appendChild(o);
    });
    var count = form.querySelector("[data-filter-count]");
    function ver(v) { var p = String(v || "0").split("."); return (+p[0] || 0) * 100 + (+p[1] || 0); }
    function apply() {
      var q = form.querySelector('[data-filter="text"]').value.trim().toLowerCase();
      var sec = sectionSel.value, lay = form.querySelector('[data-filter="layout"]').value;
      var ios = form.querySelector('[data-filter="ios"]').value, acc = form.querySelector('[data-filter="access"]').value;
      var shown = 0;
      cards.forEach(function (c) {
        var ok = (!q || c.getAttribute("data-filter-text").indexOf(q) >= 0)
          && (!sec || c.getAttribute("data-section") === sec)
          && (!lay || (" " + c.getAttribute("data-layout") + " ").indexOf(" " + lay + " ") >= 0)
          && (!ios || !c.getAttribute("data-min-ios") || ver(c.getAttribute("data-min-ios")) <= ver(ios + ".99"))
          && (!acc || c.getAttribute("data-access") === acc);
        c.hidden = !ok;
        if (ok) shown++;
      });
      count.textContent = shown + " of " + cards.length + " packages shown (stable and testing channel)";
    }
    form.addEventListener("input", apply);
    form.addEventListener("change", apply);
    apply();
  }
})();

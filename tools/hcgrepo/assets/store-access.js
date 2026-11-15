// HCG Repo product pages: asks HCG Store what the signed-in visitor may do with this product.
// No dependencies, no tracking, no inline code. Without JavaScript, or when the check fails, the
// page keeps its plain "Sign in to check access" and "View in HCG Store" links.
//
// Contract (docs/STORE_INTEGRATION.md): GET <store>api/v1/repo/products/<key>/access with the
// store's own session cookie, answering
//   { state: "signed-out" | "not-owned" | "owned" | "not-for-sale" | "unavailable",
//     buy_url?: string, store_url: string, downloads_available: boolean, message?: string }
// Only URLs that start with the configured store base are ever used. No token is put in a URL.
(function () {
  "use strict";
  var box = document.querySelector("[data-store-access]");
  if (!box || !window.fetch) return;
  var base = box.getAttribute("data-store-base") || "";
  var accessUrl = box.getAttribute("data-access-url") || "";
  var storeUrl = box.getAttribute("data-store-url") || "";
  var repoUrl = box.getAttribute("data-repo-url") || "";
  var name = box.getAttribute("data-product-name") || "this product";
  var actions = box.querySelector("[data-store-actions]");
  var status = box.querySelector("[data-store-status]");
  var NO_DOWNLOADS = "Downloads are not available yet.";
  var UNCHECKED = "Access could not be checked right now.";
  var TIMEOUT_MS = 5000;

  function storeOrigin() {
    try { return new URL(base).origin; } catch (e) { return ""; }
  }
  // A URL from the store's response is used only if it is under the configured store base.
  function fromStore(u) {
    if (typeof u !== "string" || u.indexOf(base) !== 0 || /[\s"'<>\\]/.test(u)) return null;
    try {
      var parsed = new URL(u);
      if (parsed.origin !== storeOrigin() || parsed.username || parsed.password) return null;
    } catch (e) {
      return null;
    }
    return u;
  }
  if (base.indexOf("https://") !== 0 || !storeOrigin() || !actions || !fromStore(accessUrl) || !fromStore(storeUrl)) return;

  function link(text, href, primary) {
    var a = document.createElement("a");
    a.className = "btn " + (primary ? "btn-primary" : "btn-metal");
    a.href = href;
    a.textContent = text;
    return a;
  }
  function disabled(text) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn-metal";
    b.disabled = true;
    b.textContent = text;
    return b;
  }
  function badge(text) {
    var s = document.createElement("span");
    s.className = "badge badge-ok";
    s.textContent = text;
    return s;
  }
  function replace(nodes) {
    while (actions.firstChild) actions.removeChild(actions.firstChild);
    nodes.forEach(function (n) { actions.appendChild(n); });
  }
  function say(text) {
    if (status) status.textContent = text;
  }
  function note(data) {
    return data && typeof data.message === "string" ? " " + data.message.slice(0, 300) : "";
  }

  function render(data) {
    var page = fromStore(data.store_url) || storeUrl;
    switch (data.state) {
      case "signed-out":
        say("Sign in to HCG Store to see whether you can get " + name + "." + note(data));
        return;
      case "not-owned":
        replace([link("Buy Product", fromStore(data.buy_url) || page, true), link("View in HCG Store", page, false)]);
        say(note(data).trim());
        return;
      case "owned":
        if (data.downloads_available === true) {
          replace([badge("Owned"), link("Get from HCG Repo", "../../#install", true), link("View in HCG Store", page, false)]);
          say("Add the HCG Repo source " + repoUrl + " to Sileo, then install " + name + " from it in Sileo." + note(data));
        } else {
          replace([badge("Owned"), disabled("Get from HCG Repo"), link("View in HCG Store", page, false)]);
          say(NO_DOWNLOADS + note(data));
        }
        return;
      case "not-for-sale":
        replace([disabled("Not for sale yet"), link("View in HCG Store", page, false)]);
        say(note(data).trim());
        return;
      default:
        say(UNCHECKED);
    }
  }

  var controller = window.AbortController ? new AbortController() : null;
  var timer = setTimeout(function () {
    if (controller) controller.abort();
    finish(null);
  }, TIMEOUT_MS);
  var done = false;
  function finish(data) {
    if (done) return;
    done = true;
    clearTimeout(timer);
    box.removeAttribute("aria-busy");
    if (data && typeof data === "object" && typeof data.state === "string") render(data);
    else say(UNCHECKED);
  }

  box.setAttribute("aria-busy", "true");
  fetch(accessUrl, {
    method: "GET",
    mode: "cors",
    credentials: "include",
    cache: "no-store",
    redirect: "error",
    headers: { Accept: "application/json" },
    signal: controller ? controller.signal : undefined
  }).then(function (res) {
    var type = res.headers.get("Content-Type") || "";
    if (!res.ok || type.indexOf("json") === -1) return null;
    return res.json();
  }).then(finish, function () { finish(null); });
})();

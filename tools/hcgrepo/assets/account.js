/* HCG Repo › My Library. Reads the signed-in account from HCG Store (read-only, credentialed CORS) and renders it.
 * Display only: it grants nothing, stores nothing and never sees a password or token. Every download is decided by
 * HCG Store at the moment it is requested. If the Store cannot be reached, it says so and links to HCG Store. */
(function () {
  "use strict";
  var root = document.querySelector("[data-profile]");
  if (!root) return;
  var store = root.getAttribute("data-store-base");
  var state = root.querySelector("[data-profile-state]");
  var signedIn = root.querySelector("[data-profile-signed-in]");

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function link(href, text, cls) { var a = el("a", cls || "btn btn-metal", text); a.href = href; return a; }
  function show(html, actions) {
    state.textContent = "";
    state.appendChild(el("p", null, html));
    if (actions) { var d = el("div", "actions"); actions.forEach(function (a) { d.appendChild(a); }); state.appendChild(d); }
  }
  function money(minor, cur) {
    try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur }).format(minor / 100); }
    catch (e) { return (minor / 100).toFixed(2) + " " + cur; }
  }

  // Versions HCG Repo currently offers, from this site's own index (same origin).
  function indexVersions(repoUrl) {
    return fetch(repoUrl + "Packages", { credentials: "omit" }).then(function (r) { return r.ok ? r.text() : ""; })
      .then(function (t) {
        var out = {};
        t.split("\n\n").forEach(function (st) {
          var p = /^Package: (.+)$/m.exec(st), v = /^Version: (.+)$/m.exec(st);
          if (p && v) out[p[1]] = v[1];
        });
        return out;
      }).catch(function () { return {}; });
  }

  function render(d, versions) {
    state.hidden = true;
    signedIn.hidden = false;
    root.querySelector("[data-profile-name]").textContent = (d.user && d.user.name) || "HCG account";
    root.querySelector("[data-profile-email]").textContent = (d.user && d.user.email) || "—";
    root.querySelector("[data-profile-downloads]").textContent = d.ownershipUnavailable
      ? "Some ownership could not be checked right now; try again shortly."
      : (d.downloadsMessage || "");
    var list = root.querySelector("[data-profile-library]");
    (d.library || []).forEach(function (o) {
      var li = el("li", "card pkg-card");
      li.appendChild(el("h3", null, o.name + (o.editionName ? " — " + o.editionName : "")));
      li.appendChild(el("p", "pkg-meta", (o.acquisition === "licensed" ? "Licensed" : "Free with your account") + " · " + o.licence));
      if (o.package) li.appendChild(el("p", "pkg-id", o.package));
      var offered = o.package && versions[o.package];
      li.appendChild(el("p", null, offered ? "Version in HCG Repo: " + offered
        : "Not in the HCG Repo index yet" + (o.currentRelease ? " (HCG Store release " + o.currentRelease.version + ")" : "")));
      var acts = el("div", "actions");
      if (o.package && offered && o.state === "downloads-available") acts.appendChild(link("sileo://package/" + o.package, "Open in Sileo", "btn btn-primary"));
      if (o.hcgRepoKey) acts.appendChild(link("../products/" + o.hcgRepoKey + "/", "Product page"));
      li.appendChild(acts);
      list.appendChild(li);
    });
    root.querySelector("[data-profile-library-empty]").hidden = (d.library || []).length > 0;
    var pur = root.querySelector("[data-profile-purchases]");
    if (!(d.purchases || []).length) {
      pur.appendChild(el("p", "muted", "No purchases."));
    } else {
      var t = el("table", "kv-table"), head = el("tr");
      ["Date", "Item", "Amount", "Status"].forEach(function (h) { head.appendChild(el("th", null, h)); });
      t.appendChild(head);
      d.purchases.forEach(function (p) {
        var tr = el("tr");
        tr.appendChild(el("td", null, new Date(p.createdAt).toLocaleDateString()));
        tr.appendChild(el("td", null, p.title + (p.testOnly && !/\(TEST\)/.test(p.title) ? " (TEST)" : "")));
        tr.appendChild(el("td", null, money(p.amount, p.currency)));
        tr.appendChild(el("td", null, p.status));
        t.appendChild(tr);
      });
      pur.appendChild(t);
    }
    var dev = root.querySelector("[data-profile-devices]");
    (d.connectedPackageManagers || []).forEach(function (x) {
      dev.appendChild(el("li", null, x.label + " — connected " + new Date(x.connectedAt).toLocaleDateString()));
    });
    if (!(d.connectedPackageManagers || []).length) dev.appendChild(el("li", "muted", "No package manager is signed in."));
    root.querySelector("[data-profile-devices-note]").textContent = d.devicesNote || "";
  }

  fetch(root.getAttribute("data-profile-url"), { credentials: "include", headers: { accept: "application/json" } })
    .then(function (r) {
      if (r.status === 404) throw new Error("not-deployed");
      return r.json().then(function (d) { return { status: r.status, d: d }; });
    })
    .then(function (res) {
      var d = res.d;
      if (res.status === 503 || d.unavailable) {
        show("Your HCG account could not be checked right now. Nothing has changed; try again shortly.", [link(store + "account", "Open HCG Store")]);
        return;
      }
      if (!d.signedIn) {
        show("Sign in with your HCG account to see your packages, licences and purchases. Sign-in happens on HCG Store with HCG Connect; come back to this page afterwards.",
          [link(d.sign_in_url || store + "auth/login", "Sign in with HCG Connect", "btn btn-primary"), link(store, "Open HCG Store")]);
        return;
      }
      return indexVersions(root.getAttribute("data-repo-url")).then(function (v) { render(d, v); });
    })
    .catch(function () {
      show("Your library is managed by HCG Store. It cannot be shown here right now.", [link(store + "account", "Open my account in HCG Store", "btn btn-primary")]);
    });
})();

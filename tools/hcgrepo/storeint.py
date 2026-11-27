"""HCG Store integration: status, publication plan and controlled rollback (docs/STORE_PROVIDER.md).

Read-only. Everything comes from the real sources: config/repo.toml and config/products.toml, repo-lock.json and the
recorded provenance, `bin/hcgrepo validate`'s checks, Git history, and HCG Store's own public endpoints
(`<store>api/v1/health`, `<provider>info`, `<store>api/public/v1/published`, `<store>api/public/v1/release-resolution/
<product>`) plus, when a local HCG Store checkout is given, its catalogue (for products HCG Repo does not map).
Nothing here imports, publishes, edits configuration or calls an authenticated Store API. HCG Store stays the only
authority for prices, purchases and entitlements.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .config import Report, RepoConfig, entry_channel, load_lock, load_package_meta

TIMEOUT = 15


# ---------------------------------------------------------------------------- HTTP (GET only)

def http_get(url: str, timeout: int = TIMEOUT) -> tuple[int, bytes, str]:
    """(status, body, error). HTTPS trusts the system store, or HCG_STORE_CA_FILE (TEST environment only)."""
    host = urlsplit(url).hostname or ""
    handlers: list = []
    if host in ("127.0.0.1", "localhost"):
        handlers.append(urllib.request.ProxyHandler({}))
    cafile = os.environ.get("HCG_STORE_CA_FILE") or os.environ.get("SSL_CERT_FILE") or None
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "hcgrepo-store-integration"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read(1 << 22), ""
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(1 << 16), ""
    except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
        return 0, b"", str(getattr(exc, "reason", exc))


def _json(body: bytes):
    try:
        return json.loads(body or b"null")
    except ValueError:
        return None


# ---------------------------------------------------------------------------- pieces

def provider_section(cfg: RepoConfig, fetch=http_get, live: bool = True) -> dict:
    pp = cfg.payment_provider
    out = {
        "url": pp.url, "advertise": pp.advertise, "advertised_url": cfg.payment_endpoint, "trusted_hosts": list(pp.trusted_hosts),
        "approval": ({"approved_by": pp.approved_by, "date": pp.date, "record": pp.record} if pp.approved_by else None),
        "api": None,
    }
    if live and pp.url:
        st, body, err = fetch(pp.url + "info")
        info = _json(body) if st == 200 else None
        ok = isinstance(info, dict) and bool(info.get("name"))
        out["api"] = {"endpoint": pp.url + "info", "http_status": st, "ok": ok, "error": err or (None if ok else _err(body)),
                      "name": info.get("name") if ok else None, "description": info.get("description") if ok else None}
    return out


def _err(body: bytes) -> str | None:
    d = _json(body)
    if isinstance(d, dict):
        e = d.get("error")
        return e.get("message") if isinstance(e, dict) else (e if isinstance(e, str) else None)
    return None


def store_health(cfg: RepoConfig, fetch=http_get, live: bool = True) -> dict:
    if not live:
        return {"endpoint": cfg.store_base_url + "api/v1/health", "checked": False}
    st, body, err = fetch(cfg.store_base_url + "api/v1/health")
    h = _json(body) if st == 200 else None
    h = h if isinstance(h, dict) else {}
    authority = h.get("entitlementAuthority")
    return {
        "endpoint": cfg.store_base_url + "api/v1/health", "checked": True, "http_status": st, "error": err or None,
        "ok": h.get("status") == "ok", "mode": h.get("mode"), "hcg_connect": h.get("hcgConnect"),
        "entitlement_authority": authority, "payments": h.get("payments"), "protected_downloads": h.get("protectedDownloads"),
        "publishing": h.get("publishing"),
        # HCG Store reports which authority it uses; it checks that authority on every protected request (and fails
        # closed when it cannot be reached). HCG Repo cannot query it without an account.
        "entitlements_available": bool(h.get("status") == "ok" and authority not in (None, "none") and h.get("hcgConnect") == "configured"),
    }


def associations(cfg: RepoConfig, lock: dict | None = None) -> list[dict]:
    from .repo import commercial_gate, protected_packages
    lock = lock or load_lock(cfg)
    prot = {(p.info.package, p.info.version, p.info.architecture): p for p in protected_packages(cfg)}
    rows = []
    for prod in cfg.products:
        builds = []
        for e in lock["packages"]:
            if e["package"] not in prod.package_ids:
                continue
            p = prot.get((e["package"], e["version"], e["architecture"]))
            status = e.get("status", "published")
            indexed = (status == "published") or bool(
                p and p.store_release and cfg.payment_endpoint and entry_channel(e) == "stable" and commercial_gate(cfg, p))
            builds.append({"package": e["package"], "version": e["version"], "architecture": e["architecture"],
                           "channel": entry_channel(e), "status": status, "sha256": e["sha256"],
                           "store_release": p.store_release if p else "", "indexed": indexed})
        c = prod.commercial
        rows.append({
            "key": prod.key, "name": prod.name, "store_product": prod.store_slug, "store_edition": prod.store_edition,
            "packages": list(prod.package_ids), "access": cfg.product_level(prod) or "undeclared",
            "commercial": ({"approved_version": c.approved_version, "installable": c.installable,
                            "settings": dict(c.settings)} if c else None),
            "builds": builds,
        })
    return rows


def counts(cfg: RepoConfig, lock: dict | None = None) -> dict:
    lock = lock or load_lock(cfg)
    by_level: dict[str, set] = {"public": set(), "connect": set(), "paid": set()}
    versions = {"public": 0, "connect": 0, "paid": 0}
    for e in lock["packages"]:
        if e.get("status", "published") == "withdrawn":
            continue
        lvl = load_package_meta(cfg, e["package"]).access or "public"
        if e.get("status") == "published" or e.get("status") is None:
            lvl_eff = "public"        # a published (served) file is free by construction (free demo or public)
        else:
            lvl_eff = lvl if lvl in ("connect", "paid") else "paid"
        by_level[lvl_eff].add(e["package"])
        versions[lvl_eff] += 1
    return {"free_packages": len(by_level["public"]), "free_versions": versions["public"],
            "paid_packages": len(by_level["paid"]), "paid_versions": versions["paid"],
            "account_packages": len(by_level["connect"]), "account_versions": versions["connect"]}


def store_catalogue(checkout: Path | None) -> dict | None:
    if not checkout:
        return None
    p = Path(checkout) / "catalogue" / "products.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def unmapped(cfg: RepoConfig, catalogue: dict | None, fetch=http_get, live: bool = True) -> list[dict]:
    out = []
    repo_keys = {p.key: p for p in cfg.products}
    repo_pkgs = {pkg for p in cfg.products for pkg in p.package_ids}
    if catalogue:
        cat = {p["id"]: p for p in catalogue.get("products", [])}
        for p in catalogue.get("products", []):
            if p.get("visibility") == "draft":
                continue
            for link in p.get("hcgRepo") or []:
                if link.get("key") not in repo_keys:
                    out.append({"side": "store", "store_product": p["id"], "edition": link.get("editionId"),
                                "reason": f"HCG Store links to HCG Repo product {link.get('key')!r}, which config/products.toml does not define"})
            for e in p.get("editions", []):
                for i in e.get("identifiers", []):
                    if i.get("kind") == "debian-package" and i.get("value") not in repo_pkgs:
                        out.append({"side": "store", "store_product": p["id"], "edition": e["id"], "package": i.get("value"),
                                    "reason": "Debian package in the HCG Store catalogue with no HCG Repo product"})
        for prod in cfg.products:
            if prod.store_slug and prod.store_slug not in cat:
                out.append({"side": "repo", "key": prod.key, "store_product": prod.store_slug,
                            "reason": "store_slug is not a product in the HCG Store catalogue"})
    elif live:
        for prod in cfg.products:
            if not prod.store_slug or prod.key == "test-fixtures":
                continue
            st, _, err = fetch(f"{cfg.store_base_url}api/public/v1/release-resolution/{prod.store_slug}")
            if st == 404:
                out.append({"side": "repo", "key": prod.key, "store_product": prod.store_slug,
                            "reason": "HCG Store does not know this product (release-resolution 404)"})
            elif st != 200:
                out.append({"side": "unknown", "key": prod.key, "store_product": prod.store_slug,
                            "reason": f"could not check with HCG Store (HTTP {st} {err})".strip()})
    for prod in cfg.products:
        if prod.key != "test-fixtures" and not prod.store_slug:
            out.append({"side": "repo", "key": prod.key, "reason": "no store_slug: not associated with an HCG Store product"})
    return out


def invalid_metadata(cfg: RepoConfig) -> list[dict]:
    """Errors and warnings from the same checks as `bin/hcgrepo validate` (pool, lock, metadata, commercial gates)."""
    from .repo import check_repository, load_pool
    r = Report("repository")
    pkgs = load_pool(cfg, r)
    check_repository(cfg, pkgs, r)
    return [{"level": lvl, "code": code, "message": msg} for lvl, code, msg in r.items]


def history(cfg: RepoConfig, limit: int = 25) -> list[dict]:
    """Integration and publication history: commits that changed the lock, product mapping or provider settings,
    the Store links recorded in the lock, and the owner approvals in config."""
    ev = []
    try:
        out = subprocess.run(["git", "-C", str(cfg.root), "log", f"-{limit}", "--format=%H%x09%cI%x09%s", "--",
                              "repo-lock.json", "config/products.toml", "config/repo.toml"],
                             capture_output=True, text=True, timeout=20).stdout
        for line in out.splitlines():
            h, at, subj = line.split("\t", 2)
            ev.append({"at": at, "kind": "commit", "subject": subj, "ref": h[:12]})
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    from .repo import load_provenance
    for e in load_lock(cfg)["packages"]:
        prov = load_provenance(cfg, e["filename"])
        link = prov.get("hcg_store") or (prov if prov.get("source") == "hcg-store" else None)
        if link:
            ev.append({"at": e.get("imported_at") or "", "kind": "store-record",
                       "subject": f"{e['package']} {e['version']} linked to HCG Store release {link.get('store_release')}",
                       "ref": link.get("store_artifact")})
    for p in cfg.products:
        for a in (p.commercial.approvals if p.commercial else []):
            ev.append({"at": a.get("date", ""), "kind": "owner-approval",
                       "subject": f"{p.key}: {a.get('setting')} = {a.get('value')} (approved by {a.get('approved_by')})",
                       "ref": a.get("record", "")[:120]})
    pp = cfg.payment_provider
    if pp.approved_by:
        ev.append({"at": pp.date, "kind": "owner-approval", "subject": f"payment provider advertise = {pp.advertise} "
                   f"(approved by {pp.approved_by})", "ref": pp.record[:120]})
    return sorted(ev, key=lambda x: x["at"] or "", reverse=True)


# ---------------------------------------------------------------------------- publication plan

def publication_plan(cfg: RepoConfig, feed: dict | None) -> list[dict]:
    """What HCG Store currently publishes for HCG Repo's mapped products, against what HCG Repo has recorded.

    Never acts. Each row says what a maintainer does next, with the existing reviewed commands."""
    lock = load_lock(cfg)
    from .repo import protected_packages
    recorded = {}
    for p in protected_packages(cfg):
        recorded.setdefault(p.info.sha256, p)
    public = {e["sha256"]: e for e in lock["packages"]}
    rows = []
    products = (feed or {}).get("products") or {}
    for prod in cfg.products:
        if not prod.store_slug:
            continue
        for rel in products.get(prod.store_slug, []):
            if prod.store_edition and rel.get("editionId") != prod.store_edition:
                continue
            for f in rel.get("files", []):
                if f.get("format") != "deb":
                    continue
                sha = f.get("sha256", "")
                base = {"product": prod.key, "store_release": rel.get("releaseId"), "store_artifact": f.get("id"),
                        "version": rel.get("version"), "channel": rel.get("channel"), "source": rel.get("source"),
                        "sha256": sha}
                p = recorded.get(sha)
                if p and p.store_release:
                    c = prod.commercial
                    if c and c.approved_version and c.approved_version != p.info.version:
                        rows.append({**base, "state": "recorded-not-current",
                                     "next": f"HCG Store publishes {p.info.version}; HCG Repo's approved_version is "
                                             f"{c.approved_version}. Align them (store rollback/forward) in a reviewed change"})
                    else:
                        note = "" if p.store_release == rel.get("releaseId") else (
                            f" (same bytes; HCG Repo recorded them from Store release {p.store_release})")
                        rows.append({**base, "state": "recorded", "next": "none" + note})
                elif sha in public:
                    rows.append({**base, "state": "conflict",
                                 "next": "these bytes are recorded as a public file in HCG Repo; a Store-held build must not be public"})
                else:
                    rows.append({**base, "state": "new",
                                 "next": ("record it: save the Store's release record (GET /api/v1/publishing/releases/"
                                          f"{rel.get('releaseId')}) and the reviewed bytes, then bin/hcgrepo import-store-release "
                                          f"--release <record.json> --artifact {f.get('id') or '<artifact id>'} --deb <file>; "
                                          "review in a pull request")})
    # Store-linked builds whose exact bytes HCG Store does not currently serve (superseded, disabled, withdrawn, revoked).
    live = {f.get("sha256") for rels in products.values() for r in rels for f in r.get("files", [])}
    for p in protected_packages(cfg):
        if p.store_release and feed is not None and p.info.sha256 not in live:
            prod = cfg.product_for_package(p.info.package)
            indexed = bool(prod and prod.commercial and prod.commercial.approved_version == p.info.version)
            rows.append({"product": prod.key if prod else "", "store_release": p.store_release, "version": p.info.version,
                         "sha256": p.info.sha256, "state": "not-published-in-store",
                         "next": ("HCG Store does not publish this release now: Sileo would be refused. Roll back/forward "
                                  "approved_version, or withdraw it (bin/hcgrepo withdraw) if the Store withdrew it")
                         if indexed else "kept for history; not indexed"})
    return rows


def fetch_feed(cfg: RepoConfig, fetch=http_get) -> tuple[dict | None, str]:
    """What HCG Store currently serves for each product HCG Repo maps, shaped like hcg.store.published/1.

    Source: GET <store>api/public/v1/release-resolution/<product> (the current release a person published in HCG Store,
    or the catalogue's current release), joined with GET <store>api/public/v1/published for artifact ids. Public,
    metadata only. None when HCG Store cannot be read."""
    st, body, err = fetch(cfg.store_base_url + "api/public/v1/published")
    pub = _json(body) if st == 200 else None
    if not (isinstance(pub, dict) and pub.get("format") == "hcg.store.published/1"):
        return None, err or f"published feed HTTP {st}"
    ids = {f.get("sha256"): f.get("id") for rels in (pub.get("products") or {}).values() for r in rels for f in r.get("files", [])}
    products: dict[str, list] = {}
    for slug in sorted({p.store_slug for p in cfg.products if p.store_slug and p.key != "test-fixtures"}):
        st, body, err = fetch(f"{cfg.store_base_url}api/public/v1/release-resolution/{slug}")
        if st == 404:
            continue                                  # reported as unmapped
        d = _json(body) if st == 200 else None
        if not (isinstance(d, dict) and d.get("format") == "hcg.store.release-resolution/1"):
            return None, err or f"release-resolution {slug} HTTP {st}"
        rels = {r["id"]: r for r in d.get("releases", [])}
        grouped: dict[tuple, dict] = {}
        for o in d.get("options", []):
            r = rels.get(o.get("releaseId"), {})
            g = grouped.setdefault((o.get("releaseId"), o.get("editionId")), {
                "releaseId": o.get("releaseId"), "editionId": o.get("editionId"), "version": r.get("version"),
                "channel": r.get("channel"), "source": r.get("source"), "files": []})
            g["files"].append({"id": ids.get(o.get("sha256")), "fileName": o.get("fileName"), "format": o.get("format"),
                               "sha256": o.get("sha256"), "architectures": o.get("architectures")})
        if grouped:
            products[slug] = list(grouped.values())
    return {"format": "hcg.repo.store-current/1", "products": products}, ""


# ---------------------------------------------------------------------------- controlled rollback

def rollback_check(cfg: RepoConfig, product_key: str, to_version: str, feed: dict | None) -> tuple[Report, str]:
    """Whether `approved_version` of a commercial product may move to `to_version`, and the exact reviewed edit.

    Changes nothing: the edit is made in config/products.toml with an owner approval record, in a pull request
    (docs/COMMERCIAL.md). Bytes are never changed; only which recorded build is indexed."""
    from .repo import protected_packages
    r = Report(f"rollback {product_key} → {to_version}")
    prod = next((p for p in cfg.products if p.key == product_key), None)
    if prod is None or prod.commercial is None:
        r.error("rollback-product", f"{product_key} is not a commercial product in config/products.toml")
        return r, ""
    builds = [p for p in protected_packages(cfg) if p.info.package in prod.package_ids and p.info.version == to_version]
    lock = load_lock(cfg)
    entries = [e for e in lock["packages"] if e["package"] in prod.package_ids and e["version"] == to_version]
    if not entries:
        r.error("rollback-not-recorded", f"{to_version} is not recorded in repo-lock.json")
    for e in entries:
        if e.get("status") != "protected" or entry_channel(e) != "stable":
            r.error("rollback-not-protected", f"{e['filename']} is {entry_channel(e)}/{e.get('status', 'published')}; "
                                               "only a protected stable build can be the commercial version")
    if entries and not builds:
        r.error("rollback-no-record", f"{to_version} has no recorded control data")
    for p in builds:
        if not p.store_release:
            r.error("rollback-no-store-link", f"{to_version} has no HCG Store release link (import-store-release)")
        elif feed is None:
            r.warn("rollback-store-unchecked", "HCG Store's published releases could not be read: check that HCG Store "
                                               f"publishes {p.store_release} as current before merging")
        else:
            live = {f.get("sha256") for rels in (feed.get("products") or {}).values() for x in rels for f in x.get("files", [])}
            if p.info.sha256 not in live:
                r.error("rollback-store-not-current", f"HCG Store does not currently serve these bytes ({p.store_release}): restore it "
                        "as current in HCG Store first (Control Center → release → Restore), or Sileo would be refused")
    if prod.commercial.approved_version == to_version:
        r.warn("rollback-noop", f"{to_version} is already the approved version")
    edit = (f'# config/products.toml, product "{product_key}", [products.commercial]\n'
            f'approved_version = "{to_version}"\n\n[[products.commercial.approvals]]\nsetting = "approved_version"\n'
            f'value = "{to_version}"\napproved_by = "<owner>"\ndate = "<YYYY-MM-DD>"\n'
            f'record = "<the owner\'s approval of the rollback, verbatim>"\n')
    return r, edit


# ---------------------------------------------------------------------------- everything

def status(cfg: RepoConfig, live: bool = True, store_checkout: Path | None = None, fetch=http_get) -> dict:
    lock = load_lock(cfg)
    feed, feed_err = fetch_feed(cfg, fetch) if live else (None, "not checked")
    cat = store_catalogue(store_checkout or (Path(os.environ["HCG_STORE_CHECKOUT"]) if os.environ.get("HCG_STORE_CHECKOUT") else None))
    return {
        "format": "hcg.repo.store-integration/1",
        "store": cfg.store_base_url,
        "provider": provider_section(cfg, fetch, live),
        "store_health": store_health(cfg, fetch, live),
        "counts": counts(cfg, lock),
        "associations": associations(cfg, lock),
        "unmapped": unmapped(cfg, cat, fetch, live),
        "catalogue_source": "local HCG Store checkout" if cat else ("HCG Store release-resolution (live)" if live else "not checked"),
        "invalid_metadata": invalid_metadata(cfg),
        "publication_plan": publication_plan(cfg, feed) if feed is not None else [],
        "published_feed": "ok" if feed is not None else feed_err,
        "history": history(cfg),
    }


def render_status(d: dict) -> str:
    pv, h, c = d["provider"], d["store_health"], d["counts"]
    api = pv.get("api") or {}
    lines = [
        f"HCG Store            {d['store']}",
        f"Payment provider     {pv['url'] or '(none)'}",
        f"  advertised         {'YES: ' + pv['advertised_url'] if pv['advertise'] else 'no (payment_endpoint is not published)'}",
        f"  trusted hosts      {', '.join(pv['trusted_hosts']) or '(none)'}",
        f"  owner approval     {pv['approval']['approved_by'] + ' ' + pv['approval']['date'] if pv['approval'] else 'none'}",
        f"  provider API       {('OK ' + str(api.get('name'))) if api.get('ok') else ('NOT AVAILABLE (HTTP ' + str(api.get('http_status')) + ') ' + str(api.get('error') or '')) if api else 'not checked'}",
        f"Store health         {('ok, mode ' + str(h.get('mode'))) if h.get('ok') else ('not checked' if not h.get('checked') else 'NOT OK (HTTP ' + str(h.get('http_status')) + ')')}",
    ]
    if h.get("checked"):
        lines += [f"  HCG Connect        {h.get('hcg_connect')}",
                  f"  entitlements       {h.get('entitlement_authority')} ({'available' if h.get('entitlements_available') else 'NOT available'})",
                  f"  payments           {h.get('payments')}",
                  f"  protected downl.   {h.get('protected_downloads')}",
                  f"  publishing         {h.get('publishing')}"]
    lines += [f"Packages             free {c['free_packages']} ({c['free_versions']} versions) · paid {c['paid_packages']} "
              f"({c['paid_versions']}) · account-required {c['account_packages']} ({c['account_versions']})",
              "Associations"]
    for a in d["associations"]:
        if a["key"] == "test-fixtures":
            continue
        idx = [f"{b['version']}{'*' if b['indexed'] else ''}" + (f"[{b['store_release']}]" if b["store_release"] else "") for b in a["builds"]]
        store = f"{a['store_product'] or '-'}/{a['store_edition'] or '-'}"
        lines.append(f"  {a['key']:<24} {store:<26} {a['access']:<8} {', '.join(idx) or 'no builds'}")
    lines.append("  (* = in the public index; [rel-…] = linked HCG Store release)")
    lines.append(f"Unmapped ({d['catalogue_source']})")
    lines += [f"  {u['side']:<6} {u.get('store_product') or u.get('key')}: {u['reason']}" for u in d["unmapped"]] or ["  none"]
    inv = [m for m in d["invalid_metadata"] if m["level"] == "error"]
    lines.append(f"Invalid metadata     {len(inv)} error(s), {len(d['invalid_metadata']) - len(inv)} warning(s)")
    lines += [f"  {m['code']}: {m['message']}" for m in inv]
    lines.append(f"Store → HCG Repo     current releases: {d['published_feed']}")
    lines += [f"  {r['state']:<22} {r['product']} {r['version']} {r.get('store_release')}: {r['next']}" for r in d["publication_plan"]] or ["  nothing published for HCG Repo products"]
    lines.append("History (latest)")
    lines += [f"  {e['at'][:19]:<19} {e['kind']:<15} {e['subject']}" for e in d["history"][:10]]
    return "\n".join(lines)

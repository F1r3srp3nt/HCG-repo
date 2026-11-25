"""Command-line interface: `bin/hcgrepo <command>` (or `python3 -m tools.hcgrepo`)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .config import ConfigError, Report, load_config, load_lock, load_package_meta
from .debinspect import InspectError, inspect_deb, validate_deb


def _print_report(r: Report, ok_msg: str) -> int:
    if r.items:
        print(r.render())
    if r.ok:
        print(f"OK: {ok_msg}" + (f" ({len(r.warnings)} warnings)" if r.warnings else ""))
        return 0
    print(f"FAILED: {len(r.errors)} errors, {len(r.warnings)} warnings")
    return 1


def cmd_inspect(a, cfg) -> int:
    try:
        info = inspect_deb(Path(a.deb))
    except InspectError as exc:
        print(f"REJECTED: {exc}")
        return 1
    meta = load_package_meta(cfg, info.package)
    r = validate_deb(info, cfg, meta)
    evidence = None
    if getattr(a, "deep", False):
        from .deepcheck import deep_validate
        deep, evidence = deep_validate(info, lifecycle=not a.no_lifecycle)
        r.extend(deep)
    if a.json:
        print(json.dumps({"summary": info.summary(), "control": dict(info.control),
                          "problems": [{"level": l, "code": c, "message": m} for l, c, m in r.items],
                          "evidence": evidence}, indent=2))
        return 0 if r.ok else 1
    print(f"{info.path.name}: {info.size} bytes, sha256 {info.sha256}")
    print(info.control_text.rstrip())
    s = info.summary()
    for app in s["apps"]:
        print(f"app: {app['path']} id={app['bundle_id']} exec={app['executable']} min_os={app['minimum_os']}")
    for b in s["binaries"]:
        print(f"binary: {b['path']} arches={b['arches']} signed={b['signed']}")
        print(f"  entitlements: {json.dumps(b['entitlements'], sort_keys=True)}")
        for lib in b["libraries"]:
            print(f"  links: {lib}")
    print(f"maintainer scripts: {', '.join(s['maintainer_scripts']) or 'none'}")
    if evidence:
        lc = evidence["lifecycle"]
        print(f"static checks (secrets, scripts, helpers): {'passed' if evidence['static']['passed'] else 'FAILED'}")
        if lc:
            steps = ", ".join(f"{st['step']} {'ok' if st['ok'] else 'FAILED'}" for st in lc["steps"])
            print(f"simulated lifecycle: {'passed' if lc['passed'] else 'FAILED'} ({steps or 'not run'}); "
                  "maintainer scripts not executed")
        print("physical-device testing: not recorded (never inferred)")
    return _print_report(r, "package passes package-level validation")


def cmd_validate(a, cfg) -> int:
    from .repo import check_lock_history, check_repository, load_pool, lock_at_ref
    r = Report("repository")
    pkgs = load_pool(cfg, r)
    check_repository(cfg, pkgs, r)
    if a.base_ref:
        check_lock_history(lock_at_ref(cfg.root, a.base_ref), load_lock(cfg), r)
    return _print_report(r, f"{len(pkgs)} packages in pool, repository valid")


def _import(cfg, path: Path, prov: dict, dry_run: bool) -> int:
    from .importer import import_deb
    status, r = import_deb(cfg, path, prov, dry_run=dry_run)
    if r.items:
        print(r.render())
    print(f"{status.upper()}: {path.name}")
    return 0 if status in ("imported", "already-present", "validated", "protected") else 1


def cmd_import_deb(a, cfg) -> int:
    prov = {"source": "local-file", "original_filename": Path(a.deb).name}
    if a.built_from:
        repo, _, sha = a.built_from.partition("@")
        from .importer import SHA40, REPO_RE
        if not REPO_RE.match(repo) or not SHA40.match(sha.lower()):
            print("--built-from must be OWNER/REPO@<40-char commit sha>")
            return 2
        prov.update({"repository": repo, "commit": sha.lower()})
    if a.note:
        prov["note"] = a.note
    return _import(cfg, Path(a.deb), prov, a.dry_run)


def cmd_import_store(a, cfg) -> int:
    """Record an HCG Store artifact that a person approved for public delivery to HCG Repo."""
    from .debinspect import inspect_deb
    from .importer import import_deb
    from .repo import expected_pool_rel
    from .store import check_release, load_release_record, provenance
    rec, rec_sha = load_release_record(Path(a.release))
    try:
        info = inspect_deb(Path(a.deb))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {a.deb}: {exc}")
        return 1
    report = check_release(cfg, rec, a.artifact, info)
    if not report.ok:
        return _print_report(report, "")
    prov = provenance(rec, a.artifact, rec_sha, a.store_origin)
    rel = expected_pool_rel(info)
    existing = [e for e in load_lock(cfg)["packages"] if (e["package"], e["version"], e["architecture"]) == info.key]
    if existing and existing[0]["sha256"] == info.sha256 and existing[0].get("status") == "protected":
        # Already recorded from its build: keep that provenance and add the Store link next to it.
        status, rep = import_deb(cfg, Path(a.deb), {}, dry_run=True, expect={"sha256": info.sha256})
        if status not in ("validated", "already-present"):
            return _print_report(rep, "")
        if not a.dry_run:
            pp = cfg.root / (rel + ".provenance.json")
            data = json.loads(pp.read_text()) if pp.exists() else {}
            data["hcg_store"] = prov
            pp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{info.package} {info.version} {info.architecture}: linked to Store release {rec.get('id')} "
              f"artifact {a.artifact} (sha256 {info.sha256}){' [dry run]' if a.dry_run else ''}")
        return 0
    return _import(cfg, Path(a.deb), prov, a.dry_run)


def cmd_import_release(a, cfg) -> int:
    from .importer import GitHub, ImportError_, fetch_release_asset, temp_workdir
    with temp_workdir() as tmp:
        try:
            path, prov = fetch_release_asset(GitHub(), a.repo, a.tag, a.commit, a.asset, Path(tmp), a.sha256)
        except ImportError_ as exc:
            print(f"REJECTED: {exc}")
            return 1
        return _import(cfg, path, prov, a.dry_run)


def cmd_import_artifact(a, cfg) -> int:
    from .importer import GitHub, ImportError_, fetch_workflow_artifact, temp_workdir
    with temp_workdir() as tmp:
        try:
            path, prov = fetch_workflow_artifact(GitHub(), a.repo, a.run_id, a.commit, a.artifact, a.member, Path(tmp), a.sha256)
        except ImportError_ as exc:
            print(f"REJECTED: {exc}")
            return 1
        return _import(cfg, path, prov, a.dry_run)


def cmd_import_plan(a, cfg) -> int:
    from .importer import pending_requests
    pend = pending_requests(cfg)
    builds = [{"id": r["id"], "repository": r["source"]["repository"], "commit": r["source"]["commit"],
               "recipe": r["source"]["recipe"], "layout": r["source"]["layout"],
               "entitlements": r["source"]["entitlements"]} for r in pend if r["source"]["kind"] == "build"]
    out = {"pending": [r["id"] for r in pend], "builds": builds}
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as f:
            f.write(f"pending={json.dumps(out['pending'])}\nbuilds={json.dumps(builds)}\n")
    print(json.dumps(out, indent=2))
    return 0


def cmd_import_request(a, cfg) -> int:
    import os
    from .importer import (AwaitingLocalImport, GitHub, ImportError_, load_request, request_is_done, run_request,
                           temp_workdir)
    from .release import INCOMING_ENV
    incoming = Path(a.incoming or os.environ.get(INCOMING_ENV) or Path.home() / ".local/share/hcg-repo/incoming")
    rc = 0
    for f in a.requests:
        try:
            r = load_request(Path(f))
        except ImportError_ as exc:
            print(f"REJECTED: {exc}")
            rc = 1
            continue
        if request_is_done(cfg, r):
            print(f"SKIPPED: {r['id']} ({r['request']['package']} {r['request']['version']} "
                  f"{r['request']['architecture']}) is already in repo-lock.json")
            continue
        built = None
        if r["source"]["kind"] == "build":
            cand = sorted(Path(a.built_dir, r["id"]).glob("*.deb")) if a.built_dir else []
            if len(cand) != 1:
                print(f"REJECTED: {r['id']}: expected exactly one built .deb in {a.built_dir}/{r['id']}/, found {len(cand)}")
                rc = 1
                continue
            built = cand[0]
        builder = {"builder_run_url": a.builder_run_url} if a.builder_run_url else {}
        with temp_workdir() as tmp:
            try:
                status, rep = run_request(cfg, r, GitHub(), Path(tmp), built, builder, dry_run=a.dry_run,
                                          incoming=incoming if incoming.is_dir() else None)
            except AwaitingLocalImport as exc:
                print(f"AWAITING-LOCAL-IMPORT: {r['id']}: {exc}")
                continue
            except ImportError_ as exc:
                print(f"REJECTED: {r['id']}: {exc}")
                rc = 1
                continue
        if rep.items:
            print(rep.render())
        print(f"{status.upper()}: {r['id']}")
        rc |= 0 if status in ("imported", "already-present", "validated", "protected") else 1
    return rc


def cmd_keyring_deb(a, cfg) -> int:
    from .keyring import build_keyring_deb
    from .sign import SignError
    try:
        for layout in a.layout:
            deb = build_keyring_deb(cfg, layout, a.version, Path(a.out))
            print(f"built {deb} (import it with: bin/hcgrepo import-deb {deb})")
    except SignError as exc:
        print(f"REFUSED: {exc}")
        return 1
    return 0


def cmd_protect(a, cfg) -> int:
    from .importer import ImportError_, protect
    try:
        dest = protect(cfg, a.package, a.version, a.arch, Path(a.move_to))
    except ImportError_ as exc:
        print(f"REFUSED: {exc}")
        return 1
    print(f"protected: {a.package} {a.version} {a.arch}; the .deb is now at {dest} (outside this repository).")
    print("Commit repo-lock.json and the provenance file; deliver the .deb to HCG Connect's protected storage.")
    return 0


def cmd_channel(a, cfg) -> int:
    from .importer import ImportError_, set_channel
    try:
        before = set_channel(cfg, a.package, a.version, a.arch, a.channel)
    except ImportError_ as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"{a.package} {a.version} {a.arch}: channel {before} -> {a.channel}")
    return 0


def cmd_withdraw(a, cfg) -> int:
    from .importer import withdraw
    fn = withdraw(cfg, a.package, a.version, a.arch, a.reason)
    print(f"withdrawn: {fn} (kept in repo-lock.json; rebuild and publish to remove it from the index)")
    return 0


def _base(a, cfg) -> str:
    return cfg.base_url(getattr(a, "target", None), getattr(a, "base_url", None))


def cmd_commercial(a, cfg) -> int:
    """Read-only report of each commercial product's release settings (docs/COMMERCIAL.md). It changes nothing:
    every setting is changed by editing config/products.toml with an owner approval record, reviewed in a PR."""
    from .config import COMMERCIAL_DEFAULTS
    from .repo import check_commercial
    products = [p for p in cfg.products if p.commercial and (not a.key or p.key == a.key)]
    if not products:
        print(f"no commercial product{' ' + a.key if a.key else ''} in config/products.toml")
        return 1
    lock = load_lock(cfg)
    for p in products:
        c = p.commercial
        print(f"{p.name} ({p.key}) — {', '.join(p.package_ids)}")
        print(f"  release channel  {c.release_channel}   price {c.price_text}{' | ' + c.price_note if c.price_note else ''}   store page {c.store_page}")
        print(f"  source → dest    {c.source_channel} → {c.destination}")
        for k in COMMERCIAL_DEFAULTS:
            v = c.settings[k] or "(none)"
            ap = c.approval_for(k, c.settings[k]) if c.settings[k] != COMMERCIAL_DEFAULTS[k] else None
            mark = f"approved by {ap['approved_by']} on {ap['date']}" if ap else (
                "default (closed)" if c.settings[k] == COMMERCIAL_DEFAULTS[k] else "NOT APPROVED")
            print(f"  {k:<19}{v:<15}{mark}")
        builds = [f"{e['version']} {e.get('channel', 'stable')}/{e.get('status', 'published')}" for e in lock["packages"]
                  if e["package"] in p.package_ids]
        print(f"  recorded builds  {', '.join(builds) or 'none'}")
        print(f"  public listing   {'shown' if p.listed() else 'not shown'}   main index   "
              f"{'approved version indexed' if c.installable else 'not indexed'}")
        steps = (("listing", "approved"), ("store_readiness", "ready"), ("approved_version", None),
                 ("commercial_release", "approved"), ("purchases", "enabled"), ("downloads", "enabled"),
                 ("installation", "enabled"))
        todo = [(k, v) for k, v in steps if (not c.settings[k] if v is None else c.settings[k] != v)]
        nxt = (f"{todo[0][0]} → {todo[0][1] or '<the exact protected stable version>'}" if todo
               else "none: fully activated")
        print(f"  next owner step  {nxt}")
    r = Report("commercial settings")
    check_commercial(cfg, lock, r)
    return _print_report(r, "commercial settings consistent")


def cmd_build(a, cfg) -> int:
    from .site import BuildError, build
    base = _base(a, cfg)
    try:
        if a.preview_listings and a.diagnostics:
            print("--preview-listings builds a review copy only; it cannot be combined with --diagnostics")
            return 2
        res = build(cfg, base, Path(a.out), sign_key=a.sign_key, require_signature=True if a.require_signature else None,
                    preview_listings=a.preview_listings)
    except BuildError as exc:
        if exc.report:
            print(exc.report.render())
        print(f"BUILD FAILED: {exc}")
        return 1
    if res.report.warnings:
        print(res.report.render())
    if a.diagnostics:
        from .diagnostics import build_all, verify_all
        build_all(cfg, base, res.out_dir)
        from .diagnostics import VARIANTS
        from .repo import protected_packages
        stray = [p for v in VARIANTS for p in (res.out_dir / "diag" / v.key).rglob("*.deb")]   # d1-d6 carry no files
        names = {p.info.canonical_filename() for p in protected_packages(cfg)}
        leaked = [p for p in (res.out_dir / "diag").rglob("*.deb") if p.name in names]       # nor any protected one
        if stray or leaked:
            print(f"BUILD FAILED: package files under diag/: {[str(p) for p in stray + leaked]}")
            return 1
        failed = False
        for key, rep in verify_all(cfg, base, res.out_dir.as_uri() + "/").items():
            print(f"  diag/{key}: {'OK' if rep.ok else 'FAILED'} ({len(rep.errors)} errors, {len(rep.warnings)} warnings)")
            if not rep.ok or rep.warnings:
                print(rep.render())
            failed |= not rep.ok
        if failed:
            print("BUILD FAILED: a diagnostic source failed verification")
            return 1
    print(f"built {res.out_dir} for {res.repo_url}")
    if a.preview_listings:
        print("  PREVIEW: prepared commercial listings are rendered for review. Never deploy this build "
              "(verify refuses it).")
    print(f"  packages: {len(res.packages)}; files: {len(res.files)}")
    print(f"  metadata signature: {res.signature_detail if res.signed else 'unsigned'}")
    return 0


def cmd_verify(a, cfg) -> int:
    from .verify import verify
    base = _base(a, cfg)
    fetch = a.fetch_url or base
    pub = Path(a.pubkey) if a.pubkey else None
    ev = Path(a.evidence) if a.evidence else None
    if getattr(a, "channel", "stable") == "candidate":
        cfg = cfg.candidate()
    res = verify(base, fetch, cfg=cfg, pubkey=pub, require_signature=a.require_signature, check_debs=not a.no_debs,
                 evidence=ev, check_provider=not a.no_provider, site_checks=not a.source_only)
    if ev:
        print(f"evidence: {len(res.exchanges)} responses saved in {ev} (responses.json lists status, redirects, headers, SHA-256)")
    if a.list_urls:
        for u in res.checked_urls:
            print("  GET", u)
    print(res.summary())
    return _print_report(res.report, f"{fetch} verified as {cfg.repo_url(base)}")


def cmd_serve(a, cfg) -> int:
    from .server import make_server
    base = _base(a, cfg)
    prefix = a.prefix or urlsplit(base).path
    srv = make_server(Path(a.dir), prefix, a.host, a.port, verbose=True)
    host, port = srv.server_address[:2]
    print(f"serving {a.dir} at http://{host}:{port}{prefix}  (APT source: http://{host}:{port}{prefix}{cfg.repo_subdir}/)")
    print("Metadata inside still points at", cfg.repo_url(base), "- use `hcgrepo verify --fetch-url` to test it.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_smoke(a, cfg) -> int:
    """Build for a public base URL, serve it locally under the same path prefix, verify over HTTP."""
    from .server import BackgroundServer
    from .site import BuildError, build
    from .verify import verify
    rc = 0
    targets = [a.base_url] if a.base_url else ([a.target] if a.target else list(cfg.urls))
    for t in targets:
        base = cfg.base_url(None, t) if a.base_url else cfg.base_url(t)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "site"
            try:
                build(cfg, base, out)
            except BuildError as exc:
                if exc.report:
                    print(exc.report.render())
                print(f"BUILD FAILED for {base}: {exc}")
                return 1
            prefix = urlsplit(base).path
            with BackgroundServer(out, prefix) as srv:
                res = verify(base, srv.url, cfg=cfg)
            print(f"[{base}] served at {srv.url}: {res.summary()}")
            if res.report.items:
                print(res.report.render())
            rc |= 0 if res.report.ok else 1
    print("SMOKE OK" if rc == 0 else "SMOKE FAILED")
    return rc


def cmd_release(a, cfg) -> int:
    from . import release as rm
    from .submission import SubmissionError
    store = rm.incoming_root(a.incoming)
    try:
        if a.action == "submit":
            review = rm.submit(cfg, store, handoff=Path(a.handoff) if a.handoff else None,
                               deb=Path(a.deb) if a.deb else None, manifest=Path(a.manifest) if a.manifest else None,
                               lifecycle=not a.no_lifecycle)
            m, review, _ = rm.load_review(store, review["submission"])
            print(rm.render_review(m, review))
            return 0 if review["state"] == "validated" else 1
        if a.action == "incoming":
            out = rm.list_incoming(cfg, store)
            if a.json:
                print(json.dumps(out, indent=2))
                return 0
            print(f"Incoming store: {store}")
            for s in out["submissions"]:
                print(f"  {s['state']:<10} {s['package']} {s['version']}  {s['sha256']}  [{s['provider']}] "
                      f"{s['errors']} errors, {s['warnings']} warnings  id={s['id']}")
            if not out["submissions"]:
                print("  (no submissions)")
            print("Superseded Candidate versions (still installable by exact version; not offered as the upgrade):")
            for x in out["superseded_candidates"]:
                print(f"  {x['package']} {x['version']} -> {x['superseded_by']}")
            return 0
        if a.action == "show":
            m, review, _ = rm.load_review(store, a.id)
            print(json.dumps(review, indent=2) if a.json else rm.render_review(m, review))
            return 0
        if a.action == "approve":
            review = rm.approve(store, a.id, a.sha256, a.by, a.record)
            print(f"APPROVED for Candidate import: {a.id} sha256 {review['approval']['sha256']}. Not published.")
            return 0
        if a.action == "prepare":
            status, rep, review = rm.prepare(cfg, store, a.id, free_demo_approval=a.free_demo_approval,
                                             create_package_metadata=a.create_package_metadata, via=a.via)
            if rep.items:
                print(rep.render())
            where = ("imports/local/, " if status == "staged" else "pool/, repo-lock.json, ")
            print(f"{status.upper()}: {a.id} -> Candidate ({where}imports/{a.id}.toml, submissions/{a.id}/). Review and "
                  "commit on a branch; publishing is the separate Publish workflow.")
            return 0 if status in ("imported", "already-present", "staged") else 1
        if a.action == "withdraw":
            rm.withdraw_submission(store, a.id, a.reason)
            print(f"WITHDRAWN: {a.id}")
            return 0
    except (rm.ReleaseError, SubmissionError) as exc:
        print(f"REFUSED: {exc}")
        return 1
    return 2


def cmd_compat(a, cfg) -> int:
    from . import compat
    reg = compat.registry(cfg, a.channel)
    if a.json:
        print(json.dumps(reg, indent=2, sort_keys=True))
        return 0
    for pkg in reg["packages"]:
        print(f"{pkg['package']}  (last physically verified: {pkg['last_verified_version'] or 'none'})")
        for v in pkg["versions"]:
            ios = f"iOS {v['minimum_ios']}+" if v["minimum_ios"] else "iOS ?"
            print(f"  {v['version']:<16} {v['channel']:<9} {v['layout']:<9} {ios:<10} {v['status']}")
            if v["label"]:
                print(f"      label: {v['label']}")
            for t in v["device_evidence"]:
                print(f"      {t['stage'] or 'runtime'} {t['result']}: {t['device']}, iOS {t['ios']}, {t['jailbreak']} "
                      f"({t['date']}; {t['evidence']})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hcgrepo", description=__doc__)
    ap.add_argument("--version", action="version", version=f"hcgrepo {__version__}")
    sp = ap.add_subparsers(dest="cmd", required=True)

    def target_args(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--target", help="named base URL from config/repo.toml [urls] (pages, production)")
        g.add_argument("--base-url", help="explicit public base URL of the site (the APT source is <base>repo/)")

    p = sp.add_parser("inspect", help="inspect and validate one .deb without importing it")
    p.add_argument("deb")
    p.add_argument("--json", action="store_true")
    p.add_argument("--deep", action="store_true",
                   help="also scan for secrets, review maintainer scripts and helpers, and simulate the dpkg lifecycle")
    p.add_argument("--no-lifecycle", action="store_true", help="with --deep: skip the simulated dpkg lifecycle")
    p.set_defaults(fn=cmd_inspect)

    p = sp.add_parser("validate", help="validate every package in pool/, the lock and dependencies")
    p.add_argument("--base-ref", help="also check repo-lock.json history against this git ref (e.g. origin/main)")
    p.set_defaults(fn=cmd_validate)

    p = sp.add_parser("import-deb", help="validate a local .deb and add it to pool/")
    p.add_argument("deb")
    p.add_argument("--built-from", help="OWNER/REPO@COMMITSHA the package was built from (recorded as provenance)")
    p.add_argument("--note", help="free-text provenance note")
    p.add_argument("--dry-run", action="store_true", help="validate only")
    p.set_defaults(fn=cmd_import_deb)

    p = sp.add_parser("import-store-release", help="record an HCG Store artifact approved for public delivery to HCG Repo")
    p.add_argument("--release", required=True, help="the Store's release record (JSON from GET /api/v1/publishing/releases/<id>)")
    p.add_argument("--artifact", required=True, help="artifact id inside that release (art-...)")
    p.add_argument("--deb", required=True, help="the artifact's bytes, downloaded through the Store's authorised review download")
    p.add_argument("--store-origin", default="https://store.hakmicapitalgroup.com", help="Store the record came from (provenance)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_import_store)

    p = sp.add_parser("import-github-release", help="import one asset of a specific GitHub release")
    p.add_argument("--repo", required=True, help="OWNER/NAME")
    p.add_argument("--tag", required=True, help="exact release tag ('latest' is refused)")
    p.add_argument("--commit", required=True, help="full commit SHA the tag must point to")
    p.add_argument("--asset", required=True, help="exact asset file name")
    p.add_argument("--sha256", help="expected sha256 of the asset (optional extra pin)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_import_release)

    p = sp.add_parser("import-github-artifact", help="import a .deb from a specific workflow run's artifact")
    p.add_argument("--repo", required=True)
    p.add_argument("--run-id", required=True, type=int)
    p.add_argument("--commit", required=True, help="full commit SHA the run must have built")
    p.add_argument("--artifact", required=True, help="exact artifact name")
    p.add_argument("--member", help="path of the .deb inside the artifact, if it has several")
    p.add_argument("--sha256")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_import_artifact)

    p = sp.add_parser("import-plan", help="list import requests in imports/ that are not yet in repo-lock.json")
    p.add_argument("--github-output", help="also write pending/builds as GitHub Actions step outputs to this file")
    p.set_defaults(fn=cmd_import_plan)

    p = sp.add_parser("import-request", help="process import request files (imports/*.toml)")
    p.add_argument("requests", nargs="+")
    p.add_argument("--built-dir", help="directory with <request-id>/<file>.deb for build requests")
    p.add_argument("--builder-run-url", help="URL of the CI run that built the package (provenance)")
    p.add_argument("--incoming", help="private incoming store for local-submission requests (default $HCG_REPO_INCOMING)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_import_request)

    p = sp.add_parser("release", help="Candidate Release Manager: submit, review, approve and prepare incoming packages")
    p.add_argument("--incoming", help="private incoming store (default $HCG_REPO_INCOMING or ~/.local/share/hcg-repo/incoming)")
    rs = p.add_subparsers(dest="action", required=True)
    q = rs.add_parser("submit", help="validate an artifact into the private incoming store (never publishes)")
    q.add_argument("--handoff", help="HCG Executable Maker Unleashify candidate-handoff folder")
    q.add_argument("--deb", help="the .deb, with --manifest")
    q.add_argument("--manifest", help="hcg.repo.artifact-submission v1 manifest (docs/DISTRIBUTION_V2.md)")
    q.add_argument("--no-lifecycle", action="store_true", help="skip the simulated dpkg lifecycle")
    q = rs.add_parser("incoming", help="list submissions and superseded Candidate versions")
    q.add_argument("--json", action="store_true")
    q = rs.add_parser("show", help="show one submission's review and exact checksum")
    q.add_argument("id")
    q.add_argument("--json", action="store_true")
    q = rs.add_parser("approve", help="record the owner's approval of one exact SHA-256 for Candidate import")
    q.add_argument("id")
    q.add_argument("--sha256", required=True)
    q.add_argument("--by", required=True, help="who approved")
    q.add_argument("--record", required=True, help="the approval, verbatim")
    q = rs.add_parser("prepare", help="import an approved submission into pool/ for Candidate (does not publish)")
    q.add_argument("id")
    q.add_argument("--free-demo-approval", help="owner's verbatim approval of this version as a free Candidate demo")
    q.add_argument("--create-package-metadata", action="store_true",
                   help="create packages/<id>/ from the submission's package.toml draft when it does not exist")
    q.add_argument("--via", choices=["pool", "local-file"], default="pool",
                   help="pool: import here (default); local-file: stage the approved bytes for Prepare packages in CI")
    q = rs.add_parser("withdraw", help="withdraw a submission that has not been prepared")
    q.add_argument("id")
    q.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_release)

    p = sp.add_parser("compat", help="compatibility registry: evidence-based status of every released version")
    p.add_argument("--channel", choices=["stable", "candidate"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_compat)

    p = sp.add_parser("keyring-deb", help="build the package that installs the public archive key on devices")
    p.add_argument("--layout", action="append", choices=["rootless", "rootful"], required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--out", default="keyring-out")
    p.set_defaults(fn=cmd_keyring_deb)

    p = sp.add_parser("protect", help="move a connect/paid package out of the public pool (metadata stays)")
    p.add_argument("--package", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--arch", required=True)
    p.add_argument("--move-to", required=True, help="directory OUTSIDE this repository to move the .deb to")
    p.set_defaults(fn=cmd_protect)

    p = sp.add_parser("channel", help="move a recorded build between the stable and candidate (testing) sources")
    p.add_argument("package")
    p.add_argument("version")
    p.add_argument("arch")
    p.add_argument("channel", choices=["stable", "candidate"])
    p.set_defaults(fn=cmd_channel)

    p = sp.add_parser("withdraw", help="remove a version from the index; its lock record stays")
    p.add_argument("--package", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--arch", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_withdraw)

    p = sp.add_parser("build", help="build the complete static snapshot (site + APT repo)")
    target_args(p)
    p.add_argument("--out", default="_site")
    p.add_argument("--sign-key", help="gpg key id/fingerprint for Release.gpg and InRelease (or HCG_REPO_SIGNING_KEY)")
    p.add_argument("--require-signature", action="store_true")
    p.add_argument("--diagnostics", action="store_true",
                   help="also write the isolated Sileo diagnostic sources under diag/ (docs/SILEO_CRASH.md); never by default")
    p.add_argument("--preview-listings", action="store_true",
                   help="also render prepared commercial listings, marked as previews, for owner review (docs/COMMERCIAL.md); "
                        "the result is never deployable")
    p.set_defaults(fn=cmd_build)

    p = sp.add_parser("commercial", help="show the owner-controlled commercial release settings and what blocks the next step")
    p.add_argument("key", nargs="?", help="product key (default: every commercial product)")
    p.set_defaults(fn=cmd_commercial)

    p = sp.add_parser("verify", help="verify a deployed or locally served repository like a client would")
    target_args(p)
    p.add_argument("--fetch-url", help="fetch from here instead of the public base (local server or file:// URL)")
    p.add_argument("--pubkey", help="armored public key to verify Release.gpg/InRelease against")
    p.add_argument("--require-signature", action="store_true")
    p.add_argument("--no-debs", action="store_true", help="skip downloading packages")
    p.add_argument("--list-urls", action="store_true")
    p.add_argument("--evidence", help="save every response body and responses.json (status, redirects, headers) here")
    p.add_argument("--no-provider", action="store_true", help="do not call the payment provider's info endpoint")
    p.add_argument("--channel", choices=["stable", "candidate"], default="stable",
                   help="which source to verify: stable <base>repo/ or candidate <base>repo/candidate/")
    p.add_argument("--source-only", action="store_true",
                   help="check only the APT source (what Sileo fetches), not the website and product pages")
    p.set_defaults(fn=cmd_verify)

    p = sp.add_parser("serve", help="serve a built site under its URL prefix for local testing")
    target_args(p)
    p.add_argument("--dir", default="_site")
    p.add_argument("--prefix", help="URL prefix (default: path of the base URL, e.g. /HCG-repo/)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)

    p = sp.add_parser("smoke", help="build + serve under the right prefix + verify over HTTP, for each target")
    target_args(p)
    p.set_defaults(fn=cmd_smoke)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        cfg = load_config()
        return a.fn(a, cfg)
    except (ConfigError, InspectError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

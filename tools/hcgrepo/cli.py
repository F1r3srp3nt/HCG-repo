"""Command-line interface: `bin/hcgrepo <command>` (or `python3 -m tools.hcgrepo`)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
        out = {"summary": info.summary(), "control": dict(info.control),
               "problems": [{"level": l, "code": c, "message": m} for l, c, m in r.items], "evidence": evidence}
        if evidence:
            from . import levels as lv
            from .deepcheck import stage_of
            fs = [{"stage": stage_of(c), "level": l, "code": c, "message": m} for l, c, m in r.items]
            out["levels"] = lv.all_levels(lv.machine_levels(fs, evidence["lifecycle"], sorted(info.scripts)),
                                          meta.device_tests(info.version))
        print(json.dumps(out, indent=2))
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
        from . import levels as lv
        from .deepcheck import stage_of
        findings = [{"stage": "static", "level": l, "code": c, "message": m} for l, c, m in r.items
                    if not any(c == dc for _, dc, _ in deep.items)]
        findings += [{"stage": stage_of(c), "level": l, "code": c, "message": m} for l, c, m in deep.items]
        machine = lv.machine_levels(findings, evidence["lifecycle"], sorted(info.scripts))
        print("\n".join(lv.render(lv.all_levels(machine, meta.device_tests(info.version)))))
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
            deb = build_keyring_deb(cfg, layout, a.version, Path(a.out),
                                    extra_keys=[Path(k) for k in a.transition_key or []],
                                    test_key=Path(a.test_key) if a.test_key else None)
            print(f"built {deb}" + (" (TEST KEY: the importer refuses it)" if a.test_key else
                                    f" (import it with: bin/hcgrepo import-deb {deb})"))
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
            if a.result_json:
                Path(a.result_json).write_text(json.dumps(rm.submission_result(cfg, m, review), indent=2) + "\n")
            print(rm.render_review(m, review))
            return 0 if review["state"] == "validated" else 1
        if a.action == "status":
            m, review, _ = rm.load_review(store, a.id)
            print(json.dumps(rm.submission_result(cfg, m, review), indent=2))
            return 0
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
    if a.public:
        text = json.dumps(compat.public_registry(cfg, a.channel), indent=2, sort_keys=True) + "\n"
        if a.out:
            Path(a.out).write_text(text)
            print(f"wrote {a.out} (preview only: publication needs [compatibility] publish = true)")
        else:
            print(text, end="")
        return 0
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
            if v["runtime_compatibility"] != "not-established" or v["levels"]["device_installation"]["state"] != "not-recorded":
                print(f"      overall runtime compatibility: {v['runtime_compatibility']}")
            for k in ("device_installation", "application_launch", "runtime_verification"):
                st = v["levels"][k]["state"]
                if st != "not-recorded":
                    print(f"      {k.replace('_', ' ')}: {st}" + (f" ({v['levels'][k]['note']})" if v['levels'][k].get('note') else ""))
            for t in v["device_evidence"]:
                print(f"      {t['stage'] or 'runtime'} {t['result']}: {t['device']}, iOS {t['ios']}, {t['jailbreak']} "
                      f"({t['date']}; {t['evidence']})")
    return 0


def cmd_inspect_artifact(a, cfg) -> int:
    """Download one pinned GitHub Actions artifact and review it: exact SHA-256, size and every machine evidence level,
    for the owner's decision before an import request is written. Stores and commits nothing."""
    from . import levels as lv
    from . import release as rm
    from .importer import GitHub, ImportError_, fetch_workflow_artifact, temp_workdir
    with temp_workdir() as tmp:
        try:
            path, prov = fetch_workflow_artifact(GitHub(), a.repo, a.run_id, a.commit, a.artifact, a.member, Path(tmp), a.sha256)
        except ImportError_ as exc:
            print(f"REFUSED: {exc}")
            return 1
        review = rm.inspect_artifact(cfg, path, lifecycle=not a.no_lifecycle)
    result = rm.submission_result(cfg, None, review)
    result.update({"submission": None, "source": {"repository": a.repo, "run_id": a.run_id, "commit": a.commit.lower(),
                                                  "artifact": a.artifact, "artifact_digest": prov.get("artifact_digest")}})
    result["requirements"] = [r for r in result["requirements"] if r["kind"] != "checksum-approval"]
    result["requirements"].insert(0, {"kind": "checksum-approval", "satisfied": False,
                                      "detail": "the owner approves exactly this SHA-256 before an import request pins it"})
    if a.result_json:
        Path(a.result_json).write_text(json.dumps(result, indent=2) + "\n")
    pkg = review.get("package", {})
    print(f"Artifact     {a.repo} run {a.run_id} @ {a.commit} / {a.artifact}")
    print(f"Package      {pkg.get('identifier')} {pkg.get('version')} {pkg.get('architecture')}")
    print(f"Size         {review.get('size')} bytes")
    print(f"SHA-256      {review.get('sha256')}")
    print(f"State        {review['state'].upper()}")
    print("\n".join(lv.render(review.get("levels", {}))))
    for f in review.get("findings", []):
        print(f"  {'ERROR' if f['level'] == 'error' else 'warn '} [{f['code']}] {f['message']}")
    return 0 if review["state"] == "validated" else 1


def cmd_passport(a, cfg) -> int:
    from .passport import all_passports
    ps = all_passports(cfg, a.package, a.version)
    if not ps:
        print("no matching release in repo-lock.json")
        return 1
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        for p in ps:
            (out / f"{p['package']}_{p['version']}_{p['architecture']}.json").write_text(json.dumps(p, indent=2) + "\n")
        print(f"wrote {len(ps)} passports to {out}")
        return 0
    print(json.dumps(ps if len(ps) > 1 else ps[0], indent=2))
    return 0


def cmd_admin(a, cfg) -> int:
    import time
    from . import admin
    from .release import incoming_root
    srv, url, _ = admin.serve(cfg, incoming_root(a.incoming), port=a.port)
    print(f"HCG Repo administration (local only): {url}")
    print("The token in the URL is required for every request. Ctrl+C stops the dashboard. Nothing here publishes.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


def cmd_guard(a, cfg) -> int:
    """Publication safeguards around a deploy (docs/SIGNING.md, Publication safeguards)."""
    from . import guard
    r = Report("publication guard")
    if a.deployed:
        man = json.loads(Path(a.deployed).read_text())
        r.extend(guard.check_deployed(cfg, man, _base(a, cfg), a.fetch_base))
        return _print_report(r, f"{len(man['files'])} live metadata files are byte-identical to the build")
    site = Path(a.site)
    r.extend(guard.check_site(cfg, site))
    if a.against_live:
        r.extend(guard.compare_live(cfg, site, _base(a, cfg), a.fetch_base, allow_key_change=a.allow_key_change))
    if a.manifest_out:
        Path(a.manifest_out).write_text(json.dumps(guard.manifest(cfg, site), indent=2) + "\n")
    return _print_report(r, "exact checksums, completeness" + (" and live comparison" if a.against_live else "") + " passed")


def cmd_monitor(a, cfg) -> int:
    from . import guard
    out = guard.monitor(cfg, _base(a, cfg), a.fetch_base)
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        for c in out["checks"]:
            print(f"{c['status'].upper():<8} {c['id']:<28} {c['detail']}")
        print(f"overall: {out['status']} (read-only; nothing was changed)")
    return {"ok": 0, "warning": 0}.get(out["status"], 1) if not a.strict else (0 if out["status"] == "ok" else 1)


def cmd_signing_status(a, cfg) -> int:
    from .sign import SignError, check_key, key_status
    pub = cfg.root / cfg.public_key_file
    print(f"Configured fingerprint : {cfg.signing_fingerprint or '(none: production signing not configured)'}")
    print(f"Signing required       : {cfg.signing_required or bool(cfg.signing_fingerprint)}"
          + ("  (fail-closed: a fingerprint is configured)" if cfg.signing_fingerprint else ""))
    if not pub.is_file():
        print(f"Public key             : {cfg.public_key_file} is not committed")
        return 0 if not cfg.signing_fingerprint else 1
    try:
        st = key_status(pub)
    except SignError as exc:
        print(f"Public key             : unreadable: {exc}")
        return 1
    print(f"Public key primary     : {st['primary_fpr']}  {', '.join(st['uids'])}")
    for k in st["keys"]:
        exp = datetime.fromtimestamp(k["expires"], tz=timezone.utc).date().isoformat() if k["expires"] else "never"
        print(f"  {k['kind']:<8} {k['fpr']}  caps={k['capabilities']} expires={exp}"
              + ("  REVOKED" if k["revoked"] else "") + ("  EXPIRED" if k["expired"] else ""))
    print(f"Days until signing ends: {st['days_until_signing_expiry'] if st['days_until_signing_expiry'] is not None else 'no expiry'}")
    probs = check_key(pub, cfg.signing_fingerprint, warn_days=a.warn_days)
    for lvl, code, msg in probs:
        print(f"  {'ERROR' if lvl == 'error' else 'warn '} [{code}] {msg}")
    return 1 if any(l == "error" for l, _, _ in probs) else 0


def cmd_approval(a, cfg) -> int:
    from . import approvals as ap
    try:
        if a.action in ("draft-artifact", "draft-publication"):
            if a.action == "draft-artifact":
                stmt = ap.draft_artifact(cfg, a.package, a.version, a.architecture, a.channel, a.approver,
                                         sha256=a.sha256, repository=a.repository, commit=a.commit, note=a.note or "")
            else:
                stmt = ap.draft_publication(cfg, a.commit, a.target or "production", a.approver, note=a.note or "")
            path = ap.write_draft(cfg, stmt)
            rel = path.relative_to(cfg.root)
            print(path.read_text(), end="")
            print(f"\nDRAFT written to {rel}. Check every field, then sign it OFFLINE with the founder approval key:")
            print(f"  gpg --local-user {stmt['approver']} --armor --detach-sign {rel}")
            print(f"and commit {rel} with {rel}.asc. Never sign with the repository signing key.")
            return 0
        if a.action == "verify":
            rc = 0
            files = [Path(f) for f in a.files] or sorted((cfg.root / "approvals").rglob("*.json"))
            for f in files:
                stmt, r = ap.verify_statement(cfg, f)
                print(r.render() if r.items else "", end="\n" if r.items else "")
                print(f"{'VERIFIED' if r.ok else 'INVALID'}: {f} ({stmt.get('kind')}, approver {stmt.get('approver')})")
                rc |= 0 if r.ok else 1
            return rc
        if a.action == "check-publication":
            if not cfg.approvals_required_for_publish and not a.force:
                print("publication approvals are not required ([approvals] required_for_publish = false)")
                return 0
            r = ap.check_publication(cfg, a.commit, a.target or "production")
            return _print_report(r, f"publication of {a.commit[:12]} to {a.target or 'production'} is approved")
    except ap.ApprovalError as exc:
        print(f"REFUSED: {exc}")
        return 1
    return 2


def cmd_trust(a, cfg) -> int:
    from . import trust
    doc = trust.trust_document(cfg, _base(a, cfg))
    text = json.dumps(doc, indent=2) + "\n"
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out} (preview: publication needs [trust] publish = true)")
    else:
        print(text, end="")
    return 0


def cmd_verify_artifact(a, cfg) -> int:
    from . import trust
    res = trust.verify_artifact(cfg, Path(a.deb), _base(a, cfg), a.fetch_base, a.channel,
                                Path(a.pubkey) if a.pubkey else None)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for st in res["steps"]:
            print(f"{'ok  ' if st['ok'] else 'FAIL'} {st['step']:<18} {st['detail']}")
        art = res.get("artifact", {})
        print(f"file      {res['file']}  sha256 {art.get('sha256')}")
        for b in res.get("contents", {}).get("binaries", []):
            print(f"code      {b['path']}  signed={b['signed']}  entitlements={', '.join(b['entitlements']) or 'none'}")
        print(f"VERDICT   {res['verdict']}  (metadata authenticated: {res.get('metadata_authenticated')})")
        print(f"note      {res['scope_note']}")
    return 0 if res["verdict"] in ("verified", "matches-unauthenticated-index") else 1


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
    q.add_argument("--result-json", help="also write the machine-readable submission result (hcg.repo.submission-result v1)")
    q = rs.add_parser("status", help="machine-readable submission result (JSON) for HCG Executable Maker")
    q.add_argument("id")
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

    p = sp.add_parser("inspect-artifact", help="review a pinned GitHub Actions artifact (exact SHA-256, levels); commits nothing")
    p.add_argument("--repo", required=True)
    p.add_argument("--run-id", required=True, type=int)
    p.add_argument("--commit", required=True)
    p.add_argument("--artifact", required=True)
    p.add_argument("--member", help="the .deb inside the artifact, when it holds several files")
    p.add_argument("--sha256", help="optional: refuse unless the .deb has this SHA-256")
    p.add_argument("--no-lifecycle", action="store_true")
    p.add_argument("--result-json", help="write the machine-readable result here")
    p.set_defaults(fn=cmd_inspect_artifact)

    p = sp.add_parser("compat", help="compatibility registry: evidence-based status of every released version")
    p.add_argument("--channel", choices=["stable", "candidate"])
    p.add_argument("--json", action="store_true", help="full internal registry (includes evidence paths)")
    p.add_argument("--public", action="store_true", help="the sanitized public compatibility.json (for review)")
    p.add_argument("--out", help="with --public: write to this file")
    p.set_defaults(fn=cmd_compat)

    p = sp.add_parser("guard", help="publication safeguards: exact checksums, completeness, live comparison, deployed bytes")
    target_args(p)
    p.add_argument("--site", default="_site", help="the built site to check")
    p.add_argument("--against-live", action="store_true", help="also compare with the live repository at the target")
    p.add_argument("--fetch-base", help="fetch from here instead of the public base (local testing)")
    p.add_argument("--allow-key-change", action="store_true", help="acknowledge a planned signing-key rotation")
    p.add_argument("--manifest-out", help="write the SHA-256 manifest of the built metadata files")
    p.add_argument("--deployed", help="check that the live metadata matches this manifest (after deploy)")
    p.set_defaults(fn=cmd_guard)

    p = sp.add_parser("monitor", help="read-only health check of the live repository (HCG Sentinel contract)")
    target_args(p)
    p.add_argument("--fetch-base")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    p.set_defaults(fn=cmd_monitor)

    p = sp.add_parser("signing-status", help="configured fingerprint, committed public key, subkeys, expiry")
    p.add_argument("--warn-days", type=int, default=60)
    p.set_defaults(fn=cmd_signing_status)

    p = sp.add_parser("approval", help="founder-signed release approvals (artifact / publication), docs/RELEASE_APPROVALS.md")
    rs = p.add_subparsers(dest="action", required=True)
    q = rs.add_parser("draft-artifact", help="write an artifact approval statement to sign offline")
    for k in ("--package", "--version", "--architecture", "--channel", "--approver"):
        q.add_argument(k, required=True)
    q.add_argument("--sha256")
    q.add_argument("--repository")
    q.add_argument("--commit")
    q.add_argument("--note")
    q = rs.add_parser("draft-publication", help="write a publication approval statement to sign offline")
    q.add_argument("--commit", required=True)
    q.add_argument("--target", default="production")
    q.add_argument("--approver", required=True)
    q.add_argument("--note")
    q = rs.add_parser("verify", help="verify approval statements (default: all under approvals/)")
    q.add_argument("files", nargs="*")
    q = rs.add_parser("check-publication", help="require a verified publication approval for a commit (when configured)")
    q.add_argument("--commit", required=True)
    q.add_argument("--target", default="production")
    q.add_argument("--force", action="store_true", help="check even when not required")
    p.set_defaults(fn=cmd_approval)

    p = sp.add_parser("trust-document", help="hcg.repo.trust v1: sources, signing status, fingerprints, contracts (preview)")
    target_args(p)
    p.add_argument("--out")
    p.set_defaults(fn=cmd_trust)

    p = sp.add_parser("verify-artifact", help="independently verify ONE downloaded .deb against the live index")
    target_args(p)
    p.add_argument("deb")
    p.add_argument("--channel", choices=["stable", "candidate"], default="stable")
    p.add_argument("--fetch-base")
    p.add_argument("--pubkey", help="trusted public key to verify a signed index (default: the committed key)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_verify_artifact)

    p = sp.add_parser("admin", help="local administration dashboard over the Release Manager (127.0.0.1 only)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--incoming", help="private incoming store (default $HCG_REPO_INCOMING)")
    p.set_defaults(fn=cmd_admin)

    p = sp.add_parser("passport", help="HCG Release Passport records (hcg.release-passport v1) from the repository's records")
    p.add_argument("package", nargs="?")
    p.add_argument("version", nargs="?")
    p.add_argument("--out", help="write one JSON file per release into this directory")
    p.set_defaults(fn=cmd_passport)

    p = sp.add_parser("keyring-deb", help="build the package that installs the public archive key on devices")
    p.add_argument("--layout", action="append", choices=["rootless", "rootful"], required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--out", default="keyring-out")
    p.add_argument("--transition-key", action="append", help="also trust this public key (rotation transition)")
    p.add_argument("--test-key", help="development only: build with this public key; marked as a test, never importable")
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

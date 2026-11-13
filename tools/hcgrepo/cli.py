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
    if a.json:
        print(json.dumps({"summary": info.summary(), "control": dict(info.control),
                          "problems": [{"level": l, "code": c, "message": m} for l, c, m in r.items]}, indent=2))
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
    return 0 if status in ("imported", "already-present", "validated") else 1


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


def cmd_withdraw(a, cfg) -> int:
    from .importer import withdraw
    fn = withdraw(cfg, a.package, a.version, a.arch, a.reason)
    print(f"withdrawn: {fn} (kept in repo-lock.json; rebuild and publish to remove it from the index)")
    return 0


def _base(a, cfg) -> str:
    return cfg.base_url(getattr(a, "target", None), getattr(a, "base_url", None))


def cmd_build(a, cfg) -> int:
    from .site import BuildError, build
    base = _base(a, cfg)
    try:
        res = build(cfg, base, Path(a.out), sign_key=a.sign_key, require_signature=True if a.require_signature else None)
    except BuildError as exc:
        if exc.report:
            print(exc.report.render())
        print(f"BUILD FAILED: {exc}")
        return 1
    if res.report.warnings:
        print(res.report.render())
    print(f"built {res.out_dir} for {res.repo_url}")
    print(f"  packages: {len(res.packages)}; files: {len(res.files)}")
    print(f"  metadata signature: {res.signature_detail if res.signed else 'unsigned'}")
    return 0


def cmd_verify(a, cfg) -> int:
    from .verify import verify
    base = _base(a, cfg)
    fetch = a.fetch_url or base
    pub = Path(a.pubkey) if a.pubkey else None
    res = verify(base, fetch, cfg=cfg, pubkey=pub, require_signature=a.require_signature, check_debs=not a.no_debs)
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
    p.set_defaults(fn=cmd_build)

    p = sp.add_parser("verify", help="verify a deployed or locally served repository like a client would")
    target_args(p)
    p.add_argument("--fetch-url", help="fetch from here instead of the public base (local server or file:// URL)")
    p.add_argument("--pubkey", help="armored public key to verify Release.gpg/InRelease against")
    p.add_argument("--require-signature", action="store_true")
    p.add_argument("--no-debs", action="store_true", help="skip downloading packages")
    p.add_argument("--list-urls", action="store_true")
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

#!/usr/bin/env python3
"""Add the HCG Repo qualification source to a built site (docs/QUALIFICATION_SILEO.md).

  tools/qualification/assemble.py --site _site [--preflight] [--allow-inactive]

1. Loads qualification/config (its own identity, lock and packages) and builds it for its unlisted base URL.
2. Places the result at <site>/<[qualification] path>, marks every HTML page noindex, and refuses to touch anything
   outside that directory (the public sources stay exactly as built).
3. Serves the assembled site locally and walks the qualification source the way Sileo does (bin/hcgrepo verify),
   including what payment_endpoint advertises.
4. --preflight: HCG Store's provider must answer <url>info with name "HCG Store" before a deploy.

Refuses unless [qualification] active = true (the owner's deployment approval), except with --allow-inactive for a
local dry run. It never deploys; the Qualification workflow and Publish do, and only when started by hand.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from hcgrepo.config import load_config  # noqa: E402

NOINDEX = '<meta name="robots" content="noindex, nofollow">'


def build_qualification(site: Path, allow_inactive: bool = False) -> tuple[Path, object]:
    from hcgrepo.site import build
    q = load_config(ROOT / "qualification")
    if not q.qualification_path:
        raise SystemExit("qualification/config/repo.toml has no [qualification] path")
    if not q.qualification_active and not allow_inactive:
        raise SystemExit("[qualification] active = false: the owner has not approved deploying the qualification source")
    if q.channel != "stable" or not q.payment_endpoint:
        raise SystemExit("the qualification source must advertise the payment provider ([payment_provider] advertise = true)")
    base = q.base_url("qualification")
    if not base.endswith("/" + q.qualification_path):
        raise SystemExit(f"[urls] qualification {base} does not end with [qualification] path {q.qualification_path}")
    dest = site / q.qualification_path
    if dest.exists():
        raise SystemExit(f"{dest} already exists in the site; refusing to overwrite it")
    with tempfile.TemporaryDirectory() as tmp:
        res = build(q, base, Path(tmp) / "q")
        shutil.copytree(res.out_dir, dest)
    for html in dest.rglob("*.html"):
        text = html.read_text(encoding="utf-8")
        if NOINDEX not in text:
            html.write_text(text.replace("<head>", "<head>\n" + NOINDEX, 1), encoding="utf-8")
    return dest, q


def verify_served(site: Path, q) -> int:
    from hcgrepo.server import BackgroundServer
    from hcgrepo.verify import verify
    base = q.base_url("qualification")
    with BackgroundServer(site, "/") as srv:
        fetch = srv.url.rstrip("/") + "/" + q.qualification_path
        res = verify(base, fetch, cfg=q, check_debs=True, check_provider=False)
    print(res.summary())
    if res.report.items:
        print(res.report.render())
    return 0 if res.report.ok else 1


def preflight(q) -> int:
    from hcgrepo import storeint
    pv = storeint.provider_section(q)
    api = pv.get("api") or {}
    h = storeint.store_health(q)
    print(json.dumps({"provider": api, "store_health": h}, indent=1))
    if not (api.get("ok") and api.get("name") == "HCG Store"):
        print(f"PREFLIGHT FAILED: {q.payment_provider.url}info does not answer as HCG Store (HTTP {api.get('http_status')})")
        return 1
    if not h.get("ok"):
        print("PREFLIGHT FAILED: HCG Store health is not ok")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--preflight", action="store_true", help="require HCG Store's provider to answer before deploying")
    ap.add_argument("--allow-inactive", action="store_true", help="local dry run while [qualification] active = false")
    a = ap.parse_args()
    site = Path(a.site).resolve()
    before = {p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file()}
    dest, q = build_qualification(site, a.allow_inactive)
    after = {p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file()}
    outside = sorted(x for x in after - before if not x.startswith(q.qualification_path))
    if outside or not before <= after:
        print(f"REFUSED: files outside {q.qualification_path} changed: {outside[:5]}")
        return 1
    print(f"qualification source at /{q.qualification_path}repo/ ({len(after - before)} files)")
    rc = verify_served(site, q)
    if rc == 0 and a.preflight:
        rc = preflight(q)
    return rc


if __name__ == "__main__":
    sys.exit(main())

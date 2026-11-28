#!/usr/bin/env python3
"""Prove that a rebuilt site is byte-identical to what is live (docs/QUALIFICATION_SILEO.md, "Deploy").

  tools/qualification/compare_live.py <site-dir> <public-base-url>

Every file of <site-dir> is fetched from <public-base-url> and compared by SHA-256 (.nojekyll is a GitHub Pages control
file and is never served). Exit 1 on any difference or fetch failure: the qualification deploy then stops, so the
public sources can only ever be redeployed exactly as they are.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

SKIP = {".nojekyll"}


def main() -> int:
    site, base = Path(sys.argv[1]), sys.argv[2].rstrip("/") + "/"
    files = sorted(p for p in site.rglob("*") if p.is_file() and p.relative_to(site).as_posix() not in SKIP)
    bad = 0
    for p in files:
        rel = p.relative_to(site).as_posix()
        try:
            with urllib.request.urlopen(base + urllib.request.quote(rel), timeout=120) as r:
                live = r.read()
        except Exception as exc:  # noqa: BLE001 - any failure stops the deploy
            print(f"FETCH FAILED {rel}: {exc}")
            bad += 1
            continue
        if hashlib.sha256(live).digest() != hashlib.sha256(p.read_bytes()).digest():
            print(f"DIFFERS {rel}")
            bad += 1
    print(f"{len(files)} files compared with {base}: {'identical' if not bad else f'{bad} differ or failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

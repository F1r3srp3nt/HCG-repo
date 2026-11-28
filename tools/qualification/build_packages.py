#!/usr/bin/env python3
"""Build the two HCG Repo qualification packages (docs/QUALIFICATION_SILEO.md), reproducibly.

  tools/qualification/build_packages.py <out-dir>

com.hakmicapitalgroup.qualification.free  1.0.0  free, served by the qualification source
com.hakmicapitalgroup.qualification.paid  1.0.0  TEST commercial package, held and delivered ONLY by HCG Store

Each is a rootless (iphoneos-arm64) package with no maintainer scripts and no binaries. It installs a small text file
and a shell script under /var/jb that print which package and version is installed, so a tester can confirm an install
in Filza or a terminal. They are qualification fixtures, never listed in the public HCG Repo sources.

The bytes are reproducible: fixed timestamps (SOURCE_DATE_EPOCH), root ownership, sorted gzip members. Anyone can
rebuild them and get the same SHA-256 printed below.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EPOCH = 1795824000            # 2026-11-28T00:00:00Z
VERSION = "1.0.0"
PACKAGES = {
    "free": ("HCG Qualification (Free)", "Free HCG Repo qualification package"),
    "paid": ("HCG Qualification (Paid TEST)", "TEST commercial package for HCG Store payment-provider qualification"),
}


def control(kind: str) -> str:
    name, short = PACKAGES[kind]
    extra = ("It is delivered only by HCG Store after an entitlement check (a sandbox TEST purchase). It has no value "
             "and grants nothing." if kind == "paid" else "It installs from the qualification source like any free package.")
    return (f"Package: com.hakmicapitalgroup.qualification.{kind}\nName: {name}\nVersion: {VERSION}\n"
            "Architecture: iphoneos-arm64\nMaintainer: Hakmi Capital Group <noreply@hakmicapitalgroup.com>\n"
            "Author: Hakmi Capital Group\nSection: Utilities\nDepends: firmware (>= 15.0)\n"
            f"Description: {short}\n Qualification fixture for HCG Repo in Sileo. {extra}\n"
            " Installs /var/jb/usr/share/hcg-qualification and /var/jb/usr/bin/hcg-qualification-" + kind + ". No scripts.\n")


def build(kind: str, out: Path) -> Path:
    pkg = f"com.hakmicapitalgroup.qualification.{kind}"
    with tempfile.TemporaryDirectory() as tmp:
        st = Path(tmp) / "stage"
        (st / "DEBIAN").mkdir(parents=True)
        (st / "DEBIAN" / "control").write_text(control(kind))
        share = st / "var/jb/usr/share/hcg-qualification"
        share.mkdir(parents=True)
        (share / f"{kind}.txt").write_text(
            f"{pkg} {VERSION}\nHCG Repo qualification package ({kind}).\n"
            + ("Delivered by HCG Store after an entitlement check (sandbox TEST purchase).\n" if kind == "paid"
               else "Installed from the HCG Repo qualification source without an account.\n"))
        bindir = st / "var/jb/usr/bin"
        bindir.mkdir(parents=True)
        script = bindir / f"hcg-qualification-{kind}"
        script.write_text(f"#!/var/jb/bin/sh\ncat /var/jb/usr/share/hcg-qualification/{kind}.txt\n")
        for p in sorted(st.rglob("*")):
            os.chmod(p, 0o755 if p.is_dir() or p == script else 0o644)
        for p in sorted(st.rglob("*"), reverse=True):
            os.utime(p, (EPOCH, EPOCH), follow_symlinks=False)
        os.utime(st, (EPOCH, EPOCH))
        target = out / f"{pkg}_{VERSION}_iphoneos-arm64.deb"
        env = dict(os.environ, SOURCE_DATE_EPOCH=str(EPOCH), TZ="UTC", LC_ALL="C")
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zgzip", "-z9", "--build", str(st), str(target)],
                       check=True, capture_output=True, env=env)
    return target


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not shutil.which("dpkg-deb"):
        print("dpkg-deb is required")
        return 2
    for kind in PACKAGES:
        p = build(kind, out)
        print(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.stat().st_size:>6}  {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

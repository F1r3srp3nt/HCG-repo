"""Build the keyring package that installs HCG Repo's public archive key on a device.

Sileo verifies Release.gpg only against keys in <jbroot>/etc/apt/trusted.gpg.d/ (see docs/SIGNING.md),
so signing protects users only once this package is installed. The package contains exactly one file,
the dearmored public key, and no maintainer scripts. It is built from keys/hcg-repo-archive-key.asc and
refuses to build if that file is missing or does not contain the configured fingerprint.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import RepoConfig
from .sign import SignError, key_fingerprints

PACKAGE = "com.hakmicapitalgroup.repo-keyring"
LAYOUTS = {
    "rootless": ("iphoneos-arm64", "var/jb/etc/apt/trusted.gpg.d/hcg-repo.gpg"),
    "rootful": ("iphoneos-arm", "etc/apt/trusted.gpg.d/hcg-repo.gpg"),
}


def build_keyring_deb(cfg: RepoConfig, layout: str, version: str, out_dir: Path) -> Path:
    if layout not in LAYOUTS:
        raise SignError(f"layout must be one of {sorted(LAYOUTS)}")
    pub = cfg.root / cfg.public_key_file
    if not pub.exists():
        raise SignError(f"{cfg.public_key_file} does not exist yet; create the archive key first (docs/SIGNING.md)")
    if not cfg.signing_fingerprint:
        raise SignError("config/repo.toml [signing] fingerprint is empty")
    fprs = key_fingerprints(pub)
    if cfg.signing_fingerprint not in fprs:
        raise SignError(f"{cfg.public_key_file} does not contain the configured fingerprint {cfg.signing_fingerprint}")
    arch, dest = LAYOUTS[layout]
    gpg = shutil.which("gpg")
    if not gpg:
        raise SignError("gpg is required")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "stage"
        target = stage / dest
        target.parent.mkdir(parents=True)
        p = subprocess.run([gpg, "--batch", "--yes", "--dearmor", "--output", str(target), str(pub)],
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise SignError(f"gpg --dearmor failed: {p.stderr.strip()}")
        os.chmod(target, 0o644)
        for d in [stage, *[q for q in target.parents if stage in q.parents]]:
            os.chmod(d, 0o755)
        (stage / "DEBIAN").mkdir()
        (stage / "DEBIAN" / "control").write_text(
            f"Package: {PACKAGE}\n"
            "Name: HCG Repo Archive Key\n"
            f"Version: {version}\n"
            f"Architecture: {arch}\n"
            "Maintainer: Hakmi Capital Group\n"
            "Author: Hakmi Capital Group\n"
            "Section: Repositories\n"
            f"Description: OpenPGP key that verifies HCG Repo's signed Release files.\n"
            f" Fingerprint {cfg.signing_fingerprint}. Installs only /{dest} and runs no scripts.\n")
        deb = out_dir / f"{PACKAGE}_{version}_{arch}.deb"
        p = subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "-b", str(stage), str(deb)],
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise SignError(f"dpkg-deb failed: {p.stderr.strip()}")
    return deb

"""HCG Repo Keyring: the package that installs HCG Repo's public archive key(s) on a device (docs/KEYRING.md).

Sileo verifies Release.gpg only against keys in <jbroot>/etc/apt/trusted.gpg.d/ (APT reads the same directory), so
repository signing protects a user only once this package is installed. The package:

  <jbroot>/etc/apt/trusted.gpg.d/hcg-repo.gpg           the dearmored public key(s), one binary keyring
  <jbroot>/usr/share/hcg-repo-keyring/FINGERPRINTS      fingerprint, user ID and expiry of every included key
  <jbroot>/usr/share/hcg-repo-keyring/hcg-repo-archive-key.asc   armored copy, for inspection

- **no maintainer scripts and no conffiles**: an update replaces the key file and removal deletes it, nothing else on
  the device is touched (other APT keys are never modified);
- **key updates and rotation**: a new version replaces the keyring; a transition version carries the old and the new
  key together (`extra_keys`), so devices keep verifying across a rotation;
- **production builds** contain exactly the committed key with the configured fingerprint, plus approved transition
  keys; **test builds** (`test=True`) carry `X-HCG-Test-Key: yes`, a "TEST" name and a `~test` version, and HCG Repo's
  importer refuses them, so a development key can never be published.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .config import RepoConfig
from .sign import SignError, key_status

PACKAGE = "com.hakmicapitalgroup.repo-keyring"
TEST_FIELD = "X-HCG-Test-Key"
LAYOUTS = {
    "rootless": ("iphoneos-arm64", "var/jb"),
    "rootful": ("iphoneos-arm", ""),
}
KEYRING_REL = "etc/apt/trusted.gpg.d/hcg-repo.gpg"
SHARE_REL = "usr/share/hcg-repo-keyring"


def _p(prefix: str, rel: str) -> str:
    return f"{prefix}/{rel}" if prefix else rel


def fingerprints_text(keys: list[Path]) -> str:
    lines = ["# HCG Repo Keyring: keys trusted for HCG Repo's signed Release files.",
             "# Compare each fingerprint with docs/KEYRING.md on GitHub and with the HCG Repo website before relying on it.",
             ""]
    for k in keys:
        st = key_status(k)
        prim = st["keys"][0]
        exp = (datetime.fromtimestamp(prim["expires"], tz=timezone.utc).date().isoformat() if prim["expires"] else "never")
        lines.append(f"{st['primary_fpr']}  {', '.join(st['uids'])}  (primary expires {exp})")
        for s in st["keys"][1:]:
            sexp = (datetime.fromtimestamp(s["expires"], tz=timezone.utc).date().isoformat() if s["expires"] else "never")
            lines.append(f"  subkey {s['fpr']}  usage={s['capabilities']}  expires {sexp}"
                         + ("  REVOKED" if s["revoked"] else ""))
    return "\n".join(lines) + "\n"


def build_keyring_deb(cfg: RepoConfig, layout: str, version: str, out_dir: Path,
                      extra_keys: list[Path] | None = None, test_key: Path | None = None) -> Path:
    """Build the keyring package for one layout.

    Production: keys/hcg-repo-archive-key.asc must exist and hold the configured fingerprint; `extra_keys` adds
    transition keys for a rotation. Test: `test_key` replaces the production key and marks the package as a test.
    """
    if layout not in LAYOUTS:
        raise SignError(f"layout must be one of {sorted(LAYOUTS)}")
    test = test_key is not None
    if test:
        keys = [Path(test_key)] + [Path(k) for k in extra_keys or []]
        if not version.endswith("~test"):
            version += "~test"
    else:
        pub = cfg.root / cfg.public_key_file
        if not pub.exists():
            raise SignError(f"{cfg.public_key_file} does not exist yet; create the archive key first (docs/SIGNING.md)")
        if not cfg.signing_fingerprint:
            raise SignError("config/repo.toml [signing] fingerprint is empty: production keyrings need the approved "
                            "fingerprint (use test_key for development)")
        if key_status(pub)["primary_fpr"].upper() != cfg.signing_fingerprint.upper():
            raise SignError(f"{cfg.public_key_file} is not the configured key {cfg.signing_fingerprint}")
        keys = [pub] + [Path(k) for k in extra_keys or []]
    statuses = [key_status(k) for k in keys]
    for k, st in zip(keys, statuses):
        if st["primary_revoked"] or st["primary_expired"] or not st["usable_signing_keys"]:
            raise SignError(f"{k.name}: the key is revoked, expired or cannot sign; it must not be trusted")
    arch, prefix = LAYOUTS[layout]
    gpg = shutil.which("gpg")
    if not gpg:
        raise SignError("gpg is required")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "stage"
        ring = stage / _p(prefix, KEYRING_REL)
        share = stage / _p(prefix, SHARE_REL)
        ring.parent.mkdir(parents=True)
        share.mkdir(parents=True)
        blobs = []
        for k in keys:
            out = Path(tmp) / (k.name + ".gpg")
            p = subprocess.run([gpg, "--batch", "--yes", "--homedir", tmp, "--dearmor", "--output", str(out), str(k)],
                               capture_output=True, text=True)
            if p.returncode != 0:
                raise SignError(f"gpg --dearmor {k.name} failed: {p.stderr.strip()}")
            blobs.append(out.read_bytes())
        ring.write_bytes(b"".join(blobs))
        (share / "FINGERPRINTS").write_text(fingerprints_text(keys), encoding="utf-8")
        (share / "hcg-repo-archive-key.asc").write_text("".join(k.read_text() for k in keys), encoding="utf-8")
        for f in (ring, share / "FINGERPRINTS", share / "hcg-repo-archive-key.asc"):
            os.chmod(f, 0o644)
        for d in [p for p in stage.rglob("*") if p.is_dir()] + [stage]:
            os.chmod(d, 0o755)
        (stage / "DEBIAN").mkdir()
        fprs = ", ".join(st["primary_fpr"] for st in statuses)
        name = "HCG Repo Keyring" + (" (TEST KEY, NOT FOR PRODUCTION)" if test else "")
        control = (f"Package: {PACKAGE}\nName: {name}\nVersion: {version}\nArchitecture: {arch}\n"
                   "Maintainer: Hakmi Capital Group\nAuthor: Hakmi Capital Group\nSection: Repositories\n"
                   + (f"{TEST_FIELD}: yes\n" if test else "")
                   + f"Description: OpenPGP key(s) that verify HCG Repo's signed Release files.\n"
                   f" Trusted fingerprint(s): {fprs}.\n"
                   f" Installs only /{_p(prefix, KEYRING_REL)} and /{_p(prefix, SHARE_REL)}/; runs no scripts.\n")
        (stage / "DEBIAN" / "control").write_text(control)
        deb = out_dir / f"{PACKAGE}_{version}_{arch}.deb"
        p = subprocess.run(["dpkg-deb", "--root-owner-group", "-Zgzip", "-b", str(stage), str(deb)],
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise SignError(f"dpkg-deb failed: {p.stderr.strip()}")
    return deb


def is_test_keyring(control_fields: list[tuple[str, str]]) -> bool:
    return any(k.lower() == TEST_FIELD.lower() and v.strip().lower() == "yes" for k, v in control_fields)

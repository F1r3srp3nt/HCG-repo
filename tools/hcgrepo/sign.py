"""OpenPGP signing of APT repository metadata (Release.gpg and InRelease).

This is *repository metadata* signing. It has nothing to do with iOS code signing (ldid /
entitlements), which happens when a package is built, before it reaches this repository.

The private key never lives in Git. CI imports it from a secret into a throw-away GNUPGHOME.
After signing, both signatures are verified with gpgv against the public key committed in
keys/, so a build reports "verified" only when that check really passed.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class SignError(Exception):
    pass


@dataclass
class SignResult:
    signed: bool
    verified: bool
    fingerprint: str
    detail: str


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def key_fingerprints(pubkey: Path) -> list[str]:
    gpg = shutil.which("gpg")
    if not gpg:
        raise SignError("gpg not found")
    with tempfile.TemporaryDirectory() as home:
        p = _run([gpg, "--homedir", home, "--batch", "--with-colons", "--show-keys", str(pubkey)])
        if p.returncode != 0:
            raise SignError(f"cannot read public key {pubkey}: {p.stderr.strip()}")
    return [line.split(":")[9] for line in p.stdout.splitlines() if line.startswith("fpr:")]


def sign_release(repo_dir: Path, key_id: str, pubkey: Path, expected_fpr: str) -> SignResult:
    gpg = shutil.which("gpg")
    gpgv = shutil.which("gpgv")
    if not gpg or not gpgv:
        raise SignError("gpg and gpgv are required for signing")
    if not pubkey.exists():
        raise SignError(f"public key {pubkey} is not committed; see docs/SIGNING.md")
    fprs = key_fingerprints(pubkey)
    if expected_fpr and expected_fpr not in fprs:
        raise SignError(f"config fingerprint {expected_fpr} is not in {pubkey} ({', '.join(fprs)})")
    release = repo_dir / "Release"
    common = [gpg, "--batch", "--yes", "--pinentry-mode", "loopback", "--local-user", key_id,
              "--digest-algo", "SHA512"]
    for args in (["--armor", "--detach-sign", "--output", str(repo_dir / "Release.gpg"), str(release)],
                 ["--clearsign", "--output", str(repo_dir / "InRelease"), str(release)]):
        p = _run(common + args)
        if p.returncode != 0:
            raise SignError(f"gpg failed: {p.stderr.strip()}")
    shutil.copyfile(pubkey, repo_dir / pubkey.name)
    ok, detail = verify_signatures(repo_dir, pubkey)
    return SignResult(True, ok, expected_fpr or (fprs[0] if fprs else ""), detail)


def verify_signatures(repo_dir: Path, pubkey: Path) -> tuple[bool, str]:
    """Verify Release.gpg and InRelease with gpgv against only `pubkey`."""
    gpg = shutil.which("gpg")
    gpgv = shutil.which("gpgv")
    if not gpg or not gpgv:
        return False, "gpg/gpgv not installed; signatures NOT verified"
    with tempfile.TemporaryDirectory() as tmp:
        keyring = Path(tmp) / "keyring.gpg"
        p = _run([gpg, "--batch", "--yes", "--output", str(keyring), "--dearmor", str(pubkey)])
        if p.returncode != 0:
            return False, f"cannot dearmor {pubkey}: {p.stderr.strip()}"
        results = []
        for args in ([str(repo_dir / "Release.gpg"), str(repo_dir / "Release")], [str(repo_dir / "InRelease")]):
            v = _run([gpgv, "--keyring", str(keyring)] + args)
            results.append((Path(args[0]).name, v.returncode == 0, v.stderr.strip().splitlines()[-1:] or [""]))
        ok = all(r[1] for r in results)
        if ok:
            # InRelease must carry exactly the Release we published.
            p = _run([gpg, "--batch", "--homedir", tmp, "--no-default-keyring", "--keyring", str(keyring),
                      "--decrypt", str(repo_dir / "InRelease")])
            if p.returncode != 0 or p.stdout != (repo_dir / "Release").read_text(encoding="utf-8"):
                return False, "InRelease content does not match Release"
        detail = "; ".join(f"{n}: {'good signature' if good else 'FAILED'} ({msg[0]})" for n, good, msg in results)
        return ok, detail

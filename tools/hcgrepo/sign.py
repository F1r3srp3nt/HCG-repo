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
    problems = [m for lvl, _, m in check_key(pubkey, expected_fpr) if lvl == "error"]
    if problems:
        raise SignError("refusing to sign: " + "; ".join(problems))
    release = repo_dir / "Release"
    common = [gpg, "--batch", "--yes", "--pinentry-mode", "loopback", "--local-user", key_id,
              "--digest-algo", "SHA512"]
    for args in (["--armor", "--detach-sign", "--output", str(repo_dir / "Release.gpg"), str(release)],
                 ["--clearsign", "--output", str(repo_dir / "InRelease"), str(release)]):
        p = _run(common + args)
        if p.returncode != 0:
            raise SignError(f"gpg failed: {p.stderr.strip()}")
    shutil.copyfile(pubkey, repo_dir / pubkey.name)
    ok, detail = verify_signatures(repo_dir, pubkey, expected_fpr)
    return SignResult(True, ok, expected_fpr or (fprs[0] if fprs else ""), detail)


# OpenPGP hash algorithm ids (RFC 4880 §9.4): 8 SHA256, 9 SHA384, 10 SHA512, 11 SHA224. SHA-1 (2) and MD5 (1) refused.
STRONG_DIGESTS = {"8", "9", "10", "11"}


def _validsigs(status: str) -> list[dict]:
    """[GNUPG:] VALIDSIG <signing key fpr> <date> <ts> <expire> <ver> <rsv> <pk algo> <hash algo> <class> <primary fpr>"""
    out = []
    for line in status.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "[GNUPG:]" and parts[1] == "VALIDSIG":
            f = parts[2:]
            out.append({"signing_fpr": f[0], "hash_algo": f[7] if len(f) > 7 else "",
                        "primary_fpr": f[9] if len(f) > 9 else f[0]})
    return out


def verify_signatures(repo_dir: Path, pubkey: Path, expected_fpr: str = "") -> tuple[bool, str]:
    """Verify Release.gpg and InRelease with gpgv against only `pubkey`.

    Both signatures must be good, made with a strong digest, and, when `expected_fpr` is given, made by that primary
    key (or one of its subkeys); InRelease must carry exactly the Release that is published.
    """
    gpg = shutil.which("gpg")
    gpgv = shutil.which("gpgv")
    if not gpg or not gpgv:
        return False, "gpg/gpgv not installed; signatures NOT verified"
    expected_fpr = (expected_fpr or "").replace(" ", "").upper()
    with tempfile.TemporaryDirectory() as tmp:
        keyring = Path(tmp) / "keyring.gpg"
        p = _run([gpg, "--batch", "--yes", "--homedir", tmp, "--output", str(keyring), "--dearmor", str(pubkey)])
        if p.returncode != 0:
            return False, f"cannot dearmor {pubkey}: {p.stderr.strip()}"
        results = []
        for args in ([str(repo_dir / "Release.gpg"), str(repo_dir / "Release")], [str(repo_dir / "InRelease")]):
            name = Path(args[0]).name
            if not Path(args[0]).exists():
                results.append((name, False, "missing"))
                continue
            v = _run([gpgv, "--status-fd", "1", "--keyring", str(keyring)] + args)
            sigs = _validsigs(v.stdout)
            problem = ""
            if v.returncode != 0 or not sigs:
                problem = (v.stderr.strip().splitlines() or ["bad signature"])[-1]
            elif any(sg["hash_algo"] not in STRONG_DIGESTS for sg in sigs):
                problem = "weak digest algorithm (SHA-1 or older)"
            elif expected_fpr and any(sg["primary_fpr"].upper() != expected_fpr for sg in sigs):
                problem = (f"signed by {sigs[0]['primary_fpr']}, not the expected key {expected_fpr}")
            signer = sigs[0]["signing_fpr"] if sigs else ""
            results.append((name, not problem, problem or f"good signature, signing key {signer[-16:]}"))
        ok = all(r[1] for r in results)
        if ok:
            # InRelease must carry exactly the Release we published.
            p = _run([gpg, "--batch", "--homedir", tmp, "--no-default-keyring", "--keyring", str(keyring),
                      "--decrypt", str(repo_dir / "InRelease")])
            if p.returncode != 0 or p.stdout != (repo_dir / "Release").read_text(encoding="utf-8"):
                return False, "InRelease content does not match Release"
        detail = "; ".join(f"{n}: {'verified' if good else 'FAILED'} ({msg})" for n, good, msg in results)
        return ok, detail


# ---------------------------------------------------------------------------- key status (expiry, revocation)


def key_status(pubkey: Path, now: float | None = None) -> dict:
    """What the public key file says about itself: primary key, subkeys, capabilities, expiry, revocation.
    Reads only public material."""
    import time
    gpg = shutil.which("gpg")
    if not gpg:
        raise SignError("gpg not found")
    now = time.time() if now is None else now
    with tempfile.TemporaryDirectory() as home:
        p = _run([gpg, "--homedir", home, "--batch", "--with-colons", "--fixed-list-mode", "--show-keys", str(pubkey)])
        if p.returncode != 0:
            raise SignError(f"cannot read public key {pubkey}: {p.stderr.strip()}")
    keys: list[dict] = []
    uids: list[str] = []
    for line in p.stdout.splitlines():
        f = line.split(":")
        if f[0] in ("pub", "sub"):
            exp = int(f[6]) if f[6] else None
            keys.append({"kind": "primary" if f[0] == "pub" else "subkey", "validity": f[1],
                         "revoked": f[1] == "r", "algo": f[3], "bits": f[2], "created": int(f[5]) if f[5] else None,
                         "expires": exp, "expired": bool(exp and exp <= now), "capabilities": f[11], "fpr": ""})
        elif f[0] == "fpr" and keys and not keys[-1]["fpr"]:
            keys[-1]["fpr"] = f[9]
        elif f[0] == "uid":
            uids.append(f[9])
    if not keys or keys[0]["kind"] != "primary":
        raise SignError(f"{pubkey} holds no primary key")
    primary = keys[0]
    signers = [k for k in keys if "s" in k["capabilities"] and not k["revoked"] and not k["expired"]]  # lowercase: own capability
    days = None
    exps = [e for e in [primary["expires"]] + [k["expires"] for k in signers] if e]
    if exps and signers:
        # The repository can be signed until the later of the usable signing keys expires, and never after the primary.
        latest_signer = max((k["expires"] or float("inf")) for k in signers)
        limit = min(latest_signer, primary["expires"] or float("inf"))
        days = None if limit == float("inf") else int((limit - now) // 86400)
    return {"primary_fpr": primary["fpr"], "uids": uids, "keys": keys,
            "primary_revoked": primary["revoked"], "primary_expired": primary["expired"],
            "usable_signing_keys": [k["fpr"] for k in signers], "days_until_signing_expiry": days}


def check_key(pubkey: Path, expected_fpr: str, warn_days: int = 60, fail_days: int = 14) -> list[tuple[str, str, str]]:
    """Problems with the published key: [(level, code, message)]. Used before signing and by monitoring."""
    out: list[tuple[str, str, str]] = []
    try:
        st = key_status(pubkey)
    except SignError as exc:
        return [("error", "key-unreadable", str(exc))]
    exp = (expected_fpr or "").replace(" ", "").upper()
    if exp and st["primary_fpr"].upper() != exp:
        out.append(("error", "key-fingerprint", f"{pubkey.name} primary key is {st['primary_fpr']}, configured {exp}"))
    if st["primary_revoked"]:
        out.append(("error", "key-revoked", "the primary key is revoked"))
    if st["primary_expired"]:
        out.append(("error", "key-expired", "the primary key has expired"))
    if not st["usable_signing_keys"]:
        out.append(("error", "key-no-signing-key", "no unexpired, unrevoked key or subkey can sign"))
    d = st["days_until_signing_expiry"]
    if d is not None and d < fail_days:
        out.append(("error", "key-expiring", f"signing ability ends in {d} days; extend the expiry or rotate now"))
    elif d is not None and d < warn_days:
        out.append(("warning", "key-expiring", f"signing ability ends in {d} days; extend the expiry before then"))
    return out

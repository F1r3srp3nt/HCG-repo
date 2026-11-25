"""Publication safeguards and read-only monitoring (docs/SIGNING.md, docs/SENTINEL_MONITORING.md).

Before deploy (`bin/hcgrepo guard --site _site --against <target>`):
  - every package in each built index (Stable and Candidate) has exactly the SHA-256 recorded in repo-lock.json, and
    the file in debs/ has those bytes;
  - every published public lock entry of that channel is in the index (no missing package);
  - against the live repository: a version already published keeps the same bytes; a version may only disappear
    when the lock marks it withdrawn; the signature must not be dropped once the live source is signed; the signing
    key must not change unless the rotation is acknowledged (--allow-key-change).
After deploy (`--deployed manifest.json`): the live metadata files are byte-identical to what the build produced.
Monitoring (`bin/hcgrepo monitor`): the same facts, read-only, for HCG Sentinel. Nothing here writes or remediates.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import debian
from .config import Report, RepoConfig, entry_channel, load_lock, load_package_meta
from .verify import Fetcher, parse_release_hashes

INDEX_FILES = ("Release", "InRelease", "Release.gpg", "Packages", "Packages.gz", "Packages.xz", "Packages.bz2",
               "Packages.zst")
CHANNEL_DIRS = {"stable": "", "candidate": "candidate/"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _index(text: str) -> dict[tuple[str, str, str], dict]:
    out = {}
    for st in debian.parse_stanzas(text):
        g = dict(st)
        if g.get("Package"):
            out[(g["Package"], g.get("Version", ""), g.get("Architecture", ""))] = g
    return out


def _expected(cfg: RepoConfig, channel: str) -> dict[tuple[str, str, str], dict]:
    """Lock entries that a build of `channel` must index: published and publicly downloadable."""
    out = {}
    for e in load_lock(cfg)["packages"]:
        if entry_channel(e) != channel or e.get("status", "published") != "published":
            continue
        if load_package_meta(cfg, e["package"]).access_for(e["version"]) != "public":
            continue
        out[(e["package"], e["version"], e["architecture"])] = e
    return out


def check_site(cfg: RepoConfig, site: Path) -> Report:
    """Exact checksums and completeness of a built site, for Stable and Candidate."""
    r = Report("built site")
    lock = {(e["package"], e["version"], e["architecture"]): e for e in load_lock(cfg)["packages"]}
    for channel, sub in CHANNEL_DIRS.items():
        repo = site / cfg.repo_subdir / sub
        pk = repo / "Packages"
        if not pk.is_file():
            r.error("index-missing", f"{cfg.repo_subdir}/{sub}Packages is missing")
            continue
        idx = _index(pk.read_text(encoding="utf-8"))
        rel = repo / "Release"
        if rel.is_file():
            hashes = parse_release_hashes(debian.parse_control(rel.read_text(encoding="utf-8")))
            want = (hashes.get("sha256") or {}).get("Packages")
            if not want or want[0] != _sha(pk.read_bytes()):
                r.error("index-inconsistent", f"{channel}: Release does not list Packages' SHA-256")
        for key, g in idx.items():
            fn = g.get("Filename", "")
            if fn.startswith("protected/"):
                continue        # commercial: indexed, never deployed (docs/COMMERCIAL.md)
            e = lock.get(key)
            label = f"{channel} {key[0]} {key[1]} {key[2]}"
            if e is None:
                r.error("unexpected-package", f"{label} is indexed but not in repo-lock.json")
                continue
            if g.get("SHA256") != e["sha256"]:
                r.error("checksum-mismatch", f"{label}: index SHA-256 {g.get('SHA256')} is not the locked {e['sha256']}")
            f = repo / fn
            if not f.is_file():
                r.error("package-file-missing", f"{label}: {fn} is not in the built site")
            elif _sha(f.read_bytes()) != e["sha256"]:
                r.error("checksum-mismatch", f"{label}: {fn} does not have the locked bytes")
        for key in _expected(cfg, channel):
            if key not in idx:
                r.error("package-missing", f"{channel} {' '.join(key)} is published in repo-lock.json but not indexed")
    return r


def manifest(cfg: RepoConfig, site: Path) -> dict:
    """SHA-256 of every metadata file the deploy must serve unchanged (both channels) plus the revision."""
    files = {}
    for sub in CHANNEL_DIRS.values():
        for name in INDEX_FILES:
            p = site / cfg.repo_subdir / sub / name
            if p.is_file():
                files[f"{cfg.repo_subdir}/{sub}{name}"] = _sha(p.read_bytes())
    info = json.loads((site / "build-info.json").read_text(encoding="utf-8"))
    return {"revision": info.get("revision"), "files": files}


def _live(f: Fetcher, base: str, path: str) -> tuple[int, bytes]:
    code, data, _ = f.get(base + path)
    return code, data


def _signer(sig: bytes) -> str:
    """Issuer of a detached signature (the signing key or subkey fingerprint, else its key ID), read from the packet
    itself with gpg --list-packets: no keyring needed, so it also works for a key that is no longer committed."""
    import re
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "Release.gpg").write_bytes(sig)
        p = subprocess.run(["gpg", "--homedir", tmp, "--batch", "--list-packets", str(Path(tmp, "Release.gpg"))],
                           capture_output=True, text=True)
    m = re.search(r"issuer fpr v\d ([0-9A-Fa-f]{40,64})", p.stdout) or re.search(r"keyid ([0-9A-Fa-f]{16})", p.stdout)
    return m.group(1).upper() if m else ""


def compare_live(cfg: RepoConfig, site: Path, public_base: str, fetch_base: str | None = None,
                 allow_key_change: bool = False) -> Report:
    """The new build against what is live now: no silent change of published bytes, no silent removal, no signing
    downgrade, no unacknowledged key change."""
    r = Report("against live")
    f = Fetcher(public_base, fetch_base or public_base)
    withdrawn = {(e["package"], e["version"], e["architecture"]) for e in load_lock(cfg)["packages"]
                 if e.get("status") == "withdrawn"}
    for channel, sub in CHANNEL_DIRS.items():
        path = f"{cfg.repo_subdir}/{sub}"
        code, live = _live(f, public_base, path + "Packages")
        if code != 200:
            r.warn("live-unavailable", f"{channel}: live Packages returned HTTP {code}; nothing to compare")
            continue
        old = _index(live.decode("utf-8", "replace"))
        new = _index((site / cfg.repo_subdir / sub / "Packages").read_text(encoding="utf-8"))
        for key, g in old.items():
            label = f"{channel} {' '.join(key)}"
            if key in new:
                if new[key].get("SHA256") != g.get("SHA256"):
                    r.error("unexpected-change", f"{label}: live SHA-256 {g.get('SHA256')} would become "
                                                 f"{new[key].get('SHA256')}; published bytes never change")
            elif key in withdrawn:
                r.warn("withdrawal", f"{label} leaves the index (withdrawn in repo-lock.json)")
            else:
                r.error("unexpected-removal", f"{label} is live but would disappear without a withdrawal record")
        for key in new.keys() - old.keys():
            r.warn("addition", f"{channel} {' '.join(key)} will be added (SHA-256 {new[key].get('SHA256')})")
        # Signing transitions.
        gcode, gsig = _live(f, public_base, path + "Release.gpg")
        built_sig = site / cfg.repo_subdir / sub / "Release.gpg"
        if gcode == 200 and not built_sig.is_file():
            r.error("signing-downgrade", f"{channel}: the live source is signed; the new build is not")
        elif gcode == 200 and built_sig.is_file():
            live_fpr = _signer(gsig)
            new_fpr = _signer(built_sig.read_bytes())
            if live_fpr and new_fpr and not (live_fpr.endswith(new_fpr) or new_fpr.endswith(live_fpr)) \
                    and not allow_key_change:
                r.error("signing-key-change", f"{channel}: live is signed by {live_fpr}, the new build by {new_fpr}; "
                                              "a key rotation needs --allow-key-change (docs/SIGNING.md, rotation)")
    return r


def check_deployed(cfg: RepoConfig, man: dict, public_base: str, fetch_base: str | None = None) -> Report:
    """After deploy: every metadata file served live is byte-identical to the build."""
    r = Report("deployed")
    f = Fetcher(public_base, fetch_base or public_base)
    for path, want in sorted(man["files"].items()):
        code, data = _live(f, public_base, path)
        if code != 200:
            r.error("deployed-missing", f"{path}: HTTP {code}")
        elif _sha(data) != want:
            r.error("deployed-mismatch", f"{path}: live SHA-256 {_sha(data)} is not the built {want}")
    return r


# ---------------------------------------------------------------------------- read-only monitoring (HCG Sentinel)


MONITOR_FORMAT = "hcg.repo.monitor"


def monitor(cfg: RepoConfig, public_base: str, fetch_base: str | None = None, pubkey: Path | None = None,
            git_ref: str = "origin/main") -> dict:
    """One read-only check of the live repository, in the contract HCG Sentinel consumes
    (docs/SENTINEL_MONITORING.md). Never writes, publishes or remediates."""
    from .sign import check_key, verify_signatures
    f = Fetcher(public_base, fetch_base or public_base)
    checks: list[dict] = []

    def add(cid: str, status: str, detail: str, **extra) -> None:
        checks.append({"id": cid, "status": status, "detail": detail, **extra})

    pubkey = pubkey or (cfg.root / cfg.public_key_file)
    lock = load_lock(cfg)["packages"]
    for channel, sub in CHANNEL_DIRS.items():
        path = f"{cfg.repo_subdir}/{sub}"
        code, rel = _live(f, public_base, path + "Release")
        pcode, pk = _live(f, public_base, path + "Packages")
        add(f"availability.{channel}", "ok" if code == 200 and pcode == 200 else "alert",
            f"Release HTTP {code}, Packages HTTP {pcode}")
        if code != 200 or pcode != 200:
            continue
        hashes = parse_release_hashes(debian.parse_control(rel.decode("utf-8", "replace")))
        want = (hashes.get("sha256") or {}).get("Packages")
        add(f"index.{channel}", "ok" if want and want[0] == _sha(pk) else "alert",
            "Release lists the served Packages" if want and want[0] == _sha(pk) else
            "Release does not match the served Packages (index inconsistency)")
        live = _index(pk.decode("utf-8", "replace"))
        expected = _expected(cfg, channel)
        changed = [f"{' '.join(k)}" for k, g in live.items() if k in expected and g.get("SHA256") != expected[k]["sha256"]]
        unknown = [f"{' '.join(k)}" for k in live if not any((e["package"], e["version"], e["architecture"]) == k
                                                              for e in lock)]
        missing = [f"{' '.join(k)}" for k in expected if k not in live]
        add(f"checksums.{channel}", "alert" if changed else "ok",
            f"{len(changed)} live package(s) differ from repo-lock.json" if changed else "every live checksum matches",
            items=changed)
        add(f"unknown-packages.{channel}", "alert" if unknown else "ok",
            f"{len(unknown)} live package(s) have no lock record (possible unauthorized publication)" if unknown
            else "every live package has a lock record", items=unknown)
        add(f"missing-packages.{channel}", "warning" if missing else "ok",
            f"{len(missing)} published lock record(s) are not live yet (unpublished main or missing)" if missing
            else "nothing missing", items=missing)
        gcode, sig = _live(f, public_base, path + "Release.gpg")
        icode, inrel = _live(f, public_base, path + "InRelease")
        if gcode != 200:
            add(f"signature.{channel}", "alert" if cfg.signing_fingerprint else "warning",
                "unsigned" + (" although a production fingerprint is configured" if cfg.signing_fingerprint else
                              " (no production signing key configured yet)"), signed=False)
        elif not pubkey.is_file():
            add(f"signature.{channel}", "warning", "signed, but no committed public key to verify against", signed=True)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "Release").write_bytes(rel)
                Path(tmp, "Release.gpg").write_bytes(sig)
                Path(tmp, "InRelease").write_bytes(inrel if icode == 200 else b"")
                ok, detail = verify_signatures(Path(tmp), pubkey, cfg.signing_fingerprint)
            add(f"signature.{channel}", "ok" if ok else "alert", detail, signed=True, verified=ok,
                fingerprint=cfg.signing_fingerprint or None)
    if cfg.signing_fingerprint and pubkey.is_file():
        probs = check_key(pubkey, cfg.signing_fingerprint)
        worst = "alert" if any(l == "error" for l, _, _ in probs) else "warning" if probs else "ok"
        from .sign import key_status
        st = key_status(pubkey)
        add("signing-key", worst, "; ".join(m for _, _, m in probs) or "key valid",
            days_until_expiry=st["days_until_signing_expiry"], fingerprint=st["primary_fpr"])
    else:
        add("signing-key", "warning", "no production signing key configured yet")
    code, bi = _live(f, public_base, "build-info.json")
    rev = ""
    if code == 200:
        try:
            rev = json.loads(bi).get("revision", "")
        except ValueError:
            rev = ""
    known = bool(rev) and subprocess.run(["git", "-C", str(cfg.root), "merge-base", "--is-ancestor", rev, git_ref],
                                         capture_output=True).returncode == 0
    add("publication-origin", "ok" if known else "alert",
        f"live build {rev[:12] or '?'} is a commit of {git_ref}" if known else
        f"live build revision {rev[:12] or '(none)'} is not a commit of {git_ref}: unknown or unauthorized publication",
        revision=rev or None)
    order = {"ok": 0, "unknown": 1, "warning": 2, "alert": 3}
    overall = max((c["status"] for c in checks), key=lambda s: order.get(s, 1)) if checks else "unknown"
    return {"format": MONITOR_FORMAT, "formatVersion": 1, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "target": public_base, "status": overall, "read_only": True, "checks": checks}

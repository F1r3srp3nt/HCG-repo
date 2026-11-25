"""Cryptographic release approvals (format `hcg.release-approval` v1, docs/RELEASE_APPROVALS.md).

An approval is a canonical JSON statement signed (detached, armored OpenPGP) with a **founder approval key**: a key
dedicated to approving, kept by the founder, separate from the repository signing key. Two kinds, never merged:

  artifact      binds: package, version, architecture, SHA-256, distribution channel, source repository and commit,
                approved access policy, timestamp. It authorizes these exact bytes for that channel.
  publication   binds: the HCG-repo commit being published, the target, the channels, timestamp. It authorizes
                deploying that exact repository state. It approves no artifact by itself.

Files: approvals/<kind>/<name>.json (canonical: sorted keys, compact) + <name>.json.asc (detached signature over the
exact bytes). Verification uses only the approver keys listed in config/repo.toml [approvals] approver_keys, refuses
any approver key that is (or contains) the repository signing key, and checks every bound field against the
repository's own records. Enforcement is opt-in ([approvals] required_for_import / required_for_publish).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .config import Report, RepoConfig, entry_channel, load_lock, load_package_meta

FORMAT = "hcg.release-approval"
KINDS = ("artifact", "publication")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
FPR = re.compile(r"^[0-9A-F]{40}$")


class ApprovalError(Exception):
    pass


def canonical(stmt: dict) -> bytes:
    return (json.dumps(stmt, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", s)


def statement_path(cfg: RepoConfig, stmt: dict) -> Path:
    if stmt["kind"] == "artifact":
        name = f"{stmt['package']}_{stmt['version']}_{stmt['architecture']}_{stmt['channel']}"
    else:
        name = f"{stmt['target']}_{stmt['repository_commit'][:12]}"
    return cfg.root / "approvals" / stmt["kind"] / (_safe(name) + ".json")


def draft_artifact(cfg: RepoConfig, package: str, version: str, architecture: str, channel: str, approver: str,
                   sha256: str | None = None, repository: str | None = None, commit: str | None = None,
                   note: str = "") -> dict:
    """Prepare an artifact approval from the repository's records (lock + provenance), or from explicit values for a
    build not yet imported (the Release Manager's reviewed SHA-256). The founder checks it, then signs it offline."""
    entry = next((e for e in load_lock(cfg)["packages"]
                  if (e["package"], e["version"], e["architecture"]) == (package, version, architecture)), None)
    prov = {}
    if entry:
        pp = cfg.root / (entry["filename"] + ".provenance.json")
        prov = json.loads(pp.read_text(encoding="utf-8")) if pp.is_file() else {}
        if sha256 and sha256.lower() != entry["sha256"]:
            raise ApprovalError(f"{package} {version} is recorded with sha256 {entry['sha256']}, not {sha256}")
        sha256 = entry["sha256"]
    if not sha256 or not SHA64.match(sha256.lower()):
        raise ApprovalError("the exact SHA-256 is required (from the lock, or --sha256 for a reviewed submission)")
    repository = repository or prov.get("repository")
    commit = (commit or prov.get("commit") or "").lower()
    if not repository or not SHA40.match(commit):
        raise ApprovalError("the source repository and its full 40-character commit are required")
    if channel not in ("stable", "candidate"):
        raise ApprovalError("channel must be stable or candidate")
    return {"format": FORMAT, "formatVersion": 1, "kind": "artifact", "package": package, "version": version,
            "architecture": architecture, "sha256": sha256.lower(), "channel": channel,
            "source": {"repository": repository, "commit": commit},
            "access": load_package_meta(cfg, package).access_for(version), "approved_at": _now(),
            "approver": approver.replace(" ", "").upper(), "note": note}


def draft_publication(cfg: RepoConfig, commit: str, target: str, approver: str, note: str = "") -> dict:
    commit = commit.lower()
    if not SHA40.match(commit):
        raise ApprovalError("the HCG-repo commit must be a full 40-character SHA")
    return {"format": FORMAT, "formatVersion": 1, "kind": "publication", "repository_commit": commit,
            "target": target, "channels": ["stable", "candidate"], "approved_at": _now(),
            "approver": approver.replace(" ", "").upper(), "note": note}


def write_draft(cfg: RepoConfig, stmt: dict) -> Path:
    path = statement_path(cfg, stmt)
    if path.exists() or Path(str(path) + ".asc").exists():
        raise ApprovalError(f"{path.relative_to(cfg.root)} already exists; approvals are never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(stmt))
    return path


# ---------------------------------------------------------------------------- verification


def approver_keys(cfg: RepoConfig) -> list[Path]:
    return [cfg.root / k for k in cfg.approver_keys]


def _fprs(key: Path) -> list[str]:
    from .sign import key_status
    st = key_status(key)
    return [k["fpr"].upper() for k in st["keys"]]


def verify_statement(cfg: RepoConfig, path: Path) -> tuple[dict, Report]:
    """Signature, canonical form, approver identity and separation from the repository signing key."""
    r = Report(path.name)
    try:
        raw = path.read_bytes()
        stmt = json.loads(raw)
    except (OSError, ValueError) as exc:
        r.error("approval-unreadable", str(exc))
        return {}, r
    if stmt.get("format") != FORMAT or stmt.get("formatVersion") != 1 or stmt.get("kind") not in KINDS:
        r.error("approval-format", f"not a {FORMAT} v1 statement")
        return stmt, r
    if raw != canonical(stmt):
        r.error("approval-not-canonical", "the file is not in canonical form (sorted keys, compact); re-draft it")
    sig = Path(str(path) + ".asc")
    if not sig.is_file():
        r.error("approval-unsigned", f"{sig.name} is missing: the statement is a draft, not an approval")
        return stmt, r
    keys = approver_keys(cfg)
    if not keys or not all(k.is_file() for k in keys):
        r.error("approval-no-approver-keys", "config/repo.toml [approvals] approver_keys lists no readable key")
        return stmt, r
    signing_fpr = (cfg.signing_fingerprint or "").upper()
    approver_fprs: set[str] = set()
    for k in keys:
        fs = _fprs(k)
        if signing_fpr and signing_fpr in fs:
            r.error("approval-key-is-signing-key", f"{k.name} contains the repository signing key; approvals need a "
                                                   "separate founder approval key")
        approver_fprs.add(fs[0])
    gpg, gpgv = shutil.which("gpg"), shutil.which("gpgv")
    with tempfile.TemporaryDirectory() as tmp:
        ring = Path(tmp) / "approvers.gpg"
        blobs = b""
        for k in keys:
            out = Path(tmp) / (k.name + ".gpg")
            subprocess.run([gpg, "--batch", "--yes", "--homedir", tmp, "--dearmor", "--output", str(out), str(k)],
                           check=True, capture_output=True)
            blobs += out.read_bytes()
        ring.write_bytes(blobs)
        p = subprocess.run([gpgv, "--status-fd", "1", "--keyring", str(ring), str(sig), str(path)],
                           capture_output=True, text=True)
    from .sign import STRONG_DIGESTS, _validsigs
    sigs = _validsigs(p.stdout)
    if p.returncode != 0 or not sigs:
        r.error("approval-bad-signature", "the signature does not verify against the approver keys")
        return stmt, r
    primary = sigs[0]["primary_fpr"].upper()
    if sigs[0]["hash_algo"] not in STRONG_DIGESTS:
        r.error("approval-weak-digest", "signed with a weak digest")
    if primary not in approver_fprs:
        r.error("approval-unknown-approver", f"signed by {primary}, not a configured approver")
    if signing_fpr and primary == signing_fpr:
        r.error("approval-key-is-signing-key", "signed with the repository signing key")
    if primary != str(stmt.get("approver", "")).upper():
        r.error("approval-approver-mismatch", f"the statement names {stmt.get('approver')} but {primary} signed it")
    return stmt, r


def find_artifact_approval(cfg: RepoConfig, package: str, version: str, architecture: str, channel: str) -> Path:
    return statement_path(cfg, {"kind": "artifact", "package": package, "version": version,
                                "architecture": architecture, "channel": channel})


def check_artifact(cfg: RepoConfig, package: str, version: str, architecture: str, channel: str, sha256: str,
                   commit: str | None = None, repository: str | None = None) -> Report:
    """A verified artifact approval exists and binds exactly these facts."""
    path = find_artifact_approval(cfg, package, version, architecture, channel)
    r = Report(f"{package} {version} {architecture} → {channel}")
    if not path.is_file():
        r.error("approval-missing", f"no artifact approval at {path.relative_to(cfg.root)}")
        return r
    stmt, vr = verify_statement(cfg, path)
    r.extend(vr)
    if stmt.get("kind") != "artifact":
        r.error("approval-kind", "not an artifact approval")
        return r
    want = {"package": package, "version": version, "architecture": architecture, "channel": channel,
            "sha256": sha256.lower(), "access": load_package_meta(cfg, package).access_for(version)}
    for k, v in want.items():
        if stmt.get(k) != v:
            r.error("approval-mismatch", f"{k}: approved {stmt.get(k)!r}, actual {v!r}")
    src = stmt.get("source") or {}
    if commit and src.get("commit") != commit.lower():
        r.error("approval-mismatch", f"source commit: approved {src.get('commit')}, actual {commit.lower()}")
    if repository and src.get("repository") != repository:
        r.error("approval-mismatch", f"source repository: approved {src.get('repository')}, actual {repository}")
    return r


def check_publication(cfg: RepoConfig, commit: str, target: str) -> Report:
    path = statement_path(cfg, {"kind": "publication", "target": target, "repository_commit": commit.lower()})
    r = Report(f"publication {target} @ {commit[:12]}")
    if not path.is_file():
        r.error("approval-missing", f"no publication approval at {path.relative_to(cfg.root)}")
        return r
    stmt, vr = verify_statement(cfg, path)
    r.extend(vr)
    if stmt.get("kind") != "publication" or stmt.get("repository_commit") != commit.lower() or stmt.get("target") != target:
        r.error("approval-mismatch", "the publication approval does not name this commit and target")
    return r


def check_lock_approvals(cfg: RepoConfig, since: str = "") -> Report:
    """With required_for_import: every published lock entry imported at or after `since` has a verified approval."""
    r = Report("artifact approvals")
    for e in load_lock(cfg)["packages"]:
        if e.get("status", "published") != "published" or (since and e.get("imported_at", "") < since):
            continue
        pp = cfg.root / (e["filename"] + ".provenance.json")
        prov = json.loads(pp.read_text(encoding="utf-8")) if pp.is_file() else {}
        r.extend(check_artifact(cfg, e["package"], e["version"], e["architecture"], entry_channel(e), e["sha256"],
                                prov.get("commit"), prov.get("repository")))
    return r

"""HCG Nexus Package Trust Center contracts (docs/NEXUS_TRUST_CENTER.md): read-only, versioned, interchangeable.

- `trust_document`  (`hcg.repo.trust` v1): repository identity, sources, signing status, trusted fingerprints and the
                    other contracts. Published as <repo>/trust.json only with [trust] publish = true (off).
- `verify_artifact` (`hcg.repo.artifact-verification` v1): independent verification of ONE downloaded .deb against the
                    live index: Release → (signature) → Packages → the file's SHA-256 and identity, plus what the file
                    itself contains (entitlements, signatures). It says whether the index it compared against was
                    cryptographically authenticated. It proves nothing about any other file, including a file a
                    package manager downloads or installs later.
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import debian
from .config import RepoConfig
from .debinspect import InspectError, inspect_deb
from .verify import Fetcher, parse_release_hashes

TRUST_FORMAT = "hcg.repo.trust"
VERIFY_FORMAT = "hcg.repo.artifact-verification"
SCOPE_NOTE = ("This result concerns only the file that was checked. It does not show that a different file, such as "
              "one a package manager downloads or installs later, has the same bytes.")


def trust_document(cfg: RepoConfig, base_url: str) -> dict:
    repo = cfg.repo_url(base_url)
    signed = bool(cfg.signing_fingerprint)
    return {
        "format": TRUST_FORMAT, "formatVersion": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "repository": {"name": cfg.label, "origin": cfg.origin, "base_url": base_url,
                       "sources": {"stable": repo, "candidate": repo + "candidate/"}},
        "signing": {
            "status": "signed" if signed else "unsigned",
            "metadata_authenticated": signed,
            "explanation": ("Release files are signed (Release.gpg, InRelease); verify them against a fingerprint "
                            "below that you obtained independently." if signed else
                            "Repository metadata is NOT signed yet. Checksums in the index can be compared, but the "
                            "index itself is not cryptographically authenticated; it relies on HTTPS only."),
            "trusted_fingerprints": [cfg.signing_fingerprint] if signed else [],
            "public_key_urls": ([repo + Path(cfg.public_key_file).name, repo + "candidate/" + Path(cfg.public_key_file).name]
                                if signed else []),
            "keyring_package": "com.hakmicapitalgroup.repo-keyring",
            "fingerprint_sources": ["https://github.com/F1r3srp3nt/HCG-repo/blob/main/docs/KEYRING.md",
                                    base_url + "#install"],
        },
        "contracts": {
            "packages_index": {"stable": repo + "Packages", "candidate": repo + "candidate/Packages"},
            "release": {"stable": repo + "Release", "candidate": repo + "candidate/Release"},
            "compatibility": (repo + "compatibility.json") if cfg.publish_compatibility else None,
            "compatibility_format": "hcg.repo.compatibility v1",
            "release_passport_format": "hcg.release-passport v1 (bin/hcgrepo passport)",
            "artifact_verification_format": f"{VERIFY_FORMAT} v1 (bin/hcgrepo verify-artifact)",
            "monitor_format": "hcg.repo.monitor v1 (bin/hcgrepo monitor)",
        },
        "scope_note": SCOPE_NOTE,
    }


def verify_artifact(cfg: RepoConfig, deb: Path, public_base: str, fetch_base: str | None = None,
                    channel: str = "stable", pubkey: Path | None = None) -> dict:
    """Independent check of one local .deb against the live index of `channel`."""
    from .sign import verify_signatures
    steps: list[dict] = []
    result: dict = {"format": VERIFY_FORMAT, "formatVersion": 1, "file": Path(deb).name, "channel": channel,
                    "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "steps": steps,
                    "scope_note": SCOPE_NOTE}

    def step(name: str, ok: bool, detail: str) -> bool:
        steps.append({"step": name, "ok": ok, "detail": detail})
        return ok

    try:
        info = inspect_deb(Path(deb))
    except InspectError as exc:
        step("read-file", False, str(exc))
        result.update(verdict="failed", metadata_authenticated=False)
        return result
    result["artifact"] = {"package": info.package, "version": info.version, "architecture": info.architecture,
                          "sha256": info.sha256, "size": info.size}
    result["contents"] = {
        "binaries": [{"path": b.path, "signed": bool(b.info and all(s.signed for s in b.info.slices)),
                      "entitlements": sorted(((b.info.slices[0].entitlements or {}) if b.info and b.info.slices else {}).keys())}
                     for b in info.binaries],
        "maintainer_scripts": sorted(info.scripts),
        "note": "code signatures and entitlements as read from this file (application code signing, not repository signing)"}
    f = Fetcher(public_base, fetch_base or public_base)
    sub = cfg.repo_subdir + "/" + ("candidate/" if channel == "candidate" else "")
    code, rel, _ = f.get(public_base + sub + "Release")
    if not step("fetch-release", code == 200, f"HTTP {code}"):
        result.update(verdict="failed", metadata_authenticated=False)
        return result
    gcode, gsig, _ = f.get(public_base + sub + "Release.gpg")
    icode, inrel, _ = f.get(public_base + sub + "InRelease")
    authenticated = False
    if gcode == 200:
        key = pubkey or (cfg.root / cfg.public_key_file)
        if key.is_file():
            with tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "Release").write_bytes(rel)
                Path(tmp, "Release.gpg").write_bytes(gsig)
                Path(tmp, "InRelease").write_bytes(inrel if icode == 200 else b"")
                ok, detail = verify_signatures(Path(tmp), key, cfg.signing_fingerprint)
            authenticated = step("verify-signature", ok, detail)
            if not ok:
                result.update(verdict="failed", metadata_authenticated=False)
                return result
        else:
            step("verify-signature", False, "Release is signed but no trusted public key is available here")
    else:
        step("verify-signature", True if not cfg.signing_fingerprint else False,
             "the index is unsigned: it is NOT cryptographically authenticated (HTTPS only)"
             + ("; a production fingerprint is configured, so this is a failure" if cfg.signing_fingerprint else ""))
        if cfg.signing_fingerprint:
            result.update(verdict="failed", metadata_authenticated=False)
            return result
    hashes = parse_release_hashes(debian.parse_control(rel.decode("utf-8", "replace")))
    pcode, pk, _ = f.get(public_base + sub + "Packages")
    want = (hashes.get("sha256") or {}).get("Packages")
    if not step("index-consistency", pcode == 200 and bool(want) and want[0] == hashlib.sha256(pk).hexdigest(),
                "Release lists the served Packages" if pcode == 200 and want and want[0] == hashlib.sha256(pk).hexdigest()
                else "Packages does not match Release"):
        result.update(verdict="failed", metadata_authenticated=False)
        return result
    entry = None
    for st in debian.parse_stanzas(pk.decode("utf-8", "replace")):
        g = dict(st)
        if (g.get("Package"), g.get("Version"), g.get("Architecture")) == (info.package, info.version, info.architecture):
            entry = g
            break
    if not step("index-entry", entry is not None, f"{info.package} {info.version} {info.architecture} "
                + ("is in the index" if entry else f"is not in the {channel} index")):
        result.update(verdict="failed", metadata_authenticated=authenticated)
        return result
    match = entry.get("SHA256") == info.sha256 and int(entry.get("Size", "-1")) == info.size
    step("checksum", match, f"index SHA-256 {entry.get('SHA256')}, file {info.sha256}")
    result["index_entry"] = {"sha256": entry.get("SHA256"), "size": entry.get("Size"), "filename": entry.get("Filename")}
    result["metadata_authenticated"] = authenticated
    result["verdict"] = ("verified" if match and authenticated else
                         "matches-unauthenticated-index" if match else "failed")
    return result

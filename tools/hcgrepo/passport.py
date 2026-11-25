"""HCG Release Passport (format `hcg.release-passport`, version 1).

One versioned, product-neutral record per released build. It is derived only from HCG Repo's own records: the
immutable lock, the import provenance, package metadata and device tests, the product catalogue and the Release
Manager's submission review. Nothing in a passport is entered by hand, and it adds no new source of truth. Other HCG
systems use the same format (Executable Maker produces the build half, HCG Store adds editions and offers, HCG Server
adds storage), so a passport can be passed between them without translation.

Fields: product identity, edition, platform, architecture, version, source provenance, build provider, artifact
checksum, validation results (the six evidence levels), physical-device evidence, approval history, distribution
channel and publication status.
"""

from __future__ import annotations

import json
from pathlib import Path

from .compat import derive_status
from .config import RepoConfig, entry_channel, load_lock, load_package_meta
from .levels import all_levels, from_legacy
from .submission import PROVIDERS

FORMAT = "hcg.release-passport"
FORMAT_VERSION = 1
PLATFORMS = {"iphoneos-arm64": "ios-jailbreak-rootless", "iphoneos-arm": "ios-jailbreak-rootful",
             "iphoneos-arm64e": "ios-jailbreak-roothide"}


def _provider(prov: dict) -> str:
    if prov.get("provider") in PROVIDERS:
        return prov["provider"]
    return {"github-workflow-artifact": "github-actions", "github-release-asset": "github-actions",
            "source-build": "hcg-repo-build", "hcg-store-release": "hcg-store",
            "local-file": "executable-maker-local"}.get(prov.get("source", ""), prov.get("source") or "unknown")


def _approvals(cfg: RepoConfig, entry: dict, meta, prov: dict) -> list[dict]:
    out = []
    a = prov.get("approval")
    if a:
        out.append({"kind": "checksum-approval", "by": a.get("by"), "at": a.get("at"), "sha256": a.get("sha256"),
                    "record": a.get("record"), "scope": a.get("scope")})
    if meta.is_free_demo(entry["version"]):
        out.append({"kind": "free-demo-approval", "record": meta.access_exception,
                    "scope": "this version, Candidate only"})
    sub = prov.get("submission")
    if sub:
        rp = cfg.root / "submissions" / str(sub).split("/")[-1] / "review.json"
        if rp.is_file():
            for h in json.loads(rp.read_text(encoding="utf-8")).get("history", []):
                out.append({"kind": f"release-manager:{h.get('event')}", "at": h.get("at"),
                            **{k: v for k, v in h.items() if k in ("by", "state", "route", "status")}})
    return out


def passport(cfg: RepoConfig, entry: dict) -> dict:
    prov_path = cfg.root / (entry["filename"] + ".provenance.json")
    prov = json.loads(prov_path.read_text(encoding="utf-8")) if prov_path.is_file() else {}
    meta = load_package_meta(cfg, entry["package"])
    product = cfg.product_for_package(entry["package"])
    tests = meta.device_tests(entry["version"])
    machine = from_legacy(prov.get("validation_levels"))
    if machine is None and prov.get("inspection"):
        machine = {"static_inspection": {"state": "passed", "note": "passed HCG Repo's import validation (before v2)"},
                   "maintainer_script_review": {"state": "not-recorded"},
                   "script_free_lifecycle": {"state": "not-recorded"}}
    status = entry.get("status", "published")
    channel = entry_channel(entry)
    return {
        "format": FORMAT, "formatVersion": FORMAT_VERSION,
        "product": {"key": product.key if product else None, "name": product.name if product else None,
                    "store_slug": (product.store_slug or None) if product else None, "central_id": None},
        "edition": (product.store_edition or None) if product else None,
        "platform": PLATFORMS.get(entry["architecture"], entry["architecture"]),
        "architecture": entry["architecture"],
        "package": entry["package"], "version": entry["version"],
        "source": {"repository": prov.get("repository"), "commit": prov.get("commit"), "branch": prov.get("branch")},
        "build": {"provider": _provider(prov), "toolchain": prov.get("toolchain"),
                  "built_at": prov.get("built_at"), "run_id": prov.get("run_id"), "artifact": prov.get("artifact_name")
                  or prov.get("artifact")},
        "artifact": {"filename": Path(entry["filename"]).name, "sha256": entry["sha256"], "size": entry["size"]},
        "validation": {"levels": all_levels(machine, tests)},
        "device_evidence": [{k: t.get(k) for k in ("device", "ios", "jailbreak", "date", "stage", "result", "evidence")}
                            for t in tests],
        "compatibility_status": derive_status(entry, prov, tests),
        "approvals": _approvals(cfg, entry, meta, prov),
        "distribution": {"channel": channel, "access": meta.access_for(entry["version"])},
        "publication": {"record_status": status, "in_index_builds": status == "published",
                        "deployed": "not checked here: the Publish workflow deploys; bin/hcgrepo verify checks the live source",
                        "imported_at": entry.get("imported_at"), "withdrawn_at": entry.get("withdrawn_at"),
                        "withdrawn_reason": entry.get("withdrawn_reason")},
    }


def all_passports(cfg: RepoConfig, package: str | None = None, version: str | None = None) -> list[dict]:
    return [passport(cfg, e) for e in load_lock(cfg)["packages"]
            if (package is None or e["package"] == package) and (version is None or e["version"] == version)]

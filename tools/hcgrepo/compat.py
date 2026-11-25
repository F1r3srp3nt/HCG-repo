"""Compatibility registry: what is actually known about each released version.

Statuses (strongest evidence wins; nothing is ever inferred upward):

    Withdrawn                     the version was withdrawn (repo-lock.json)
    Known incompatible            only failing device tests are recorded
    Physical launch passed        a device test with stage "runtime" and result "pass"
    Physical installation passed  a device test with stage "install" and result "pass", or a partial runtime
    Lifecycle validation passed   the simulated dpkg lifecycle passed (scripts not executed) at import
    Static validation passed      imported through HCG Repo's validation
    Untested                      none of the above (e.g. a protected record without inspection)

Physical statuses come only from `[[versions."<v>".device_tests]]` records with evidence in package.toml.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import debian
from .config import RepoConfig, entry_channel, load_lock, load_package_meta
from .levels import NOT_RECORDED, PASSED, all_levels, from_legacy

UNTESTED = "Untested"
STATIC = "Static validation passed"
LIFECYCLE = "Lifecycle validation passed"
PHYS_INSTALL = "Physical installation passed"
PHYS_LAUNCH = "Physical launch passed"
INCOMPATIBLE = "Known incompatible"
WITHDRAWN = "Withdrawn"
STATUSES = (UNTESTED, STATIC, LIFECYCLE, PHYS_INSTALL, PHYS_LAUNCH, INCOMPATIBLE, WITHDRAWN)


def _provenance(cfg: RepoConfig, entry: dict) -> dict:
    p = cfg.root / (entry["filename"] + ".provenance.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _imported_levels(prov: dict) -> dict | None:
    """Packages imported before Distribution v2 passed the import's static inspection; nothing more is known."""
    if not prov.get("inspection"):
        return None
    return {"static_inspection": {"state": PASSED, "note": "passed HCG Repo's import validation (before v2)"},
            "maintainer_script_review": {"state": NOT_RECORDED}, "script_free_lifecycle": {"state": NOT_RECORDED}}


def derive_status(entry: dict, prov: dict, tests: list[dict]) -> str:
    """Strongest recorded evidence, never inferred upward. A recorded failure to install or to launch with no recorded
    success at that stage makes the version Known incompatible (on the recorded configurations); its evidence levels
    still show what did pass (e.g. the installation)."""
    if entry.get("status") == "withdrawn":
        return WITHDRAWN
    stage = lambda t: t.get("stage", "runtime")  # noqa: E731
    runtime = [t for t in tests if stage(t) in ("runtime", "verification")]
    installs = [t for t in tests if stage(t) == "install"]
    if any(t.get("result") == "pass" for t in runtime):
        return PHYS_LAUNCH
    if any(t.get("result") == "partial" for t in runtime):
        return PHYS_INSTALL
    if runtime and all(t.get("result") == "fail" for t in runtime):
        return INCOMPATIBLE
    if any(t.get("result") == "pass" for t in installs):
        return PHYS_INSTALL
    if installs and all(t.get("result") == "fail" for t in installs):
        return INCOMPATIBLE
    from .levels import from_legacy
    machine = from_legacy(prov.get("validation_levels")) or {}
    if (machine.get("script_free_lifecycle") or {}).get("state") == "passed":
        return LIFECYCLE
    if prov.get("inspection"):
        return STATIC
    return UNTESTED


def known_failures(tests: list[dict]) -> list[dict]:
    return [{k: t.get(k) for k in ("stage", "result", "device", "ios", "jailbreak", "date", "evidence")}
            for t in tests if t.get("result") in ("fail", "partial")]


def registry(cfg: RepoConfig, channel: str | None = None) -> dict:
    """The registry for every lock entry (or one channel's entries)."""
    out: dict[str, dict] = {}
    for e in load_lock(cfg)["packages"]:
        if channel and entry_channel(e) != channel:
            continue
        meta = load_package_meta(cfg, e["package"])
        prov = _provenance(cfg, e)
        tests = meta.device_tests(e["version"])
        insp = prov.get("inspection") or {}
        depends = insp.get("depends", "")
        try:
            fw_min, fw_max = debian.firmware_bounds(depends)
        except debian.ControlError:
            fw_min = fw_max = None
        last_pass = [t for t in tests if t.get("result") == "pass"]
        rec = {
            "version": e["version"], "architecture": e["architecture"], "channel": entry_channel(e),
            "sha256": e["sha256"], "status": derive_status(e, prov, tests),
            "layout": {"iphoneos-arm64": "rootless", "iphoneos-arm": "rootful", "iphoneos-arm64e": "roothide"}.get(
                e["architecture"], e["architecture"]),
            "minimum_ios": fw_min or insp.get("binary_minimum_ios"), "maximum_ios": fw_max,
            "depends": depends,
            "device_evidence": [{k: t.get(k) for k in ("device", "ios", "jailbreak", "date", "stage", "result", "evidence")}
                                for t in tests],
            "label": meta.label(e["version"]),
            "levels": all_levels(from_legacy(prov.get("validation_levels")) or _imported_levels(prov), tests),
            "known_failures": known_failures(tests),
            "last_physical_pass": max((t.get("date", "") for t in last_pass), default=None),
        }
        out.setdefault(e["package"], {"package": e["package"], "versions": [], "known_issues": meta.known_issues.strip()})
        out[e["package"]]["versions"].append(rec)
    import functools
    for pkg in out.values():
        pkg["versions"].sort(key=functools.cmp_to_key(lambda a, b: debian.compare_versions(a["version"], b["version"])))
        verified = [v["version"] for v in pkg["versions"] if v["status"] in (PHYS_INSTALL, PHYS_LAUNCH)]
        launched = [v["version"] for v in pkg["versions"] if v["status"] == PHYS_LAUNCH]
        pkg["last_verified_version"] = verified[-1] if verified else None    # installed (at least) on a real device
        pkg["last_launched_version"] = launched[-1] if launched else None
    return {"format": "hcg.repo.compatibility", "formatVersion": 1, "statuses": list(STATUSES),
            "packages": [out[k] for k in sorted(out)]}


def write(cfg: RepoConfig, path: Path, channel: str | None = None) -> None:
    Path(path).write_text(json.dumps(registry(cfg, channel), indent=2, sort_keys=True) + "\n", encoding="utf-8")

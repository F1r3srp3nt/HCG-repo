"""The six evidence levels HCG Repo reports, kept strictly apart.

    static_inspection         the package was read and checked on a build/admin machine (structure, metadata,
                              architecture, minimum iOS, rootless paths, dependencies, Mach-O, signing, entitlements,
                              privileged helpers, embedded secrets, checksum, index consistency)
    maintainer_script_review  the maintainer scripts were READ (static analysis); they were not run
    script_free_lifecycle     dpkg installed, reinstalled, removed and purged a COPY WITHOUT the maintainer scripts in
                              a throwaway root. It proves the payload and dpkg database handling; it does NOT prove the
                              original maintainer scripts work
    device_installation       a real device installed the package (device_tests stage "install")
    application_launch        the app or tweak started on a real device (device_tests stage "runtime")
    runtime_verification      the product's documented checklist passed on a real device (device_tests stage
                              "verification")

The device levels come only from device_tests records with evidence. Nothing here infers one level from another,
except that a launch record also shows the package was installed (reported as such, with that reason).
"""

from __future__ import annotations

PASSED, FAILED, PARTIAL, WARNINGS = "passed", "failed", "partial", "passed-with-warnings"
NOT_RUN, NOT_RECORDED, NOT_APPLICABLE = "not-run", "not-recorded", "not-applicable"

ORDER = ("static_inspection", "maintainer_script_review", "script_free_lifecycle",
         "device_installation", "application_launch", "runtime_verification")
LABELS = {
    "static_inspection": "Static package inspection",
    "maintainer_script_review": "Maintainer script review (read, not run)",
    "script_free_lifecycle": "Script-free dpkg lifecycle",
    "device_installation": "Device installation",
    "application_launch": "Application launch",
    "runtime_verification": "Full runtime verification",
}
LIFECYCLE_DISCLAIMER = ("dpkg ran on a copy with the maintainer scripts removed: this proves the payload installs, "
                        "reinstalls, removes and purges cleanly; it does not prove the original maintainer scripts work")
DEVICE_STAGE_LEVEL = {"install": "device_installation", "runtime": "application_launch",
                      "verification": "runtime_verification"}


def machine_levels(findings: list[dict], lifecycle: dict | None, scripts: list[str]) -> dict:
    """The three levels produced on a machine, from review findings (stage: manifest/static/scripts/lifecycle/...)."""
    def worst(stage_names: tuple[str, ...]) -> str:
        fs = [f for f in findings if f["stage"] in stage_names]
        if any(f["level"] == "error" for f in fs):
            return FAILED
        return WARNINGS if fs else PASSED

    if scripts:
        script_state = worst(("scripts",))
    else:
        script_state = NOT_APPLICABLE
    if lifecycle is None:
        lc = {"state": NOT_RUN}
    else:
        lc = {"state": PASSED if lifecycle.get("passed") else FAILED, "scripts_executed": False,
              "steps": lifecycle.get("steps", []), "upgrade_from": lifecycle.get("upgrade_from")}
    lc["note"] = LIFECYCLE_DISCLAIMER
    return {
        "static_inspection": {"state": worst(("manifest", "static")),
                              "note": "the package was read and checked; nothing was installed"},
        "maintainer_script_review": {"state": script_state, "scripts": sorted(scripts), "executed": False,
                                     "note": "the scripts were read (static analysis); they were not run"},
        "script_free_lifecycle": lc,
    }


def device_levels(tests: list[dict]) -> dict:
    """The three device levels from package.toml device_tests records (never inferred)."""
    out: dict[str, dict] = {k: {"state": NOT_RECORDED, "evidence": []} for k in DEVICE_STAGE_LEVEL.values()}
    rank = {PASSED: 3, PARTIAL: 2, FAILED: 1}
    result_state = {"pass": PASSED, "partial": PARTIAL, "fail": FAILED}
    for t in tests:
        level = DEVICE_STAGE_LEVEL.get(t.get("stage", "runtime"))
        if not level:
            continue
        state = result_state.get(t.get("result"), NOT_RECORDED)
        cur = out[level]
        cur["evidence"].append(t.get("evidence"))
        if rank.get(state, 0) > rank.get(cur["state"], 0):
            cur["state"] = state
    inst, launch = out["device_installation"], out["application_launch"]
    if inst["state"] == NOT_RECORDED and launch["state"] in (PASSED, PARTIAL):
        inst["state"] = PASSED
        inst["note"] = "implied: a launch was recorded on the device, so the package was installed"
    return out


def all_levels(machine: dict | None, tests: list[dict]) -> dict:
    m = machine or {k: {"state": NOT_RECORDED} for k in ORDER[:3]}
    out = {k: m.get(k, {"state": NOT_RECORDED}) for k in ORDER[:3]}
    out.update(device_levels(tests))
    return out


def from_legacy(v: dict | None) -> dict | None:
    """Read validation_levels written before the six-level vocabulary (static / simulated_lifecycle)."""
    if not v:
        return None
    if "static_inspection" in v:
        return v
    sl = v.get("simulated_lifecycle") or {}
    return {"static_inspection": {"state": PASSED if (v.get("static") or {}).get("passed") else NOT_RECORDED},
            "maintainer_script_review": {"state": NOT_RECORDED},
            "script_free_lifecycle": {"state": PASSED if sl.get("passed") else FAILED if sl.get("passed") is False
                                      else NOT_RUN, "scripts_executed": False, "note": LIFECYCLE_DISCLAIMER}}


def render(levels: dict) -> list[str]:
    lines = []
    for k in ORDER:
        lv = levels.get(k, {"state": NOT_RECORDED})
        extra = ""
        if k == "script_free_lifecycle" and lv.get("steps"):
            extra = " (" + ", ".join(f"{s['step']} {'ok' if s['ok'] else 'FAILED'}" for s in lv["steps"]) + ")"
        lines.append(f"{LABELS[k]:<42} {lv['state'].upper()}{extra}")
    lines.append("  note: " + LIFECYCLE_DISCLAIMER)
    return lines

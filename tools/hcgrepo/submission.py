"""Artifact submission manifest (format `hcg.repo.artifact-submission`, version 1).

A submission describes one built `.deb` handed to HCG Repo by any build provider: GitHub Actions
(public or private repositories), a local HCG Executable Maker build, the HCG Server Build Host or
an approved developer upload. Everything in a manifest is a *claim*: `verify_against_deb` compares
each claim with what HCG Repo itself reads from the package, and only those independent findings
decide whether the package can go further. Nothing here downloads, installs or publishes anything.

Provenance rules:
- a GitHub Actions run ID is required for the GitHub providers and refused for every other
  provider, so a locally built package can never carry an invented CI run;
- the source commit is a full 40-character SHA and the source tree must not be dirty;
- the only channel a submission can ask for is `candidate` (Stable is a separate owner decision).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import debian
from .config import Report

FORMAT = "hcg.repo.artifact-submission"
FORMAT_VERSION = 1

PROVIDERS = {
    "github-actions": "GitHub Actions artifact (public repository)",
    "github-actions-private": "GitHub Actions artifact (private repository, read with an authorized token)",
    "executable-maker-local": "HCG Executable Maker, built locally on the developer's machine",
    "hcg-server-build-host": "HCG Server Build Host (authorized)",
    "developer-upload": "approved developer upload",
}
GITHUB_PROVIDERS = {"github-actions", "github-actions-private"}
LAYOUTS = {"rootless": "iphoneos-arm64", "rootful": "iphoneos-arm", "roothide": "iphoneos-arm64e"}
SCRIPT_NAMES = ("preinst", "postinst", "prerm", "postrm", "config")
SIGNING_METHODS = {"ldid", "apple-development", "apple-distribution", "unsigned"}
NATIVE_FOLDER_SUFFIX = "-hcg-repo-submission"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]+$")
OS_RE = re.compile(r"^\d+(\.\d+){0,2}$")


class SubmissionError(Exception):
    """The manifest is malformed or breaks a provenance rule."""


def _get(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _iso(ts) -> bool:
    if not isinstance(ts, str) or not ts:
        return False
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate_manifest(m: dict) -> list[str]:
    """Schema and provenance-rule problems of manifest `m` (empty when it is well formed)."""
    p: list[str] = []
    if not isinstance(m, dict):
        return ["manifest must be a JSON object"]
    if m.get("format") != FORMAT:
        p.append(f"format must be {FORMAT!r}")
    if m.get("formatVersion") != FORMAT_VERSION:
        p.append(f"formatVersion must be {FORMAT_VERSION}")

    pkg = m.get("package") or {}
    if not PKG_RE.match(str(pkg.get("identifier", ""))):
        p.append("package.identifier must be a Debian package name")
    try:
        debian.parse_version(str(pkg.get("version") or ""))
    except debian.ControlError:
        p.append("package.version must be a valid Debian version")
    arch = pkg.get("architecture")
    if not arch:
        p.append("package.architecture is required")

    art = m.get("artifact") or {}
    if not SHA256_RE.match(str(art.get("sha256", ""))):
        p.append("artifact.sha256 must be 64 lowercase hex characters")
    if not isinstance(art.get("size"), int) or isinstance(art.get("size"), bool) or art.get("size", 0) <= 0:
        p.append("artifact.size must be a positive integer (bytes)")
    fn = str(art.get("filename", ""))
    if not fn.endswith(".deb") or "/" in fn or "\\" in fn or fn.startswith("."):
        p.append("artifact.filename must be a plain *.deb file name")

    comp = m.get("compatibility") or {}
    if comp.get("minimumOS") is not None and not OS_RE.match(str(comp.get("minimumOS"))):
        p.append("compatibility.minimumOS must look like 15.0")
    if comp.get("minimumOS") is None:
        p.append("compatibility.minimumOS is required")
    env = comp.get("environment")
    if env not in LAYOUTS:
        p.append(f"compatibility.environment must be one of {sorted(LAYOUTS)}")
    elif arch and LAYOUTS[env] != arch:
        p.append(f"compatibility.environment {env} needs architecture {LAYOUTS[env]}, not {arch}")
    jbs = comp.get("jailbreaks", [])
    if not isinstance(jbs, list) or not all(isinstance(x, str) and x for x in jbs):
        p.append("compatibility.jailbreaks must be a list of names")

    src = m.get("source") or {}
    if not REPO_RE.match(str(src.get("repository", ""))):
        p.append("source.repository must be OWNER/NAME")
    if not SHA40_RE.match(str(src.get("commit", ""))):
        p.append("source.commit must be the full 40-character commit SHA the package was built from")
    if src.get("dirty") is not False:
        p.append("source.dirty must be false: a package built from uncommitted changes has no reproducible source")

    b = m.get("build") or {}
    prov = b.get("provider")
    if prov not in PROVIDERS:
        p.append(f"build.provider must be one of {sorted(PROVIDERS)}")
    if not b.get("toolchain"):
        p.append("build.toolchain is required (for example: Xcode 16.2, ldid 2.1.5)")
    if not _iso(b.get("builtAt")):
        p.append("build.builtAt must be an ISO 8601 timestamp")
    run_id = b.get("runId")
    if prov in GITHUB_PROVIDERS:
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
            p.append("build.runId (the GitHub Actions run) is required for GitHub providers")
        if not b.get("artifactName"):
            p.append("build.artifactName is required for GitHub providers")
        if not REPO_RE.match(str(b.get("runRepository", ""))):
            p.append("build.runRepository (OWNER/NAME of the repository that ran the workflow) is required for GitHub providers")
    elif prov is not None:
        for k in ("runId", "runUrl", "artifactName", "runRepository"):
            if b.get(k) not in (None, ""):
                p.append(f"build.{k} is only for GitHub Actions builds; a {prov} build has no CI run")

    deps = m.get("dependencies")
    if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps):
        p.append("dependencies must be a list of package names (empty when there are none)")
    ms = m.get("maintainerScripts")
    if not isinstance(ms, dict) or "declared" not in ms:
        p.append("maintainerScripts.declared is required (a list of script names, or null when the provider does not record them)")
    elif ms["declared"] is not None and (not isinstance(ms["declared"], list) or
                                         any(s not in SCRIPT_NAMES for s in ms["declared"])):
        p.append(f"maintainerScripts.declared must list only {', '.join(SCRIPT_NAMES)}")

    sig = m.get("signing")
    if sig is not None:
        if not isinstance(sig, dict) or sig.get("method") not in SIGNING_METHODS:
            p.append(f"signing.method must be one of {sorted(SIGNING_METHODS)}")
        else:
            if not isinstance(sig.get("signedFiles", []), list):
                p.append("signing.signedFiles must be a list of paths inside the package")
            ents = sig.get("entitlements", {})
            if not isinstance(ents, dict) or not all(isinstance(v, dict) for v in ents.values()):
                p.append("signing.entitlements must map a path inside the package to its entitlements dictionary")

    val = m.get("validation")
    if not isinstance(val, dict) or not isinstance(val.get("checks", []), list):
        p.append("validation must be an object with a checks list (the provider's own report, kept as a claim)")

    if m.get("channel") != "candidate":
        p.append("channel must be 'candidate': submissions never go straight to Stable")
    return p


def load_manifest(path: Path) -> dict:
    try:
        m = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SubmissionError(f"{path}: unreadable manifest: {exc}") from exc
    problems = validate_manifest(m)
    if problems:
        raise SubmissionError(f"{Path(path).name}: " + "; ".join(problems))
    return m


def submission_id(m: dict) -> str:
    pkg, art = m["package"], m["artifact"]
    raw = f"{pkg['identifier']}_{pkg['version']}_{pkg['architecture']}_{art['sha256'][:12]}"
    return re.sub(r"[^A-Za-z0-9._-]", "-", raw)


# ---------------------------------------------------------------------------- Unleashify handoff


def _control_txt(folder: Path) -> list[tuple[str, str]]:
    f = folder / "control.txt"
    if not f.is_file():
        return []
    try:
        return debian.parse_control(f.read_text(encoding="utf-8"))
    except debian.ControlError:
        return []


def _dep_names(depends: str | None) -> list[str]:
    names: list[str] = []
    try:
        groups = debian.parse_relations(depends or "")
    except debian.ControlError:
        return ["<unparseable Depends>"]
    for group in groups:
        for alt in group:
            if alt.name not in names:
                names.append(alt.name)
    return names


def from_unleashify_handoff(folder: Path) -> tuple[dict, Path]:
    """Convert an HCG Executable Maker Unleashify handoff folder into a v1 manifest.

    Returns (manifest, deb_path). Only facts the handoff records are copied; anything it does not
    record (maintainer scripts, named jailbreaks) stays undeclared rather than guessed. A handoff
    without a CI run is a local build (`executable-maker-local`) and gets no run ID.
    """
    folder = Path(folder)
    try:
        prov = json.loads((folder / "provenance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SubmissionError(f"{folder}: no readable provenance.json: {exc}") from exc
    deb = folder / str(prov.get("file", ""))
    if not prov.get("file") or not deb.is_file():
        raise SubmissionError(f"{folder}: the .deb named in provenance.json ({prov.get('file')!r}) is not in the folder")
    sums = folder / "SHA256SUMS"
    if sums.is_file():
        listed = {ln.split()[-1].lstrip("*"): ln.split()[0] for ln in sums.read_text().splitlines() if ln.strip()}
        if listed.get(deb.name, prov.get("sha256")) != prov.get("sha256"):
            raise SubmissionError(f"{folder}: SHA256SUMS and provenance.json disagree about {deb.name}")

    src = prov.get("source") or {}
    pkging = prov.get("packaging") or {}
    app = prov.get("app") or {}
    run_id = src.get("ci_run_id")
    github = isinstance(run_id, int) and not isinstance(run_id, bool) and run_id > 0
    control = _control_txt(folder)
    built_with = pkging.get("built_with") or ""
    tool = pkging.get("tool") or ""
    m = {
        "format": FORMAT, "formatVersion": FORMAT_VERSION,
        "package": {"identifier": prov.get("package"), "version": prov.get("version"),
                    "architecture": prov.get("architecture"), "name": app.get("name")},
        "artifact": {"filename": deb.name, "sha256": str(prov.get("sha256", "")).lower(), "size": prov.get("size")},
        "compatibility": {"minimumOS": app.get("minimum_os"),
                          "environment": next((k for k, v in LAYOUTS.items() if v == prov.get("architecture")), None),
                          "jailbreaks": [],
                          "deviceFamilies": app.get("device_families") or []},
        "source": {"repository": src.get("repository"), "commit": str(src.get("commit") or "").lower(),
                   "dirty": src.get("dirty"),
                   "branch": _get(prov, "unleashify.source_revision.branch")},
        "build": {"provider": "github-actions" if github else "executable-maker-local",
                  "toolchain": ", ".join(x for x in (built_with, tool) if x),
                  "builtAt": pkging.get("packaged_at")},
        "dependencies": _dep_names(debian.field(control, "Depends")) if control else [],
        "maintainerScripts": {"declared": None, "note": "not recorded by the Unleashify handoff; HCG Repo reads them from the package"},
        "validation": {"tool": tool or "HCG Executable Maker", "checks": prov.get("validation") or [],
                       "deviceTested": bool(prov.get("device_tested"))},
        "channel": "candidate",
        "origin": {"kind": "executable-maker-unleashify-handoff",
                   "unleashify": prov.get("unleashify"),
                   "installPath": pkging.get("install_path"), "ldidSigned": pkging.get("ldid_signed"),
                   "entitlementsFiles": pkging.get("entitlements_files")},
    }
    if github:
        m["build"].update({"runId": run_id, "runUrl": src.get("ci_run_url"), "artifactName": src.get("ci_artifact"),
                           "runRepository": src.get("ci_repository"), "runCommit": src.get("ci_commit")})
    if not control:
        m["dependencies"] = []
        m["origin"]["dependenciesNote"] = "control.txt missing from the handoff; dependencies taken as none and checked against the package"
    problems = validate_manifest(m)
    if problems:
        raise SubmissionError(f"{folder.name}: the handoff cannot become a submission: " + "; ".join(problems))
    return m, deb


# ---------------------------------------------------------------------------- independent checks


def verify_against_deb(m: dict, info) -> Report:
    """Compare every claim of manifest `m` with the inspected package `info` (a debinspect.DebInfo).

    Errors here mean the manifest misdescribes the package; the package is then refused whatever
    the provider's own validation report says.
    """
    r = Report("submission")
    pkg, art, comp = m["package"], m["artifact"], m["compatibility"]
    for claim, actual, label in ((pkg["identifier"], info.package, "package identifier"),
                                 (pkg["version"], info.version, "version"),
                                 (pkg["architecture"], info.architecture, "architecture"),
                                 (art["sha256"], info.sha256, "SHA-256"),
                                 (art["size"], info.size, "size")):
        if claim != actual:
            r.error("submission-mismatch", f"{label}: the manifest says {claim!r}, the package is {actual!r}")

    claimed_os = str(comp["minimumOS"])
    app_os = sorted({a.min_os for a in info.apps if a.min_os})
    for v in app_os:
        if debian.compare_versions(v, claimed_os) != 0:
            r.error("submission-min-os", f"the app's MinimumOSVersion is {v}, the manifest claims {claimed_os}")
    bin_os = info.binary_min_os
    if bin_os and debian.compare_versions(bin_os, claimed_os) > 0:
        r.error("submission-min-os", f"packaged code needs iOS {bin_os}, above the claimed minimum {claimed_os}")
    try:
        fw = debian.firmware_bounds(info.get("Depends"))[0]
    except debian.ControlError:
        fw = None
    if fw and debian.compare_versions(fw, claimed_os) != 0:
        r.warn("submission-min-os-depends", f"Depends declares firmware (>= {fw}), the manifest claims {claimed_os}")

    actual_deps = sorted(_dep_names(info.get("Depends")))
    if sorted(m["dependencies"]) != actual_deps:
        r.error("submission-dependencies", f"the manifest lists dependencies {sorted(m['dependencies'])}, "
                                           f"the package depends on {actual_deps}")

    sig = m.get("signing")
    if sig:
        bins = {b.path: b for b in info.binaries}
        for path in sig.get("signedFiles", []):
            b = bins.get(str(path).lstrip("/"))
            if b is None or not b.info:
                r.error("submission-signing", f"signing.signedFiles lists {path}, which is not a Mach-O in the package")
            elif not all(sl.signed for sl in b.info.slices) and sig["method"] != "unsigned":
                r.error("submission-signing", f"{path} is not signed, the manifest says {sig['method']}")
        for path, claimed in (sig.get("entitlements") or {}).items():
            b = bins.get(str(path).lstrip("/"))
            actual = {}
            if b and b.info:
                for sl in b.info.slices:
                    actual.update(sl.entitlements or {})
            if b is None:
                r.error("submission-entitlements", f"signing.entitlements names {path}, which is not a Mach-O in the package")
            elif actual != claimed:
                r.error("submission-entitlements", f"{path}: the manifest declares entitlements {sorted(claimed)}, "
                                                   f"the binary carries {sorted(actual)}")

    declared = m["maintainerScripts"]["declared"]
    actual_scripts = sorted(info.scripts)
    if declared is None:
        r.warn("submission-scripts-undeclared", f"the provider did not declare maintainer scripts; the package has {actual_scripts or 'none'}")
    elif sorted(declared) != actual_scripts:
        r.error("submission-scripts", f"the manifest declares scripts {sorted(declared)}, the package has {actual_scripts}")
    return r



def provenance(m: dict, sid: str, approval: dict | None, levels: dict | None = None) -> dict:
    """Provenance recorded in pool/<file>.provenance.json for a submitted artifact. One builder for every route
    (local-submission, local-file, https-artifact, Release Manager), so the records are identical in shape."""
    b, src = m["build"], m["source"]
    prov = {"source": "artifact-submission", "provider": b["provider"], "provider_label": PROVIDERS[b["provider"]],
            "repository": src["repository"], "commit": src["commit"], "branch": src.get("branch"),
            "built_at": b["builtAt"], "toolchain": b["toolchain"], "submission": sid, "approval": approval}
    if b["provider"] in GITHUB_PROVIDERS:
        prov.update({"run_id": b["runId"], "run_url": b.get("runUrl"), "artifact": b.get("artifactName"),
                     "run_repository": b.get("runRepository")})
    signing = (m.get("signing") or {})
    if signing:
        prov["signing_claimed"] = signing
    if levels is not None:
        prov["validation_levels"] = levels
    return {k: v for k, v in prov.items() if v is not None}


def from_submission_folder(folder: Path) -> tuple[dict, Path]:
    """A native HCG Repo submission folder (docs/EXECUTABLE_MAKER_SUBMISSION.md): submission.json, the .deb it
    names and SHA256SUMS. Returns (manifest, deb_path)."""
    folder = Path(folder)
    m = load_manifest(folder / "submission.json")
    deb = folder / m["artifact"]["filename"]
    if not deb.is_file():
        raise SubmissionError(f"{folder.name}: {m['artifact']['filename']} (named in submission.json) is not in the folder")
    sums = folder / "SHA256SUMS"
    if not sums.is_file():
        raise SubmissionError(f"{folder.name}: SHA256SUMS is required")
    listed = {ln.split()[-1].lstrip("*"): ln.split()[0] for ln in sums.read_text().splitlines() if ln.strip()}
    if listed.get(deb.name) != m["artifact"]["sha256"]:
        raise SubmissionError(f"{folder.name}: SHA256SUMS and submission.json disagree about {deb.name}")
    return m, deb


def load_folder(folder: Path) -> tuple[dict, Path]:
    """A native submission folder (submission.json) or, for older Executable Maker versions, an Unleashify
    candidate handoff (provenance.json)."""
    folder = Path(folder)
    if (folder / "submission.json").is_file():
        return from_submission_folder(folder)
    return from_unleashify_handoff(folder)

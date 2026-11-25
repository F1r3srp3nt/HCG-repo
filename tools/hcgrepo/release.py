"""Candidate Release Manager: incoming artifacts → review → exact-checksum approval → Candidate import.

The flow keeps the existing separation of *Prepare packages* and *Publish repository*:

    submit   copy the .deb into the PRIVATE incoming store (outside Git), validate it, write a review
    approve  the owner approves one exact SHA-256 of a validated submission (recorded verbatim)
    prepare  import the approved bytes into pool/ for the Candidate channel, with provenance,
             an import-request record and the review, ready to commit for review
    publish  NOT here: the manual "Publish repository" workflow, after the owner's explicit approval

Nothing is ever published, promoted to Stable or approved automatically. A submission that is not
approved never enters the repository: its bytes and even its metadata stay in the private store.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tomllib
from pathlib import Path

from . import debian
from .config import Report, RepoConfig, load_lock, load_package_meta
from .debinspect import InspectError, hash_file, inspect_deb, validate_deb
from .deepcheck import deep_validate
from .importer import _now, import_deb, set_channel
from .submission import (GITHUB_PROVIDERS, PROVIDERS, from_unleashify_handoff, load_manifest,
                         submission_id, verify_against_deb)

INCOMING_ENV = "HCG_REPO_INCOMING"
REVIEW_FORMAT = "hcg.repo.submission-review"
HANDOFF_EXTRAS = ("package.toml", "CHANGELOG.md", "KNOWN-ISSUES.md", "icon.png", "DEVICE-TEST.md", "VALIDATION.txt")
SID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class ReleaseError(Exception):
    pass


# ---------------------------------------------------------------------------- private store


def incoming_root(override: str | Path | None = None) -> Path:
    """The private incoming store. Never inside a Git checkout that is published."""
    root = Path(override or os.environ.get(INCOMING_ENV) or Path.home() / ".local/share/hcg-repo/incoming").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def _check_store_location(cfg: RepoConfig, store: Path) -> None:
    try:
        store.resolve().relative_to(cfg.root.resolve())
    except ValueError:
        return
    raise ReleaseError(f"the incoming store {store} is inside the repository checkout; it must be private (set {INCOMING_ENV})")


def _sub_dir(store: Path, sid: str) -> Path:
    if not SID_RE.match(sid):
        raise ReleaseError(f"invalid submission id {sid!r}")
    d = store / sid
    if not (d / "review.json").is_file():
        raise ReleaseError(f"no submission {sid!r} in the incoming store {store}")
    return d


def _write_json(path: Path, data: dict, private: bool = True) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if private:
        os.chmod(path, 0o600)


def load_review(store: Path, sid: str) -> tuple[dict, dict, Path]:
    d = _sub_dir(store, sid)
    return (json.loads((d / "submission.json").read_text(encoding="utf-8")),
            json.loads((d / "review.json").read_text(encoding="utf-8")), d)


def _event(review: dict, event: str, **extra) -> None:
    review.setdefault("history", []).append({"at": _now(), "event": event, **extra})


# ---------------------------------------------------------------------------- review


def _previous_in_pool(cfg: RepoConfig, package: str, arch: str, version: str) -> Path | None:
    older = [e for e in load_lock(cfg)["packages"]
             if e["package"] == package and e["architecture"] == arch and e.get("status") not in ("withdrawn", "protected")
             and debian.compare_versions(e["version"], version) < 0 and (cfg.root / e["filename"]).is_file()]
    if not older:
        return None
    import functools
    best = max(older, key=functools.cmp_to_key(lambda a, b: debian.compare_versions(a["version"], b["version"])))
    return cfg.root / best["filename"]


def review_artifact(cfg: RepoConfig, m: dict, deb: Path, lifecycle: bool = True) -> dict:
    """Independently validate one submitted .deb. Never trusts the manifest's own validation report."""
    findings: list[dict] = []

    def take(rep: Report, stage: str) -> None:
        for level, code, msg in rep.items:
            findings.append({"stage": stage, "level": level, "code": code, "message": msg})

    review: dict = {"format": REVIEW_FORMAT, "formatVersion": 1, "submission": submission_id(m),
                    "received_at": _now(), "provider": m["build"]["provider"],
                    "provider_label": PROVIDERS[m["build"]["provider"]],
                    "claimed": {"package": m["package"], "artifact": m["artifact"], "source": m["source"],
                                "build": m["build"], "compatibility": m["compatibility"],
                                "provider_validation": m.get("validation")},
                    "approval": None, "history": []}
    try:
        info = inspect_deb(deb)
    except InspectError as exc:
        findings.append({"stage": "structure", "level": "error", "code": "deb-unreadable", "message": str(exc)})
        review.update(state="refused", findings=findings, levels={})
        return review
    review["package"] = {"identifier": info.package, "version": info.version, "architecture": info.architecture}
    review["sha256"], review["size"], review["sha512"] = info.sha256, info.size, info.sha512
    review["inspection"] = info.summary()

    take(verify_against_deb(m, info), "manifest")
    meta = load_package_meta(cfg, info.package)
    take(validate_deb(info, cfg, meta), "static")
    upgrade_from = _previous_in_pool(cfg, info.package, info.architecture, info.version)
    deep, evidence = deep_validate(info, lifecycle=lifecycle, upgrade_from=upgrade_from)
    for level, code, msg in deep.items:
        stage = "lifecycle" if code.startswith("lifecycle-") else "static"
        findings.append({"stage": stage, "level": level, "code": code, "message": msg})
    if evidence.get("lifecycle"):
        evidence["lifecycle"]["upgrade_from"] = upgrade_from.name if upgrade_from else None

    # Repository consistency: immutability, duplicates, access policy, index validation (dry run).
    repo = Report("repository")
    same = [e for e in load_lock(cfg)["packages"] if (e["package"], e["version"], e["architecture"]) == info.key]
    if same and same[0]["sha256"] != info.sha256:
        repo.error("immutable-version", f"{info.package} {info.version} is already released with sha256 "
                                        f"{same[0]['sha256']}; different bytes need a new version")
    elif same:
        repo.warn("already-in-repository", f"identical bytes are already recorded ({same[0]['filename']})")
    if meta.directory is None:
        repo.warn("package-metadata-missing", f"packages/{info.package}/package.toml does not exist yet; "
                                              "prepare needs it (--create-package-metadata uses the handoff's draft)")
    else:
        if meta.access_for(info.version) != "public":
            repo.warn("needs-free-demo-approval", f"{info.package} is '{meta.access}' and {info.version} has no approved "
                                                  "exception; Candidate serves only free public builds")
        elif not (same and same[0]["sha256"] != info.sha256):
            status, rep = import_deb(cfg, deb, {"source": "artifact-submission"}, dry_run=True,
                                     expect={"sha256": info.sha256}, channel="candidate")
            for level, code, msg in rep.items:
                if code != "already-present":
                    (repo.error if level == "error" else repo.warn)(code, msg)
    take(repo, "repository")

    errors = [f for f in findings if f["level"] == "error"]
    lc = evidence.get("lifecycle")
    review["levels"] = {
        "static": {"passed": not any(f["stage"] in ("static", "manifest") for f in errors),
                   "detail": "package read and checked on this machine"},
        "simulated_lifecycle": ({"passed": bool(lc["passed"]), "scripts_executed": False, "steps": lc["steps"],
                                 "upgrade_from": lc.get("upgrade_from"), "note": lc["note"]}
                                if lc else {"passed": None, "note": "not run"}),
        "physical_device": {"recorded": False, "note": "no device evidence; HCG Repo never infers a device result"},
    }
    review["evidence"] = evidence["static"]
    review["findings"] = findings
    review["state"] = "refused" if errors else "validated"
    _event(review, "reviewed", state=review["state"])
    return review


# ---------------------------------------------------------------------------- commands


def submit(cfg: RepoConfig, store: Path, handoff: Path | None = None, deb: Path | None = None,
           manifest: Path | None = None, lifecycle: bool = True) -> dict:
    _check_store_location(cfg, store)
    if handoff:
        m, deb_path = from_unleashify_handoff(handoff)
    elif deb and manifest:
        m, deb_path = load_manifest(manifest), Path(deb)
    else:
        raise ReleaseError("give --handoff DIR, or --deb FILE with --manifest FILE")
    if not deb_path.is_file():
        raise ReleaseError(f"{deb_path}: no such file")
    actual = hash_file(deb_path)["sha256"]
    if actual != m["artifact"]["sha256"]:
        raise ReleaseError(f"{deb_path.name}: sha256 is {actual}, the manifest says {m['artifact']['sha256']}")
    sid = submission_id(m)
    d = store / sid
    if (d / "review.json").is_file():
        old = json.loads((d / "review.json").read_text(encoding="utf-8"))
        if old.get("state") in ("approved", "prepared"):
            raise ReleaseError(f"{sid} is already {old['state']}; an approved submission is never re-reviewed in place")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = d / "artifact.deb.partial"
    shutil.copyfile(deb_path, tmp)
    if hash_file(tmp)["sha256"] != actual:
        tmp.unlink()
        raise ReleaseError("the file changed while it was copied")
    os.replace(tmp, d / "artifact.deb")
    os.chmod(d / "artifact.deb", stat.S_IRUSR | stat.S_IWUSR)
    if handoff:
        (d / "handoff").mkdir(exist_ok=True)
        for name in HANDOFF_EXTRAS:
            if (Path(handoff) / name).is_file():
                shutil.copyfile(Path(handoff) / name, d / "handoff" / name)
    review = review_artifact(cfg, m, d / "artifact.deb", lifecycle=lifecycle)
    _event(review, "submitted", via="unleashify-handoff" if handoff else "manifest")
    _write_json(d / "submission.json", m)
    _write_json(d / "review.json", review)
    return review


def approve(store: Path, sid: str, sha256: str, by: str, record: str) -> dict:
    m, review, d = load_review(store, sid)
    if review.get("state") != "validated":
        raise ReleaseError(f"{sid} is {review.get('state')!r}; only a validated submission can be approved")
    sha256 = sha256.strip().lower()
    if sha256 != review["sha256"]:
        raise ReleaseError(f"the approved SHA-256 {sha256} is not this submission's {review['sha256']}")
    if hash_file(d / "artifact.deb")["sha256"] != sha256:
        raise ReleaseError("the stored artifact no longer matches its reviewed checksum")
    if not by.strip() or not record.strip():
        raise ReleaseError("an approval needs who approved (--by) and the approval itself, verbatim (--record)")
    review["approval"] = {"sha256": sha256, "by": by.strip(), "record": record.strip(), "at": _now(),
                          "scope": "Candidate import of these exact bytes; publication is a separate approval"}
    review["state"] = "approved"
    _event(review, "approved", sha256=sha256, by=by.strip())
    _write_json(d / "review.json", review)
    return review


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def add_free_demo_exception(path: Path, version: str, approval: str) -> None:
    """Record the owner's free-demo approval for exactly `version` in package.toml [access]."""
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    acc = data.get("access", {})
    versions = [str(v) for v in acc.get("exception_versions", [])]
    if version in versions:
        return
    if "exception" in acc:
        new_text = re.sub(r"(?m)^exception\s*=\s*\"(?:[^\"\\]|\\.)*\"\s*$",
                          lambda _m: "exception = " + _toml_str(f"{acc['exception']} Separately, {approval}"), text, count=1)
        new_text = re.sub(r"(?m)^exception_versions\s*=\s*\[[^\]\n]*\]\s*$",
                          lambda _m: "exception_versions = " + json.dumps(versions + [version]), new_text, count=1)
    elif re.search(r"(?m)^\[access\]\s*$", text):
        new_text = re.sub(r"(?m)^\[access\]\s*$", lambda _m: "[access]\nexception = " + _toml_str(approval)
                          + "\nexception_versions = " + json.dumps([version]), text, count=1)
    else:
        new_text = text.rstrip("\n") + "\n\n[access]\nexception = " + _toml_str(approval) + \
                   "\nexception_versions = " + json.dumps([version]) + "\n"
    after = tomllib.loads(new_text).get("access", {})
    if version not in [str(v) for v in after.get("exception_versions", [])] or approval not in after.get("exception", ""):
        raise ReleaseError(f"{path}: could not record the exception safely; edit [access] by hand")
    path.write_text(new_text, encoding="utf-8")


def prepare(cfg: RepoConfig, store: Path, sid: str, free_demo_approval: str | None = None,
            create_package_metadata: bool = False) -> tuple[str, Report, dict]:
    """Import an approved submission into pool/ for the Candidate channel. Does not publish."""
    m, review, d = load_review(store, sid)
    if review.get("state") != "approved" or not review.get("approval"):
        raise ReleaseError(f"{sid} is {review.get('state')!r}; the owner must approve its exact SHA-256 first")
    art = d / "artifact.deb"
    if hash_file(art)["sha256"] != review["approval"]["sha256"]:
        raise ReleaseError("the stored artifact does not match the approved SHA-256; nothing was imported")
    pkg, ver, arch = review["package"]["identifier"], review["package"]["version"], review["package"]["architecture"]

    meta_dir = cfg.packages_meta_dir / pkg
    if not (meta_dir / "package.toml").is_file():
        draft = d / "handoff" / "package.toml"
        if not create_package_metadata:
            raise ReleaseError(f"packages/{pkg}/package.toml does not exist. Review the existing packages first; to create "
                               "it from the submission's draft, pass --create-package-metadata")
        if not draft.is_file():
            raise ReleaseError(f"packages/{pkg}/package.toml does not exist and the submission has no draft to create it from")
        meta_dir.mkdir(parents=True)
        for name in ("package.toml", "CHANGELOG.md", "icon.png"):
            if (d / "handoff" / name).is_file():
                shutil.copyfile(d / "handoff" / name, meta_dir / name)
    meta = load_package_meta(cfg, pkg)
    if meta.access_for(ver) != "public":
        if not free_demo_approval:
            raise ReleaseError(f"{pkg} is '{meta.access}'. Candidate serves only free public builds: {ver} needs the "
                               "owner's free-demo approval for this version (--free-demo-approval \"<owner's words>\")")
        add_free_demo_exception(meta_dir / "package.toml", ver, free_demo_approval)

    b = m["build"]
    prov = {"source": "artifact-submission", "provider": b["provider"], "repository": m["source"]["repository"],
            "commit": m["source"]["commit"], "branch": m["source"].get("branch"), "built_at": b["builtAt"],
            "toolchain": b["toolchain"], "submission": sid, "approval": review["approval"],
            "validation_levels": review["levels"]}
    if b["provider"] in GITHUB_PROVIDERS:
        prov.update({"run_id": b["runId"], "run_url": b.get("runUrl"), "artifact": b.get("artifactName"),
                     "run_repository": b.get("runRepository")})
    status, rep = import_deb(cfg, art, prov, expect={"package": pkg, "version": ver, "architecture": arch,
                                                     "sha256": review["approval"]["sha256"]}, channel="candidate")
    if status == "imported":
        set_channel(cfg, pkg, ver, arch, "candidate")
    if status in ("imported", "already-present"):
        review["state"] = "prepared"
        _event(review, "prepared", channel="candidate", status=status)
        _write_json(d / "review.json", review)
        out = cfg.root / "submissions" / sid
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "submission.json", m, private=False)
        public_review = {k: v for k, v in review.items() if k not in ("inspection",)}
        _write_json(out / "review.json", public_review, private=False)
        write_import_record(cfg, m, review)
    return status, rep, review


def write_import_record(cfg: RepoConfig, m: dict, review: dict) -> Path:
    sid = review["submission"]
    p = cfg.root / "imports" / f"{sid}.toml"
    p.parent.mkdir(exist_ok=True)
    a = review["approval"]
    lines = [
        f"# {m['package'].get('name') or review['package']['identifier']} {review['package']['version']}: "
        f"{PROVIDERS[m['build']['provider']]}.",
        f"# Source {m['source']['repository']} commit {m['source']['commit']}; built {m['build']['builtAt']} with "
        f"{m['build']['toolchain']}.",
        f"# Owner approval of this exact SHA-256 for Candidate: {a['by']}, {a['at']}. Publication is a separate approval.",
        "",
        "[request]",
        f"package = {_toml_str(review['package']['identifier'])}",
        f"version = {_toml_str(review['package']['version'])}",
        f"architecture = {_toml_str(review['package']['architecture'])}",
        'channel = "candidate"',
        "",
        "[source]",
        'kind = "local-submission"',
        f"provider = {_toml_str(m['build']['provider'])}",
        f"repository = {_toml_str(m['source']['repository'])}",
        f"commit = {_toml_str(m['source']['commit'])}",
        f"submission = {_toml_str(f'submissions/{sid}/submission.json')}",
        f"sha256 = {_toml_str(review['sha256'])}",
        "",
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def withdraw_submission(store: Path, sid: str, reason: str) -> dict:
    _, review, d = load_review(store, sid)
    if review.get("state") == "prepared":
        raise ReleaseError(f"{sid} is already in the pool; withdraw the version with `hcgrepo withdraw`")
    review["state"] = "withdrawn"
    _event(review, "withdrawn", reason=reason)
    _write_json(d / "review.json", review)
    return review


def list_incoming(cfg: RepoConfig, store: Path) -> dict:
    subs = []
    for d in sorted(p for p in store.iterdir() if (p / "review.json").is_file()):
        r = json.loads((d / "review.json").read_text(encoding="utf-8"))
        subs.append({"id": d.name, "state": r.get("state"), "provider": r.get("provider"),
                     "package": (r.get("package") or {}).get("identifier"),
                     "version": (r.get("package") or {}).get("version"), "sha256": r.get("sha256"),
                     "errors": sum(1 for f in r.get("findings", []) if f["level"] == "error"),
                     "warnings": sum(1 for f in r.get("findings", []) if f["level"] == "warning")})
    return {"submissions": subs, "superseded_candidates": superseded_candidates(cfg)}


def superseded_candidates(cfg: RepoConfig) -> list[dict]:
    """Candidate versions with a newer Candidate version of the same package (still installable, not offered as latest)."""
    import functools
    groups: dict[tuple[str, str], list[dict]] = {}
    for e in load_lock(cfg)["packages"]:
        if e.get("channel") == "candidate" and e.get("status") not in ("withdrawn", "protected"):
            groups.setdefault((e["package"], e["architecture"]), []).append(e)
    out = []
    for (pkg, arch), es in sorted(groups.items()):
        es.sort(key=functools.cmp_to_key(lambda a, b: debian.compare_versions(a["version"], b["version"])))
        for e in es[:-1]:
            out.append({"package": pkg, "architecture": arch, "version": e["version"], "superseded_by": es[-1]["version"]})
    return out


def render_review(m: dict, review: dict) -> str:
    """Human summary for the approval decision."""
    lv = review.get("levels", {})
    b = m["build"]
    lines = [
        f"Submission   {review['submission']}",
        f"State        {review['state'].upper()}",
        f"Package      {review.get('package', {}).get('identifier')} {review.get('package', {}).get('version')} "
        f"{review.get('package', {}).get('architecture')}",
        f"Size         {review.get('size')} bytes",
        f"SHA-256      {review.get('sha256')}",
        f"Provider     {b['provider']} ({PROVIDERS[b['provider']]})",
        f"Source       {m['source']['repository']} @ {m['source']['commit']}"
        + (f" ({m['source']['branch']})" if m['source'].get('branch') else ""),
        f"Built        {b['builtAt']} with {b['toolchain']}"
        + (f"; CI run {b['runId']}" if b.get("runId") else "; no CI run (local or hosted build)"),
        f"Minimum iOS  {m['compatibility']['minimumOS']} ({m['compatibility']['environment']})",
        "",
        f"Static validation         {'PASSED' if lv.get('static', {}).get('passed') else 'FAILED'}",
    ]
    sl = lv.get("simulated_lifecycle", {})
    if sl.get("passed") is None:
        lines.append("Simulated lifecycle       NOT RUN")
    else:
        steps = ", ".join(f"{s['step']} {'ok' if s['ok'] else 'FAILED'}" for s in sl.get("steps", []))
        lines.append(f"Simulated lifecycle       {'PASSED' if sl['passed'] else 'FAILED'} ({steps}; maintainer scripts not executed)")
    lines.append("Physical-device testing   NOT RECORDED")
    errs = [f for f in review.get("findings", []) if f["level"] == "error"]
    warns = [f for f in review.get("findings", []) if f["level"] == "warning"]
    if errs or warns:
        lines.append("")
    lines += [f"  ERROR [{f['code']}] {f['message']}" for f in errs]
    lines += [f"  warn  [{f['code']}] {f['message']}" for f in warns]
    if review.get("approval"):
        a = review["approval"]
        lines += ["", f"Approved     {a['sha256']} by {a['by']} at {a['at']}: {a['record']}"]
    elif review["state"] == "validated":
        lines += ["", "To approve exactly these bytes for a Candidate import:",
                  f"  bin/hcgrepo release approve {review['submission']} --sha256 {review['sha256']} "
                  "--by \"<name>\" --record \"<approval, verbatim>\""]
    return "\n".join(lines)

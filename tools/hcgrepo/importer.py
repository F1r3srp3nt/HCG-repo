"""Import .deb files into pool/ with provenance and the append-only publication lock.

GitHub imports require an explicit repository and an immutable reference (a full commit SHA that
the release tag / workflow run must match). There is deliberately no "latest" mode.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import tomllib
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import Report, RepoConfig, load_lock, save_lock
from .debinspect import DebInfo, InspectError, inspect_deb
from .repo import check_repository, expected_pool_rel, load_pool

SHA40 = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ImportError_(Exception):
    pass


def _now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def import_deb(cfg: RepoConfig, path: Path, provenance: dict, dry_run: bool = False,
               expect: dict | None = None) -> tuple[str, Report]:
    """Validate `path` together with the existing pool and, if everything passes, add it.

    `expect` may pin package/version/architecture/sha256; any mismatch rejects the import.
    Returns (status, report) where status is 'imported', 'already-present' or 'rejected'.
    """
    report = Report(Path(path).name)
    try:
        info = inspect_deb(Path(path))
    except InspectError as exc:
        report.error("deb-unreadable", str(exc))
        return "rejected", report
    actual = {"package": info.package, "version": info.version, "architecture": info.architecture, "sha256": info.sha256}
    for k, want in (expect or {}).items():
        if want and actual.get(k) != (want.lower() if k == "sha256" else want):
            report.error("unexpected-identity", f"expected {k} {want!r}, the package has {actual.get(k)!r}")
    if not report.ok:
        return "rejected", report

    lock = load_lock(cfg)
    rel = expected_pool_rel(info)
    same_key = [e for e in lock["packages"]
                if (e["package"], e["version"], e["architecture"]) == info.key]
    for e in same_key:
        if e["sha256"] != info.sha256:
            report.error("immutable-version",
                         f"{info.package} {info.version} {info.architecture} was already imported with different "
                         f"content (sha256 {e['sha256']}); bump the version instead of replacing it")
            return "rejected", report
    if same_key and (cfg.root / rel).exists():
        report.warn("already-present", f"{rel} is already in the pool with identical content")
        return "already-present", report
    if same_key and same_key[0].get("status") == "withdrawn":
        report.error("withdrawn-version", f"{info.package} {info.version} was withdrawn; publish a new version")
        return "rejected", report

    # Validate the candidate together with everything already in the pool.
    pool_report = Report("repository")
    pkgs = load_pool(cfg, pool_report, extra=[(info, rel)])
    new_entries = [] if same_key else [_lock_entry(info, rel)]  # identical bytes: restore under the existing entry
    trial_lock = {"schema": 1, "packages": lock["packages"] + new_entries}
    check_repository(cfg, pkgs, pool_report, lock=trial_lock)
    for level, code, msg in pool_report.items:
        # The candidate's provenance file is written only after validation passes.
        if code == "provenance-missing" and msg.startswith(rel):
            continue
        report.items.append((level, code, msg))
    if not report.ok:
        return "rejected", report
    if dry_run:
        return "validated", report

    dest = cfg.root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".deb.partial")
    shutil.copyfile(path, tmp)
    if inspect_deb(tmp).sha256 != info.sha256:
        tmp.unlink()
        raise ImportError_("copied file changed while importing")
    os.replace(tmp, dest)
    prov = dict(provenance)
    prov.update({
        "schema": 1, "importer": f"hcgrepo {__version__}", "imported_at": _now(),
        "package": info.package, "version": info.version, "architecture": info.architecture,
        "filename": rel, "size": info.size, "sha256": info.sha256, "sha512": info.sha512,
        "inspection": info.summary(),
    })
    Path(str(dest) + ".provenance.json").write_text(json.dumps(prov, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lock["packages"].extend(new_entries)
    save_lock(cfg, lock)
    return "imported", report


def _lock_entry(info: DebInfo, rel: str) -> dict:
    return {"package": info.package, "version": info.version, "architecture": info.architecture,
            "filename": rel, "size": info.size, "sha256": info.sha256, "status": "published",
            "imported_at": _now()}


def withdraw(cfg: RepoConfig, package: str, version: str, architecture: str, reason: str) -> str:
    """Remove a version from the index while keeping its lock record (so it can never be
    re-imported with different content)."""
    lock = load_lock(cfg)
    hits = [e for e in lock["packages"] if (e["package"], e["version"], e["architecture"]) == (package, version, architecture)]
    if not hits:
        raise ImportError_(f"{package} {version} {architecture} is not in repo-lock.json")
    for e in hits:
        e["status"] = "withdrawn"
        e["withdrawn_at"] = _now()
        e["withdrawn_reason"] = reason
        p = cfg.root / e["filename"]
        if p.exists():
            p.unlink()
    save_lock(cfg, lock)
    return hits[0]["filename"]


# ---------------------------------------------------------------------------- GitHub


class GitHub:
    def __init__(self, api: str | None = None, token: str | None = None):
        self.api = (api or os.environ.get("HCG_GITHUB_API") or "https://api.github.com").rstrip("/")
        self.token = token if token is not None else (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))

    def _req(self, url: str, accept: str = "application/vnd.github+json") -> urllib.request.Request:
        h = {"Accept": accept, "User-Agent": "hcgrepo-importer", "X-GitHub-Api-Version": "2022-11-28"}
        req = urllib.request.Request(url, headers=h)
        if self.token:
            # Unredirected: asset and artifact downloads redirect to pre-signed blob-storage URLs,
            # which must not receive (and reject) the GitHub token.
            req.add_unredirected_header("Authorization", f"Bearer {self.token}")
        return req

    def json(self, path: str):
        try:
            with urllib.request.urlopen(self._req(self.api + path), timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise ImportError_(f"GitHub API {path}: HTTP {exc.code}") from exc

    def download(self, url: str, dest: Path, accept: str = "application/octet-stream") -> None:
        try:
            with urllib.request.urlopen(self._req(url, accept), timeout=600) as resp, open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)
        except urllib.error.HTTPError as exc:
            raise ImportError_(f"download {url}: HTTP {exc.code}") from exc


def _check_ref_args(repo: str, commit: str) -> None:
    if not REPO_RE.match(repo or ""):
        raise ImportError_("--repo must be OWNER/NAME")
    if not SHA40.match((commit or "").lower()):
        raise ImportError_("--commit must be the full 40-character commit SHA the build came from")


def _verify_digest(path: Path, digest: str | None, expected_sha256: str | None) -> str:
    import hashlib
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest:
        algo, _, value = digest.partition(":")
        if algo == "sha256" and value.lower() != h:
            raise ImportError_(f"downloaded file sha256 {h} does not match GitHub's digest {value}")
    if expected_sha256 and expected_sha256.lower() != h:
        raise ImportError_(f"downloaded file sha256 {h} does not match --sha256 {expected_sha256}")
    return h


def fetch_release_asset(gh: GitHub, repo: str, tag: str, commit: str, asset_name: str,
                        workdir: Path, expected_sha256: str | None = None) -> tuple[Path, dict]:
    _check_ref_args(repo, commit)
    if not tag or tag.lower() == "latest":
        raise ImportError_("--tag must name a specific release tag; 'latest' is not accepted")
    rel = gh.json(f"/repos/{repo}/releases/tags/{tag}")
    if rel.get("draft"):
        raise ImportError_(f"release {tag} is a draft")
    ref = gh.json(f"/repos/{repo}/git/ref/tags/{tag}")
    obj = ref["object"]
    for _ in range(5):
        if obj["type"] != "tag":
            break
        obj = gh.json(f"/repos/{repo}/git/tags/{obj['sha']}")["object"]
    if obj["type"] != "commit" or obj["sha"].lower() != commit.lower():
        raise ImportError_(f"tag {tag} points to {obj['type']} {obj['sha']}, not the expected commit {commit}")
    assets = [a for a in rel.get("assets", []) if a["name"] == asset_name]
    if len(assets) != 1:
        names = ", ".join(a["name"] for a in rel.get("assets", [])) or "none"
        raise ImportError_(f"release {tag} has no single asset named {asset_name!r} (assets: {names})")
    a = assets[0]
    dest = workdir / Path(asset_name).name
    gh.download(f"{gh.api}/repos/{repo}/releases/assets/{a['id']}", dest)
    if dest.stat().st_size != a["size"]:
        raise ImportError_(f"downloaded {dest.stat().st_size} bytes, release asset is {a['size']} bytes")
    h = _verify_digest(dest, a.get("digest"), expected_sha256)
    return dest, {
        "source": "github-release", "repository": repo, "tag": tag, "commit": commit.lower(),
        "release_id": rel["id"], "release_url": rel.get("html_url"), "prerelease": bool(rel.get("prerelease")),
        "asset_id": a["id"], "asset_name": a["name"], "asset_digest": a.get("digest"),
        "downloaded_sha256": h,
    }


def fetch_workflow_artifact(gh: GitHub, repo: str, run_id: int, commit: str, artifact_name: str,
                            member: str | None, workdir: Path, expected_sha256: str | None = None) -> tuple[Path, dict]:
    _check_ref_args(repo, commit)
    if not gh.token:
        raise ImportError_("workflow artifacts can only be downloaded with a token (set GITHUB_TOKEN)")
    run = gh.json(f"/repos/{repo}/actions/runs/{int(run_id)}")
    if run.get("head_sha", "").lower() != commit.lower():
        raise ImportError_(f"run {run_id} built {run.get('head_sha')}, not the expected commit {commit}")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ImportError_(f"run {run_id} is {run.get('status')}/{run.get('conclusion')}; only successful runs are imported")
    arts = gh.json(f"/repos/{repo}/actions/runs/{int(run_id)}/artifacts?per_page=100").get("artifacts", [])
    hits = [a for a in arts if a["name"] == artifact_name]
    if len(hits) != 1:
        raise ImportError_(f"run {run_id} has no single artifact named {artifact_name!r} "
                           f"(artifacts: {', '.join(a['name'] for a in arts) or 'none'})")
    a = hits[0]
    if a.get("expired"):
        raise ImportError_(f"artifact {artifact_name} of run {run_id} has expired")
    zpath = workdir / "artifact.zip"
    gh.download(f"{gh.api}/repos/{repo}/actions/artifacts/{a['id']}/zip", zpath, accept="application/vnd.github+json")
    zip_sha = _verify_digest(zpath, a.get("digest"), None)
    with zipfile.ZipFile(zpath) as z:
        debs = [n for n in z.namelist() if n.endswith(".deb") and not n.endswith("/")]
        if member:
            if member not in debs:
                raise ImportError_(f"artifact does not contain {member!r} (contains: {', '.join(debs) or 'no .deb'})")
            chosen = member
        elif len(debs) == 1:
            chosen = debs[0]
        else:
            raise ImportError_(f"artifact contains {len(debs)} .deb files; choose one with --member ({', '.join(debs)})")
        if chosen.startswith("/") or ".." in chosen.split("/"):
            raise ImportError_(f"unsafe path in artifact: {chosen!r}")
        dest = workdir / Path(chosen).name
        with z.open(chosen) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    h = _verify_digest(dest, None, expected_sha256)
    return dest, {
        "source": "github-workflow-artifact", "repository": repo, "run_id": int(run_id), "commit": commit.lower(),
        "workflow": run.get("path"), "run_url": run.get("html_url"), "run_attempt": run.get("run_attempt"),
        "artifact_id": a["id"], "artifact_name": artifact_name, "artifact_digest": a.get("digest"),
        "artifact_zip_sha256": zip_sha, "member": chosen, "downloaded_sha256": h,
    }


def temp_workdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="hcgrepo-import-")


# ---------------------------------------------------------------------------- import requests
#
# imports/<name>.toml pins one package to an immutable source. The "Prepare packages" workflow
# processes requests on GitHub's runners (which can download artifacts and run macOS builds) and
# commits the validated result for review. See docs/PUBLISHING.md.

RECIPES = {"hcg-terminal-unleashed"}
LAYOUTS = {"rootless", "rootful"}
SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
REQUEST_KINDS = {"github-artifact", "github-release", "build"}


def load_request(path: Path) -> dict:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    req, src = data.get("request", {}), data.get("source", {})
    kind = src.get("kind")
    problems = []
    for k in ("package", "version", "architecture"):
        if not req.get(k):
            problems.append(f"[request] {k} is required")
    if kind not in REQUEST_KINDS:
        problems.append(f"[source] kind must be one of {sorted(REQUEST_KINDS)}")
    if not REPO_RE.match(src.get("repository", "")):
        problems.append("[source] repository must be OWNER/NAME")
    if not SHA40.match(str(src.get("commit", "")).lower()):
        problems.append("[source] commit must be a full 40-character SHA")
    if src.get("sha256") and not re.fullmatch(r"[0-9a-f]{64}", src["sha256"].lower()):
        problems.append("[source] sha256 must be 64 hex characters")
    if kind == "github-artifact":
        if not isinstance(src.get("run_id"), int) or not src.get("artifact"):
            problems.append("github-artifact needs integer run_id and artifact")
    elif kind == "github-release":
        if not src.get("tag") or src.get("tag", "").lower() == "latest" or not src.get("asset"):
            problems.append("github-release needs an exact tag (not 'latest') and asset")
    elif kind == "build":
        if src.get("recipe") not in RECIPES:
            problems.append(f"build recipe must be one of {sorted(RECIPES)}")
        if src.get("layout") not in LAYOUTS:
            problems.append(f"build layout must be one of {sorted(LAYOUTS)}")
        ent = src.get("entitlements", "")
        if not SAFE_PATH.match(ent) or ".." in ent.split("/"):
            problems.append("build entitlements must be a plain relative path inside the source repository")
    if problems:
        raise ImportError_(f"{path.name}: " + "; ".join(problems))
    return {"id": path.stem, "file": str(path), "request": req, "source": src}


def request_is_done(cfg: RepoConfig, r: dict) -> bool:
    req = r["request"]
    return any((e["package"], e["version"], e["architecture"]) == (req["package"], req["version"], req["architecture"])
               for e in load_lock(cfg)["packages"])


def pending_requests(cfg: RepoConfig) -> list[dict]:
    d = cfg.root / "imports"
    reqs = [load_request(p) for p in sorted(d.glob("*.toml"))] if d.exists() else []
    return [r for r in reqs if not request_is_done(cfg, r)]


def run_request(cfg: RepoConfig, r: dict, gh: GitHub, workdir: Path, built_deb: Path | None = None,
                builder: dict | None = None, dry_run: bool = False) -> tuple[str, Report]:
    req, src = r["request"], r["source"]
    expect = {"package": req["package"], "version": req["version"], "architecture": req["architecture"],
              "sha256": src.get("sha256")}
    if src["kind"] == "github-artifact":
        path, prov = fetch_workflow_artifact(gh, src["repository"], src["run_id"], src["commit"], src["artifact"],
                                             src.get("member"), workdir, src.get("sha256"))
    elif src["kind"] == "github-release":
        path, prov = fetch_release_asset(gh, src["repository"], src["tag"], src["commit"], src["asset"],
                                         workdir, src.get("sha256"))
    else:
        if built_deb is None:
            raise ImportError_(f"{r['id']}: build requests need the built .deb (--deb)")
        path = built_deb
        prov = {"source": "source-build", "recipe": src["recipe"], "repository": src["repository"],
                "commit": src["commit"].lower(), "layout": src["layout"], "entitlements_file": src["entitlements"]}
        prov.update(builder or {})
    prov["request"] = r["id"]
    return import_deb(cfg, path, prov, dry_run=dry_run, expect=expect)

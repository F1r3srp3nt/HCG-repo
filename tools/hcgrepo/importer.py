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
from .config import check_package_meta, load_package_meta
from .debinspect import validate_deb
from .repo import check_dependencies, check_repository, expected_pool_rel, load_pool

SHA40 = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ImportError_(Exception):
    pass


def _now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def import_deb(cfg: RepoConfig, path: Path, provenance: dict, dry_run: bool = False,
               expect: dict | None = None, channel: str = "stable") -> tuple[str, Report]:
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

    meta = load_package_meta(cfg, info.package)
    if meta.access_for(info.version) != "public":
        return _import_protected(cfg, info, meta, rel, provenance, report, same_key, dry_run)

    # Validate the candidate together with everything already in the pool.
    pool_report = Report("repository")
    pkgs = load_pool(cfg, pool_report, extra=[(info, rel)])
    new_entries = [] if same_key else [_lock_entry(info, rel)]  # identical bytes: restore under the existing entry
    if channel != "stable":
        for ent in new_entries:
            ent["channel"] = channel
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


def _protected_provenance(info: DebInfo, rel: str, provenance: dict) -> dict:
    prov = dict(provenance)
    prov.update({
        "schema": 1, "importer": f"hcgrepo {__version__}", "imported_at": _now(),
        "package": info.package, "version": info.version, "architecture": info.architecture,
        "filename": rel, "size": info.size, "md5": info.md5, "sha1": info.sha1, "sha256": info.sha256,
        "sha512": info.sha512, "control": info.control_text, "inspection": info.summary(),
        "storage": "protected: this .deb is not stored in this public repository",
    })
    return prov


def _import_protected(cfg: RepoConfig, info: DebInfo, meta, rel: str, provenance: dict, report: Report,
                      same_key: list, dry_run: bool) -> tuple[str, Report]:
    """connect/paid packages: validate fully, record control data and hashes, never store the file."""
    r = validate_deb(info, cfg, meta)
    check_package_meta(meta, r)
    report.extend(r)
    pool_report = Report("repository")
    others = [p.info for p in load_pool(cfg, pool_report)]
    from .repo import protected_packages
    others += [p.info for p in protected_packages(cfg)]
    check_dependencies(cfg, others + [info], pool_report)
    for level, code, msg in pool_report.items:
        if info.package in msg:
            report.items.append((level, code, msg))
    if not report.ok:
        return "rejected", report
    if dry_run:
        return "validated", report
    lock = load_lock(cfg)
    prov_path = cfg.root / (rel + ".provenance.json")
    prov_path.parent.mkdir(parents=True, exist_ok=True)
    prov_path.write_text(json.dumps(_protected_provenance(info, rel, provenance), indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    if not same_key:
        entry = _lock_entry(info, rel)
        entry["status"] = "protected"
        lock["packages"].append(entry)
        save_lock(cfg, lock)
    report.warn("protected", f"{info.package} ({meta.access_for(info.version)}) recorded as protected; the .deb was NOT stored here. "
                             "Deliver this exact file (sha256 above) to HCG Connect's protected storage.")
    return "protected", report


def protect(cfg: RepoConfig, package: str, version: str, architecture: str, move_to: Path) -> Path:
    """Move an imported package out of the public pool: keep its lock entry (status 'protected'),
    control data and hashes; move the .deb to `move_to`, which must be outside this repository."""
    move_to = Path(move_to).resolve()
    if cfg.root.resolve() in [move_to, *move_to.parents]:
        raise ImportError_("--move-to must be outside the repository, so the file is not committed again")
    lock = load_lock(cfg)
    hits = [e for e in lock["packages"] if (e["package"], e["version"], e["architecture"]) == (package, version, architecture)]
    if not hits:
        raise ImportError_(f"{package} {version} {architecture} is not in repo-lock.json")
    e = hits[0]
    if load_package_meta(cfg, package).access_for(version) == "public":
        raise ImportError_(f"{package} {version} is public: set [access] level to connect or paid in "
                           f"packages/{package}/package.toml (and remove it from exception_versions) first")
    src = cfg.root / e["filename"]
    prov_path = cfg.root / (e["filename"] + ".provenance.json")
    prov = json.loads(prov_path.read_text(encoding="utf-8")) if prov_path.exists() else {}
    if src.exists():
        info = inspect_deb(src)
        if info.sha256 != e["sha256"]:
            raise ImportError_(f"{src} does not match its lock entry")
        prov = _protected_provenance(info, e["filename"], {k: v for k, v in prov.items()
                                                            if k not in ("imported_at", "importer")})
        move_to.mkdir(parents=True, exist_ok=True)
        dest = move_to / src.name
        shutil.copyfile(src, dest)
        if hash_sha256(dest) != e["sha256"]:
            raise ImportError_("copy to --move-to failed verification")
        src.unlink()
    elif not prov.get("control"):
        raise ImportError_(f"{src} is missing and no control data is recorded; cannot protect")
    else:
        dest = move_to / src.name
    prov["protected_at"] = _now()
    prov_path.write_text(json.dumps(prov, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    e["status"] = "protected"
    e["protected_at"] = prov["protected_at"]
    save_lock(cfg, lock)
    return dest


def hash_sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
            hint = (" (a private repository reads as 404 without access: set the HCG_IMPORT_TOKEN secret to a read-only "
                    "token with actions:read and contents:read on it)") if exc.code in (401, 403, 404) else ""
            raise ImportError_(f"GitHub API {path}: HTTP {exc.code}{hint}") from exc

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


def set_channel(cfg: RepoConfig, package: str, version: str, architecture: str, channel: str) -> str:
    """Move one recorded build between the stable source and the candidate (testing) source. Only the lock entry's
    channel changes; the file, its checksums and provenance stay as they are (immutable)."""
    from .config import LOCK_CHANNELS, entry_channel
    if channel not in LOCK_CHANNELS:
        raise ImportError_(f"channel must be one of {LOCK_CHANNELS}")
    lock = load_lock(cfg)
    hits = [e for e in lock["packages"] if (e["package"], e["version"], e["architecture"]) == (package, version, architecture)]
    if not hits:
        raise ImportError_(f"{package} {version} {architecture} is not in repo-lock.json")
    e = hits[0]
    if e.get("status") == "protected" and channel == "candidate":
        raise ImportError_("protected packages cannot be in the public candidate channel")
    if channel == "stable" and load_package_meta(cfg, package).is_free_demo(version):
        raise ImportError_(f"{package} {version} is a free demo of a sign-in/paid package; free demos stay in the "
                           "candidate channel. Release a separate build at the package's own access level instead")
    before = entry_channel(e)
    if channel == "stable":
        e.pop("channel", None)
    else:
        e["channel"] = channel
    save_lock(cfg, lock)
    return before


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
REQUEST_KINDS = {"github-artifact", "github-release", "build", "local-submission", "https-artifact", "local-file"}
HTTPS_PROVIDERS = {"hcg-server-build-host", "developer-upload"}
LOCAL_PROVIDERS = {"executable-maker-local", "hcg-server-build-host", "developer-upload"}
AUTH_ENV_RE = re.compile(r"^HCG_[A-Z0-9_]{2,60}$")
SUBMISSION_PATH_RE = re.compile(r"^submissions/[A-Za-z0-9._-]+/submission\.json$")


class AwaitingLocalImport(Exception):
    """A local-submission request whose approved bytes are only on the owner's machine (not an error)."""


# A local-file request imports a .deb that was built outside GitHub Actions (e.g. by HCG Executable Maker's Unleashify
# on the owner's Mac) and committed next to the request under imports/local/. The source repository may be private:
# only its name and commit are recorded, never its contents, and no token is needed to import.
LOCAL_DIR = "imports/local/"
# Debian file names carry the version, so a committed .deb's path may also hold "~" and "+" (e.g. 0.1.0~beta1).
LOCAL_PATH = re.compile(r"^[A-Za-z0-9._/~+-]+$")
LOCAL_PROVENANCE = ("built_by", "toolchain", "built_at", "host", "source_visibility", "validation")


def load_request(path: Path) -> dict:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    req, src = data.get("request", {}), data.get("source", {})
    kind = src.get("kind")
    problems = []
    for k in ("package", "version", "architecture"):
        if not req.get(k):
            problems.append(f"[request] {k} is required")
    if req.get("channel", "stable") not in ("stable", "candidate"):
        problems.append("[request] channel must be stable or candidate")
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
    elif kind == "local-file":
        rel = src.get("path", "")
        if not rel.startswith(LOCAL_DIR) or not rel.endswith(".deb") or not LOCAL_PATH.match(rel) or ".." in rel.split("/"):
            problems.append(f"local-file path must be a .deb under {LOCAL_DIR} (plain relative path)")
        if not src.get("sha256"):
            problems.append("local-file needs the exact sha256 of the committed .deb")
        if not src.get("built_by"):
            problems.append("local-file needs built_by (the tool and version that produced the package)")
    elif kind == "build":
        if src.get("recipe") not in RECIPES:
            problems.append(f"build recipe must be one of {sorted(RECIPES)}")
        if src.get("layout") not in LAYOUTS:
            problems.append(f"build layout must be one of {sorted(LAYOUTS)}")
        ent = src.get("entitlements", "")
        if not SAFE_PATH.match(ent) or ".." in ent.split("/"):
            problems.append("build entitlements must be a plain relative path inside the source repository")
    elif kind in ("local-submission", "https-artifact"):
        if not src.get("sha256"):
            problems.append(f"{kind} needs the approved sha256")
        if not SUBMISSION_PATH_RE.match(str(src.get("submission", ""))):
            problems.append(f"{kind} needs submission = \"submissions/<id>/submission.json\"")
        if src.get("run_id") is not None:
            problems.append(f"{kind} has no CI run: run_id is only for github-artifact")
        if kind == "local-submission" and src.get("provider") not in LOCAL_PROVIDERS:
            problems.append(f"local-submission provider must be one of {sorted(LOCAL_PROVIDERS)}")
        if kind == "https-artifact":
            if src.get("provider") not in HTTPS_PROVIDERS:
                problems.append(f"https-artifact provider must be one of {sorted(HTTPS_PROVIDERS)}")
            if not str(src.get("url", "")).startswith("https://"):
                problems.append("https-artifact url must be https://")
            if not isinstance(src.get("size"), int) or src.get("size", 0) <= 0:
                problems.append("https-artifact needs the exact size in bytes")
            if src.get("auth_env") is not None and not AUTH_ENV_RE.match(str(src["auth_env"])):
                problems.append("auth_env must name an environment variable HCG_..., never hold a credential")
        if req.get("channel", "stable") != "candidate":
            problems.append(f"{kind} imports go to the candidate channel only")
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


def _submission_approval(cfg: RepoConfig, src: dict) -> dict:
    """The owner's recorded approval of exactly src.sha256 (submissions/<id>/review.json)."""
    review_path = cfg.root / Path(src["submission"]).parent / "review.json"
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImportError_(f"{review_path.relative_to(cfg.root)}: no readable review: {exc}") from exc
    a = review.get("approval") or {}
    if a.get("sha256") != src["sha256"].lower() or not a.get("by") or not a.get("record"):
        raise ImportError_(f"{review_path.relative_to(cfg.root)} records no owner approval of sha256 {src['sha256']}")
    return a


def _incoming_artifact(src: dict, incoming: Path | None) -> Path:
    sid = Path(src["submission"]).parent.name
    cand = (incoming / sid / "artifact.deb") if incoming else None
    if cand is None or not cand.is_file():
        raise AwaitingLocalImport(f"the approved bytes of {sid} are in the owner's private incoming store, not here; "
                                  "run `bin/hcgrepo release prepare` where they are")
    return cand


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise ImportError_(f"refused redirect to a non-HTTPS URL ({code})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_https_artifact(url: str, workdir: Path, sha256: str, size: int, auth_env: str | None = None) -> Path:
    """Download an approved artifact from an authorized HTTPS host (HCG Server Build Host, developer uploads).

    The credential, if any, comes from the environment variable named by auth_env (a CI secret) and is
    never sent to a redirect target. The exact size and SHA-256 must match.
    """
    if not url.startswith("https://"):
        raise ImportError_("artifact URL must be https://")
    req = urllib.request.Request(url, headers={"User-Agent": "hcgrepo-importer", "Accept": "application/octet-stream"})
    if auth_env:
        token = os.environ.get(auth_env)
        if not token:
            raise ImportError_(f"{auth_env} is not set; the artifact host needs an authorized token")
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    dest = workdir / "artifact.deb"
    opener = urllib.request.build_opener(_HttpsOnly)
    try:
        with opener.open(req, timeout=600) as resp, open(dest, "wb") as f:
            got = 0
            while chunk := resp.read(1 << 20):
                got += len(chunk)
                if got > size:
                    raise ImportError_(f"download is larger than the approved {size} bytes")
                f.write(chunk)
    except urllib.error.HTTPError as exc:
        raise ImportError_(f"download {url}: HTTP {exc.code}") from exc
    if dest.stat().st_size != size:
        raise ImportError_(f"downloaded {dest.stat().st_size} bytes, the approved size is {size}")
    _verify_digest(dest, None, sha256)
    return dest


def run_request(cfg: RepoConfig, r: dict, gh: GitHub, workdir: Path, built_deb: Path | None = None,
                builder: dict | None = None, dry_run: bool = False, incoming: Path | None = None) -> tuple[str, Report]:
    req, src = r["request"], r["source"]
    expect = {"package": req["package"], "version": req["version"], "architecture": req["architecture"],
              "sha256": src.get("sha256")}
    if src["kind"] == "github-artifact":
        path, prov = fetch_workflow_artifact(gh, src["repository"], src["run_id"], src["commit"], src["artifact"],
                                             src.get("member"), workdir, src.get("sha256"))
    elif src["kind"] == "github-release":
        path, prov = fetch_release_asset(gh, src["repository"], src["tag"], src["commit"], src["asset"],
                                         workdir, src.get("sha256"))
    elif src["kind"] in ("local-submission", "https-artifact"):
        approval = _submission_approval(cfg, src)
        if src["kind"] == "local-submission":
            path = _incoming_artifact(src, incoming)
        else:
            path = fetch_https_artifact(src["url"], workdir, src["sha256"], src["size"], src.get("auth_env"))
        prov = {"source": "artifact-submission", "provider": src["provider"], "repository": src["repository"],
                "commit": src["commit"].lower(), "submission": src["submission"], "approval": approval}
        if src["kind"] == "https-artifact":
            prov["url"] = src["url"]
    elif src["kind"] == "local-file":
        path = cfg.root / src["path"]
        if not path.is_file():
            raise ImportError_(f"{r['id']}: {src['path']} is not in the repository; commit the .deb next to the request")
        prov = {"source": "local-file", "original_filename": path.name, "repository": src["repository"],
                "commit": src["commit"].lower()}
        prov.update({k: src[k] for k in LOCAL_PROVENANCE if src.get(k)})
    else:
        if built_deb is None:
            raise ImportError_(f"{r['id']}: build requests need the built .deb (--deb)")
        path = built_deb
        prov = {"source": "source-build", "recipe": src["recipe"], "repository": src["repository"],
                "commit": src["commit"].lower(), "layout": src["layout"], "entitlements_file": src["entitlements"]}
        prov.update(builder or {})
    prov["request"] = r["id"]
    # Distribution v2: secrets, maintainer-script behaviour, privileged helpers and a simulated dpkg lifecycle
    # (scripts never executed) for every request-driven import.
    from .deepcheck import deep_validate
    try:
        deep, evidence = deep_validate(inspect_deb(path))
    except InspectError as exc:
        deep, evidence = Report(path.name), None
        deep.error("deb-unreadable", str(exc))
    if not deep.ok:
        return "rejected", deep
    prov["validation_levels"] = {"static": {"passed": True},
                                 "simulated_lifecycle": {k: evidence["lifecycle"][k] for k in ("passed", "scripts_executed", "steps")},
                                 "physical_device": {"recorded": False}}
    ch = req.get("channel", "stable")
    status, rep = import_deb(cfg, path, prov, dry_run=dry_run, expect=expect, channel=ch)
    for level, code, msg in deep.items:
        rep.items.append((level, code, msg))
    if status == "imported" and not dry_run and ch != "stable":
        set_channel(cfg, req["package"], req["version"], req["architecture"], ch)
    return status, rep

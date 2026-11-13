"""Repository configuration, per-package presentation metadata and the publication lock."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


class ConfigError(ValueError):
    pass


@dataclass
class Report:
    """Collects errors and warnings for one subject (a package, the repo, a URL)."""

    subject: str
    items: list[tuple[str, str, str]] = field(default_factory=list)

    def error(self, code: str, message: str) -> None:
        self.items.append(("error", code, message))

    def warn(self, code: str, message: str) -> None:
        self.items.append(("warning", code, message))

    def extend(self, other: "Report") -> None:
        for level, code, msg in other.items:
            self.items.append((level, code, f"{other.subject}: {msg}" if other.subject != self.subject else msg))

    @property
    def errors(self) -> list[tuple[str, str]]:
        return [(c, m) for lvl, c, m in self.items if lvl == "error"]

    @property
    def warnings(self) -> list[tuple[str, str]]:
        return [(c, m) for lvl, c, m in self.items if lvl == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> set[str]:
        return {c for _, c, _ in self.items}

    def render(self) -> str:
        lines = []
        for level, code, msg in self.items:
            lines.append(f"  {'ERROR' if level == 'error' else 'warn '} [{code}] {msg}")
        return "\n".join(lines)


def normalize_base_url(url: str) -> str:
    """Validate and normalise a public base URL: absolute http(s), no query/fragment, trailing '/'."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ConfigError(f"base URL must be an absolute http(s) URL, got {url!r}")
    if parts.query or parts.fragment:
        raise ConfigError(f"base URL must not have a query or fragment: {url!r}")
    path = parts.path or "/"
    if "//" in path or "/../" in path + "/" or "/./" in path + "/":
        raise ConfigError(f"base URL path is not normalised: {url!r}")
    if not path.endswith("/"):
        path += "/"
    return f"{parts.scheme}://{parts.netloc.lower()}{path}"


@dataclass
class RepoConfig:
    root: Path
    origin: str
    label: str
    suite: str
    codename: str
    version: str
    description: str
    repo_subdir: str
    architectures: list[str]
    namespace: str
    reserved_bundle_ids: list[str]
    urls: dict[str, str]
    default_target: str
    signing_required: bool
    signing_fingerprint: str
    public_key_file: str
    external_deps: dict[str, dict]
    products: list[dict]

    @property
    def pool_dir(self) -> Path:
        return self.root / "pool"

    @property
    def lock_path(self) -> Path:
        return self.root / "repo-lock.json"

    @property
    def packages_meta_dir(self) -> Path:
        return self.root / "packages"

    def base_url(self, target: str | None = None, explicit: str | None = None) -> str:
        if explicit:
            return normalize_base_url(explicit)
        env = os.environ.get("HCG_REPO_BASE_URL")
        if env and not target:
            return normalize_base_url(env)
        target = target or self.default_target
        if target not in self.urls:
            raise ConfigError(f"unknown target {target!r}; known: {', '.join(self.urls)}")
        return normalize_base_url(self.urls[target])

    def repo_url(self, base_url: str) -> str:
        return normalize_base_url(base_url) + self.repo_subdir + "/"


def load_config(root: Path | None = None) -> RepoConfig:
    root = Path(root or os.environ.get("HCG_REPO_ROOT") or ROOT)
    with open(root / "config" / "repo.toml", "rb") as f:
        cfg = tomllib.load(f)
    with open(root / "config" / "external-dependencies.toml", "rb") as f:
        ext = tomllib.load(f).get("packages", {})
    products_path = root / "config" / "products.toml"
    products = []
    if products_path.exists():
        with open(products_path, "rb") as f:
            products = tomllib.load(f).get("products", [])
    r = cfg["repository"]
    sub = r.get("repo_subdir", "repo").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*", sub):
        raise ConfigError(f"invalid repo_subdir {sub!r}")
    archs = list(r["architectures"])
    for a in archs:
        if a not in ("iphoneos-arm64", "iphoneos-arm", "iphoneos-arm64e"):
            raise ConfigError(f"unsupported architecture in config: {a}")
    urls = dict(cfg.get("urls", {}))
    default_target = urls.pop("default_target", "pages")
    for k, v in urls.items():
        urls[k] = normalize_base_url(v)
    s = cfg.get("signing", {})
    return RepoConfig(
        root=root, origin=r["origin"], label=r["label"], suite=r["suite"], codename=r["codename"],
        version=str(r["version"]), description=r["description"], repo_subdir=sub, architectures=archs,
        namespace=r.get("package_namespace", ""), reserved_bundle_ids=list(r.get("reserved_bundle_ids", [])),
        urls=urls, default_target=default_target, signing_required=bool(s.get("required", False)),
        signing_fingerprint=s.get("fingerprint", "").replace(" ", "").upper(),
        public_key_file=s.get("public_key_file", "keys/hcg-repo-archive-key.asc"),
        external_deps=ext, products=products,
    )


# ---------------------------------------------------------------------------- package metadata

STATUSES = ("experimental", "device-tested")
REQUIRED_TEST_FIELDS = ("device", "ios", "jailbreak", "date", "result", "evidence")


@dataclass
class PackageMeta:
    package: str
    directory: Path | None
    summary: str = ""
    icon: str | None = None
    header: str | None = None
    tint: str | None = None
    entitlements: dict[str, dict] = field(default_factory=dict)
    allow_setuid: list[str] = field(default_factory=list)
    versions: dict[str, dict] = field(default_factory=dict)
    changelog: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def status(self, version: str) -> str:
        return self.versions.get(version, {}).get("status", "experimental")

    def device_tests(self, version: str) -> list[dict]:
        return list(self.versions.get(version, {}).get("device_tests", []))


def rel_path(p: str) -> str:
    """'./var/jb/x' or '/var/jb/x' → 'var/jb/x'."""
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def load_package_meta(cfg: RepoConfig, package: str) -> PackageMeta:
    d = cfg.packages_meta_dir / package
    if not (d / "package.toml").exists():
        return PackageMeta(package=package, directory=None)
    with open(d / "package.toml", "rb") as f:
        data = tomllib.load(f)
    disp = data.get("display", {})
    review = data.get("review", {})
    meta = PackageMeta(
        package=package, directory=d, summary=disp.get("summary", ""), icon=disp.get("icon"),
        header=disp.get("header"), tint=disp.get("tint"), notes=disp.get("notes", ""),
        entitlements={rel_path(k): v for k, v in review.get("entitlements", {}).items()},
        allow_setuid=[rel_path(p) for p in review.get("allow_setuid", [])],
        versions=data.get("versions", {}),
    )
    cl = d / "CHANGELOG.md"
    if cl.exists():
        meta.changelog = parse_changelog(cl.read_text(encoding="utf-8"))
    return meta


def parse_changelog(text: str) -> dict[str, str]:
    """'## <version>' headings → body text."""
    out: dict[str, str] = {}
    cur = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(\S+)", line)
        if m:
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1), []
        elif cur:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip()
    return out


def check_package_meta(meta: PackageMeta, report: Report) -> None:
    for asset in (meta.icon, meta.header):
        if asset and (meta.directory is None or not (meta.directory / asset).is_file()):
            report.error("meta-missing-asset", f"package.toml references missing file {asset!r}")
    if meta.tint and not re.fullmatch(r"#[0-9A-Fa-f]{6}", meta.tint):
        report.error("meta-bad-tint", f"tint must be #RRGGBB, got {meta.tint!r}")
    for ver, v in meta.versions.items():
        st = v.get("status", "experimental")
        if st not in STATUSES:
            report.error("meta-bad-status", f"version {ver}: status must be one of {STATUSES}, got {st!r}")
        if st == "device-tested":
            tests = v.get("device_tests", [])
            passing = [t for t in tests if t.get("result") == "pass"]
            if not passing:
                report.error("meta-device-tested-without-evidence",
                             f"version {ver} is labelled device-tested but has no passing device_tests entry")
            for t in tests:
                missing = [k for k in REQUIRED_TEST_FIELDS if not str(t.get(k, "")).strip()]
                if missing:
                    report.error("meta-device-test-incomplete",
                                 f"version {ver}: device test entry missing {', '.join(missing)}")


# ---------------------------------------------------------------------------- lock


def load_lock(cfg: RepoConfig) -> dict:
    if not cfg.lock_path.exists():
        return {"schema": 1, "packages": []}
    data = json.loads(cfg.lock_path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("packages"), list):
        raise ConfigError("repo-lock.json: unsupported schema")
    return data


def save_lock(cfg: RepoConfig, lock: dict) -> None:
    lock["packages"].sort(key=lambda e: (e["package"], e["architecture"], e["filename"]))
    tmp = cfg.lock_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, cfg.lock_path)

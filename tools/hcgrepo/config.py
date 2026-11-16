"""Repository configuration, per-package presentation metadata and the publication lock."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

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


def normalize_store_url(url: str) -> str:
    """HCG Store base URL: like a base URL, but HTTPS only (the store sets sign-in cookies)."""
    out = normalize_base_url(url)
    if not out.startswith("https://"):
        raise ConfigError(f"[store] base_url must be an https:// URL, got {url!r}")
    return out


# ---------------------------------------------------------------------------- products

# Optional product-level `access` (connect | paid puts the product under "Sign-in required"). Who may
# download a package is decided per package by `[access] level` in packages/<id>/package.toml
# (docs/ACCESS.md); this value only drives the website and the product page's store actions.
PRODUCT_ACCESS = ("", "public", "connect", "paid")
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


@dataclass
class Product:
    """One entry of config/products.toml: a product page at products/<key>/ and, when it is sold or
    licensed through HCG Store, its HCG Store link. An empty store_slug means it is not in HCG Store."""

    key: str
    name: str
    store_slug: str = ""
    store_edition: str = ""
    access: str = ""
    package_ids: list[str] = field(default_factory=list)
    layouts: list[str] = field(default_factory=list)
    kind: str = ""
    status: str = "Not available"
    summary: str = ""
    detail: str = ""

    @property
    def in_store(self) -> bool:
        return bool(self.store_slug)

    @property
    def restricted(self) -> bool:
        return self.access in ("connect", "paid")

    def store_path(self) -> str:
        """Store product route, relative to the store base: apps/<slug>[?edition=<edition>]."""
        path = f"apps/{self.store_slug}"
        return path + (f"?edition={self.store_edition}" if self.store_edition else "")


def parse_products(raw: list) -> list[Product]:
    products: list[Product] = []
    owners: dict[str, str] = {}
    for i, d in enumerate(raw):
        where = f"config/products.toml product #{i + 1}"
        if not isinstance(d, dict):
            raise ConfigError(f"{where}: must be a table")
        for k in ("key", "name"):
            if not str(d.get(k, "")).strip():
                raise ConfigError(f"{where}: `{k}` is required")
        p = Product(
            key=d["key"], name=d["name"], store_slug=d.get("store_slug", ""), store_edition=d.get("store_edition", ""),
            access=d.get("access", ""), package_ids=list(d.get("package_ids", [])),
            layouts=list(d.get("layouts", [])), kind=d.get("kind", ""), status=d.get("status", "Not available"),
            summary=d.get("summary", ""), detail=d.get("detail", ""),
        )
        where = f"config/products.toml product {p.key!r}"
        if p.store_edition and not p.store_slug:
            raise ConfigError(f"{where}: `store_edition` needs `store_slug`")
        if p.restricted and not p.store_slug:
            raise ConfigError(f"{where}: connect/paid products need `store_slug` (access is granted by HCG Store)")
        for k in ("key",) + (("store_slug",) if p.store_slug else ()) + (("store_edition",) if p.store_edition else ()):
            if not SLUG_RE.fullmatch(getattr(p, k)):
                raise ConfigError(f"{where}: `{k}` must be lowercase letters, digits and '-', got {getattr(p, k)!r}")
        if p.access not in PRODUCT_ACCESS:
            raise ConfigError(f"{where}: `access` must be public, connect or paid when given, got {p.access!r}")
        for a in p.layouts:
            if a not in ("iphoneos-arm64", "iphoneos-arm", "iphoneos-arm64e"):
                raise ConfigError(f"{where}: unknown layout {a!r}")
        if any(q.key == p.key for q in products):
            raise ConfigError(f"{where}: duplicate key")
        for pkg in p.package_ids:
            if pkg in owners:
                raise ConfigError(f"{where}: package {pkg} is already mapped to product {owners[pkg]!r}")
            owners[pkg] = p.key
        products.append(p)
    return products


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
    products: list[Product]
    store_base_url: str
    access_require_explicit: bool = True
    payment_endpoint: str = ""

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

    # ---- products and HCG Store

    def product_for_package(self, package: str) -> Product | None:
        return next((p for p in self.products if package in p.package_ids), None)

    def product_level(self, p: Product) -> str:
        """The strictest access level of a product: its own `access`, or any of its packages'
        `[access] level` (docs/ACCESS.md). Empty when nothing is declared."""
        levels = {p.access} if p.access else set()
        for pkg in p.package_ids:
            meta = load_package_meta(self, pkg)
            if meta.access_explicit:
                levels.add(meta.access)
        return next((lvl for lvl in ("paid", "connect", "public") if lvl in levels), "")

    def store_product_url(self, p: Product) -> str:
        return self.store_base_url + p.store_path()

    def store_login_url(self, p: Product) -> str:
        return self.store_base_url + "auth/login?return_to=" + quote("/" + p.store_path(), safe="")

    def store_access_url(self, p: Product) -> str:
        return f"{self.store_base_url}api/v1/repo/products/{p.key}/access"


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
    store = cfg.get("store", {})
    store_raw = os.environ.get("HCG_STORE_BASE_URL") or store.get("base_url", "")
    if not store_raw.strip():
        raise ConfigError("config/repo.toml [store] base_url is required")
    store_base = normalize_store_url(store_raw)
    acc = cfg.get("access", {})
    endpoint = acc.get("payment_endpoint", "").strip()
    if endpoint and not endpoint.startswith("https://"):
        raise ConfigError("[access] payment_endpoint must be an https:// URL (Sileo refuses insecure providers)")
    return RepoConfig(
        root=root, origin=r["origin"], label=r["label"], suite=r["suite"], codename=r["codename"],
        version=str(r["version"]), description=r["description"], repo_subdir=sub, architectures=archs,
        namespace=r.get("package_namespace", ""), reserved_bundle_ids=list(r.get("reserved_bundle_ids", [])),
        urls=urls, default_target=default_target, signing_required=bool(s.get("required", False)),
        signing_fingerprint=s.get("fingerprint", "").replace(" ", "").upper(),
        public_key_file=s.get("public_key_file", "keys/hcg-repo-archive-key.asc"),
        external_deps=ext, products=parse_products(products), store_base_url=store_base,
        access_require_explicit=bool(acc.get("require_explicit", True)), payment_endpoint=endpoint,
    )


# ---------------------------------------------------------------------------- package metadata

STATUSES = ("experimental", "device-tested")
# public:  in the public index, .deb downloadable without sign-in.
# connect: free, but only for users signed in with HCG Connect.
# paid:    HCG Connect sign-in plus HCG Store ownership.
# connect/paid packages are never stored in this public repository or deployed as public files.
ACCESS_LEVELS = ("public", "connect", "paid")
REQUIRED_TEST_FIELDS = ("device", "ios", "jailbreak", "date", "result", "evidence")
TEST_RESULTS = ("pass", "fail", "partial")
CHANNELS = ("", "beta")


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
    limitations: str = ""
    install: str = ""
    removal: str = ""
    screenshots: list[dict] = field(default_factory=list)  # [{file, caption}]
    access: str = "public"
    access_explicit: bool = False
    access_exception: str = ""   # [access] exception: why an HCG package is public (an explicit, approved exception)
    # [access] exception_versions: the exact versions that approval covers. A new version is NOT public by default;
    # it needs its own approval, so a later paid or premium build can never become public by accident.
    access_exception_versions: list[str] = field(default_factory=list)
    channel: str = ""            # [release] channel: "" or "beta"
    known_issues: str = ""       # [display] known_issues (markdown); required for a beta

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
        limitations=disp.get("limitations", ""), install=disp.get("install", ""), removal=disp.get("removal", ""),
        screenshots=list(disp.get("screenshots", [])),
        access=data.get("access", {}).get("level", "public"), access_explicit="level" in data.get("access", {}),
        access_exception=str(data.get("access", {}).get("exception", "")).strip(),
        access_exception_versions=[str(v) for v in data.get("access", {}).get("exception_versions", [])],
        channel=str(data.get("release", {}).get("channel", "")).strip(), known_issues=disp.get("known_issues", ""),
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
    for asset in (meta.icon, meta.header, *(s.get("file") for s in meta.screenshots)):
        if asset and (meta.directory is None or not (meta.directory / asset).is_file()):
            report.error("meta-missing-asset", f"package.toml references missing file {asset!r}")
        if asset and not re.fullmatch(r"[A-Za-z0-9._-]+\.(png|jpe?g|webp)", asset):
            report.error("meta-bad-asset-name", f"asset {asset!r} must be a plain .png/.jpg/.webp file name")
    for s in meta.screenshots:
        if not s.get("file") or not str(s.get("caption", "")).strip():
            report.error("meta-screenshot", "every screenshot needs file and caption (used as alt text)")
    if meta.access not in ACCESS_LEVELS:
        report.error("meta-bad-access", f"[access] level must be one of {ACCESS_LEVELS}, got {meta.access!r}")
    if meta.channel not in CHANNELS:
        report.error("meta-bad-channel", f"[release] channel must be \"beta\" or absent, got {meta.channel!r}")
    if meta.channel == "beta" and not meta.known_issues.strip():
        report.error("meta-beta-known-issues", "a beta must state its known issues in [display] known_issues "
                     "(say plainly if none have been found yet and whether it has been tested)")
    if meta.access_exception and meta.access != "public":
        report.error("meta-exception-unused", "[access] exception only applies to level = \"public\"")
    if meta.access_exception and not meta.access_exception_versions:
        report.error("meta-exception-scope", "[access] exception needs exception_versions: the exact free versions it approves")
    if meta.access_exception_versions and not meta.access_exception:
        report.error("meta-exception-scope", "[access] exception_versions without an exception text")
    if meta.tint and not re.fullmatch(r"#[0-9A-Fa-f]{6}", meta.tint):
        report.error("meta-bad-tint", f"tint must be #RRGGBB, got {meta.tint!r}")
    for ver, v in meta.versions.items():
        for t in v.get("device_tests", []):
            model = str(t.get("device", "")).split(" (")[0].strip()
            if t.get("result") in ("fail", "partial") and model and model.lower() not in meta.known_issues.lower():
                report.error("meta-known-issues-stale", f"version {ver} has a {t.get('result')} device test on {model}; "
                             "describe it in [display] known_issues, naming that device")
        for t in v.get("device_tests", []):
            if t.get("result") not in TEST_RESULTS:
                report.error("meta-device-test-result", f"version {ver}: device test result must be one of {TEST_RESULTS}")
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

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


# ---------------------------------------------------------------------------- commercial release state
#
# [products.commercial] in config/products.toml: the owner-controlled release settings of a product that HCG Store
# will sell (docs/COMMERCIAL.md). Each setting is independent, closed by default, and every non-default value must
# carry an owner approval record. HCG Repo enforces them when it builds and verifies the site and the index; HCG
# Store enforces purchases and delivery on its own server. Nothing here can grant access to anyone.

COMMERCIAL_DEFAULTS = {
    "listing": "prepared",              # prepared (not rendered publicly) | approved (public product page) | withdrawn
    "purchases": "disabled",            # disabled | enabled
    "downloads": "disabled",            # disabled | enabled (authorised delivery through HCG Store's payment provider)
    "installation": "disabled",         # disabled | enabled (the approved version is indexed in the main source)
    "commercial_release": "not-approved",   # not-approved | approved
    "store_readiness": "pending",       # pending | ready (HCG Store payments, entitlements and licensing are live)
    "approved_version": "",             # the exact Debian version approved for commercial release
}
COMMERCIAL_VALUES = {
    "listing": ("prepared", "approved", "withdrawn"),
    "purchases": ("disabled", "enabled"),
    "downloads": ("disabled", "enabled"),
    "installation": ("disabled", "enabled"),
    "commercial_release": ("not-approved", "approved"),
    "store_readiness": ("pending", "ready"),
}
COMMERCIAL_CHANNELS = ("commercial-prerelease", "commercial-release")
PRICE_RE = re.compile(r"(0|[1-9][0-9]{0,5})\.[0-9]{2}")
CURRENCY_RE = re.compile(r"[A-Z]{3}")
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass
class Commercial:
    """The commercial release settings of one product (docs/COMMERCIAL.md)."""

    release_channel: str
    price: str
    currency: str
    price_note: str = ""
    availability: str = "Coming Soon"
    publisher: str = "Hakmi Capital Group"
    category: str = ""
    source_channel: str = "candidate"
    destination: str = "main"
    # "pending": HCG Store has no product page for it yet, so HCG Repo shows a placeholder and links no Store URL.
    # "live": the Store's catalogue page exists; HCG Repo links it (never a purchase action while purchases are off).
    store_page: str = "pending"
    description: list[str] = field(default_factory=list)
    settings: dict[str, str] = field(default_factory=lambda: dict(COMMERCIAL_DEFAULTS))
    approvals: list[dict] = field(default_factory=list)

    def __getattr__(self, name: str):   # c.listing, c.purchases, … read the settings
        settings = self.__dict__.get("settings", {})
        if name in COMMERCIAL_DEFAULTS:
            return settings.get(name, COMMERCIAL_DEFAULTS[name])
        raise AttributeError(name)

    @property
    def price_text(self) -> str:
        sym = {"USD": "$"}.get(self.currency, "")
        return f"{sym}{self.price} {self.currency}" if sym else f"{self.price} {self.currency}"

    def approval_for(self, setting: str, value: str) -> dict | None:
        return next((a for a in self.approvals if a.get("setting") == setting and str(a.get("value")) == value), None)

    def unapproved(self) -> list[tuple[str, str]]:
        """Settings changed from their closed default without a matching owner approval record."""
        return [(k, v) for k, v in self.settings.items() if v != COMMERCIAL_DEFAULTS[k] and not self.approval_for(k, v)]

    def order_problems(self) -> list[str]:
        """The release workflow's order: listing → HCG Store ready → commercial approval → purchases → delivery."""
        s, out = self.settings, []
        if s["purchases"] == "enabled":
            for k, need in (("listing", "approved"), ("store_readiness", "ready"), ("commercial_release", "approved")):
                if s[k] != need:
                    out.append(f"purchases enabled needs {k} = {need!r} (is {s[k]!r})")
        for gate in ("downloads", "installation"):
            if s[gate] == "enabled":
                for k, need in (("purchases", "enabled"), ("store_readiness", "ready"), ("commercial_release", "approved")):
                    if s[k] != need:
                        out.append(f"{gate} enabled needs {k} = {need!r} (is {s[k]!r})")
        if s["installation"] == "enabled" and s["downloads"] != "enabled":
            out.append("installation enabled needs downloads = 'enabled': an indexed package must be deliverable")
        if s["commercial_release"] == "approved" and not s["approved_version"]:
            out.append("commercial_release approved needs approved_version")
        if s["listing"] == "withdrawn" and any(s[k] == "enabled" for k in ("purchases", "downloads", "installation")):
            out.append("a withdrawn listing cannot have purchases, downloads or installation enabled")
        return out

    @property
    def installable(self) -> bool:
        """Every gate for publishing the approved version in the main index is open."""
        s = self.settings
        return (s["installation"] == "enabled" and s["downloads"] == "enabled" and s["purchases"] == "enabled"
                and s["commercial_release"] == "approved" and s["store_readiness"] == "ready"
                and bool(s["approved_version"]) and not self.unapproved() and not self.order_problems())


def parse_commercial(d: dict, where: str) -> Commercial:
    known = set(COMMERCIAL_DEFAULTS) | {"release_channel", "price", "currency", "price_note", "availability", "publisher",
                                        "category", "source_channel", "destination", "store_page", "description",
                                        "approvals"}
    for k in d:
        if k not in known:
            raise ConfigError(f"{where}: unknown commercial setting {k!r}")
    c = Commercial(
        release_channel=str(d.get("release_channel", "")), price=str(d.get("price", "")), currency=str(d.get("currency", "")),
        price_note=str(d.get("price_note", "")), availability=str(d.get("availability", "Coming Soon")),
        publisher=str(d.get("publisher", "Hakmi Capital Group")), category=str(d.get("category", "")),
        source_channel=str(d.get("source_channel", "candidate")), destination=str(d.get("destination", "main")),
        store_page=str(d.get("store_page", "pending")),
        description=[str(x) for x in d.get("description", [])],
        settings={k: str(d.get(k, v)) for k, v in COMMERCIAL_DEFAULTS.items()},
        approvals=[dict(a) for a in d.get("approvals", [])],
    )
    if c.release_channel not in COMMERCIAL_CHANNELS:
        raise ConfigError(f"{where}: release_channel must be one of {COMMERCIAL_CHANNELS}, got {c.release_channel!r}")
    if not PRICE_RE.fullmatch(c.price) or c.price in ("0.00", "0.0"):
        raise ConfigError(f"{where}: price must be a positive amount with two decimals, e.g. \"1.99\", got {c.price!r}")
    if not CURRENCY_RE.fullmatch(c.currency):
        raise ConfigError(f"{where}: currency must be an ISO 4217 code such as USD, got {c.currency!r}")
    for k, allowed in COMMERCIAL_VALUES.items():
        if c.settings[k] not in allowed:
            raise ConfigError(f"{where}: {k} must be one of {allowed}, got {c.settings[k]!r}")
    if c.store_page not in ("pending", "live"):
        raise ConfigError(f"{where}: store_page must be 'pending' or 'live', got {c.store_page!r}")
    if c.settings["purchases"] == "enabled" and c.store_page != "live":
        raise ConfigError(f"{where}: purchases enabled needs store_page = 'live' (HCG Store sells it on its product page)")
    if c.source_channel != "candidate" or c.destination != "main":
        raise ConfigError(f"{where}: commercial releases move from source_channel 'candidate' to destination 'main'")
    for i, a in enumerate(c.approvals):
        missing = [k for k in ("setting", "value", "approved_by", "date", "record") if not str(a.get(k, "")).strip()]
        if missing:
            raise ConfigError(f"{where}: approval #{i + 1} needs {', '.join(missing)}")
        if a["setting"] not in COMMERCIAL_DEFAULTS:
            raise ConfigError(f"{where}: approval #{i + 1} names unknown setting {a['setting']!r}")
        if not ISO_DATE_RE.fullmatch(str(a["date"])):
            raise ConfigError(f"{where}: approval #{i + 1} date must be YYYY-MM-DD")
    return c


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
    commercial: Commercial | None = None

    def listed(self, preview: bool = False) -> bool:
        """Whether the public site shows this product. A commercial listing is shown only once the owner
        approved it (a preview build also shows prepared listings, marked as previews; it is never deployed)."""
        if self.commercial is None:
            return True
        if self.commercial.listing == "withdrawn":
            return False
        return self.commercial.listing == "approved" and not self.commercial.unapproved() or (
            preview and self.commercial.listing == "prepared")

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
        if "commercial" in d:
            p.commercial = parse_commercial(d["commercial"], f"{where} [commercial]")
            if p.access != "paid":
                raise ConfigError(f"{where}: a commercial product must have access = \"paid\"")
            if not p.package_ids:
                raise ConfigError(f"{where}: a commercial product needs its package_ids")
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
    channel: str = "stable"      # which channel this configuration builds: "stable" (/repo/) or "candidate"
    preview_listings: bool = False   # build --preview-listings: also render prepared commercial listings (never deployed)
    publish_compatibility: bool = False   # [compatibility] publish: write <repo>/compatibility.json (owner decision)

    @property
    def visible_products(self) -> list[Product]:
        """The products the public site shows (docs/COMMERCIAL.md: a prepared commercial listing is not shown)."""
        return [p for p in self.products if p.listed(self.preview_listings)]

    def visible_product_for_package(self, package: str) -> Product | None:
        p = self.product_for_package(package)
        return p if p and p.listed(self.preview_listings) else None

    @property
    def pool_dir(self) -> Path:
        return self.root / "pool"

    @property
    def lock_path(self) -> Path:
        return self.root / "repo-lock.json"

    @property
    def packages_meta_dir(self) -> Path:
        return self.root / "packages"

    def candidate(self) -> "RepoConfig":
        """The candidate (testing) channel: an independent flat APT source at <stable source>/candidate/.

        It indexes only lock entries with channel = "candidate", never protected packages, and has its own
        identity, so Sileo and Zebra treat it as a separate source. The stable index never lists its packages."""
        import dataclasses
        return dataclasses.replace(
            self, channel="candidate", repo_subdir=f"{self.repo_subdir}/{CANDIDATE_DIR}", origin=f"{self.origin} Candidate",
            label=f"{self.label} Candidate", suite="candidate", codename=f"{self.codename}-candidate",
            description=("Testing channel for HCG tweaks, jailbreak apps and command-line tools. Builds here are betas: "
                         "they may fail to install or misbehave. " + self.description),
            payment_endpoint="")

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
        publish_compatibility=bool(cfg.get("compatibility", {}).get("publish", False)),
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
LOCK_CHANNELS = ("stable", "candidate")
CANDIDATE_DIR = "candidate"
TEST_STAGES = ("install", "runtime")


def entry_channel(e: dict) -> str:
    return e.get("channel", "stable")


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

    def access_for(self, version: str) -> str:
        """The access level of one version. A recorded exception makes exactly the versions in
        exception_versions public; every other version keeps the package's level. So a paid package
        (e.g. HCG Nexus) can have one approved free candidate build while its releases stay paid,
        and a public exception package (e.g. HCG Vortex) still refuses unapproved public versions."""
        if self.access_exception and version in self.access_exception_versions:
            return "public"
        return self.access

    def is_free_demo(self, version: str) -> bool:
        """A version of a sign-in or paid package made public by an approved exception: a free public demo.
        Free demos may only be in the candidate channel (docs/ACCESS.md, free-demo policy)."""
        return self.access != "public" and self.access_for(version) == "public"

    def status(self, version: str) -> str:
        return self.versions.get(version, {}).get("status", "experimental")

    def device_tests(self, version: str) -> list[dict]:
        return list(self.versions.get(version, {}).get("device_tests", []))

    def label(self, version: str) -> str:
        """An owner-worded status label for one version, e.g. what a packaging-only rebuild does and does not claim."""
        return str(self.versions.get(version, {}).get("label", "")).strip()


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
            if t.get("stage", "runtime") not in TEST_STAGES:
                report.error("meta-device-test-stage", f"version {ver}: device test stage must be one of {TEST_STAGES}")
            if t.get("result") not in TEST_RESULTS:
                report.error("meta-device-test-result", f"version {ver}: device test result must be one of {TEST_RESULTS}")
        st = v.get("status", "experimental")
        lab = str(v.get("label", ""))
        if lab and (len(lab) > 160 or "\n" in lab):
            report.error("meta-bad-label", f"version {ver}: label must be one line of at most 160 characters")
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

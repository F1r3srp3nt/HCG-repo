"""Verify a published repository exactly the way a client walks it.

`public_base` is the base URL embedded in the metadata (e.g. https://f1r3srp3nt.github.io/HCG-repo/).
`fetch_base` is where to actually fetch from: the same URL for a deployed site, a local HTTP
server (http://127.0.0.1:8000/HCG-repo/) or a directory (file:///.../_site/). Absolute URLs in the
metadata that start with `public_base` are fetched from `fetch_base` instead, which is what lets
nested-path mistakes be caught locally.
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import html
import json
import lzma
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from . import debian
from .config import Report, RepoConfig
from .debinspect import InspectError, inspect_deb

HASHES = (("MD5Sum", "md5"), ("SHA1", "sha1"), ("SHA256", "sha256"), ("SHA512", "sha512"))
DECOMPRESS = {"": lambda b: b, ".gz": gzip.decompress, ".bz2": bz2.decompress, ".xz": lzma.decompress,
              ".lzma": lzma.decompress}
CLIENT_TRIES = ("Packages.zst", "Packages.xz", "Packages.lzma", "Packages.bz2", "Packages.gz", "Packages")
PRESENTATION = ("SileoDepiction", "Depiction", "Icon", "Header")
DEB_LINK = re.compile(r"""(?:href|src)\s*=\s*["']?[^"'\s>]*\.deb(?:[?#"'\s>]|$)""", re.I)
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.I)
NO_DOWNLOADS = "Downloads are not available yet."


@dataclass
class VerifyResult:
    report: Report
    checked_urls: list[str] = field(default_factory=list)
    packages: int = 0
    signed: bool = False
    signature_verified: bool = False

    def summary(self) -> str:
        return (f"{len(self.checked_urls)} URLs fetched, {self.packages} packages, "
                f"signature: {'verified' if self.signature_verified else 'present, NOT verified' if self.signed else 'none'}, "
                f"{len(self.report.errors)} errors, {len(self.report.warnings)} warnings")


class Fetcher:
    def __init__(self, public_base: str, fetch_base: str):
        self.public_base = public_base
        self.fetch_base = fetch_base
        host = urlsplit(fetch_base).hostname or ""
        handlers = []
        if host in ("127.0.0.1", "localhost", "::1") or fetch_base.startswith("file:"):
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)
        self.log: list[str] = []

    def map(self, url: str) -> str:
        if url.startswith(self.public_base):
            return self.fetch_base + url[len(self.public_base):]
        return url

    def get(self, url: str) -> tuple[int, bytes, str]:
        real = self.map(url)
        if real.startswith("file:") and real.endswith("/"):
            real += "index.html"
        self.log.append(real)
        req = urllib.request.Request(real, headers={"User-Agent": "hcgrepo-verify", "Cache-Control": "no-cache"})
        try:
            with self.opener.open(req, timeout=60) as resp:
                return getattr(resp, "status", 200) or 200, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            return exc.code, b"", ""
        except (urllib.error.URLError, FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return 404, b"", ""


def parse_release_hashes(fields: list[tuple[str, str]]) -> dict[str, dict[str, tuple[str, int]]]:
    out: dict[str, dict[str, tuple[str, int]]] = {}
    for title, algo in HASHES:
        raw = debian.field(fields, title)
        if raw is None:
            continue
        table = {}
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) == 3:
                table[parts[2]] = (parts[0].lower(), int(parts[1]))
        out[algo] = table
    return out


def verify(public_base: str, fetch_base: str, cfg: RepoConfig | None = None, pubkey: Path | None = None,
           require_signature: bool = False, check_debs: bool = True) -> VerifyResult:
    from .config import normalize_base_url

    public_base = normalize_base_url(public_base)
    if not fetch_base.endswith("/"):
        fetch_base += "/"
    sub = (cfg.repo_subdir if cfg else "repo") + "/"
    repo_public = public_base + sub
    f = Fetcher(public_base, fetch_base)
    r = Report(repo_public)
    res = VerifyResult(r)

    # ---- Release
    code, release_raw, _ = f.get(repo_public + "Release")
    if code != 200:
        r.error("release-missing", f"Release: HTTP {code} at {f.map(repo_public + 'Release')}")
        res.checked_urls = f.log
        return res
    if looks_like_html(release_raw):
        r.error("release-html", "Release is an HTML page (error/login page?), not APT metadata")
        res.checked_urls = f.log
        return res
    try:
        release = debian.parse_control(release_raw.decode("utf-8"))
    except (debian.ControlError, UnicodeDecodeError) as exc:
        r.error("release-unparseable", f"Release: {exc}")
        res.checked_urls = f.log
        return res
    for name in ("Origin", "Suite", "Architectures", "Components", "Date", "SHA256"):
        if debian.field(release, name) is None:
            r.error("release-field", f"Release lacks {name}")
    archs = (debian.field(release, "Architectures") or "").split(" ")
    if cfg:
        for k, want in (("Origin", cfg.origin), ("Label", cfg.label)):
            if debian.field(release, k) != want:
                r.error("release-identity", f"Release {k} is {debian.field(release, k)!r}, expected {want!r}")
        for a in cfg.architectures:
            if a not in archs:
                r.error("release-arch", f"Release Architectures does not list {a}; Sileo on those devices would reject the source")
    hashes = parse_release_hashes(release)
    sha256 = hashes.get("sha256", {})
    if "Packages" not in sha256:
        r.error("release-no-packages", "Release does not list Packages")

    # ---- index files
    texts: dict[str, bytes] = {}
    for name in CLIENT_TRIES:
        code, data, _ = f.get(repo_public + name)
        if code != 200:
            if name in sha256:
                r.error("index-missing", f"{name} is listed in Release but returns HTTP {code}")
            continue
        if name not in sha256:
            r.error("index-unlisted", f"{name} is served but not listed in Release; Sileo would try it first and reject it")
            continue
        for algo, table in hashes.items():
            want = table.get(name)
            if want is None:
                r.error("index-hash-missing", f"{name} missing from Release {algo} list")
                continue
            if len(data) != want[1] or hashlib.new(algo, data).hexdigest() != want[0]:
                r.error("index-checksum", f"{name}: {algo}/size do not match Release")
        ext = name[len("Packages"):]
        if ext not in DECOMPRESS:
            r.warn("index-not-checked", f"{name}: cannot decompress {ext} locally; checksum checked only")
            continue
        try:
            texts[name] = DECOMPRESS[ext](data)
        except Exception as exc:  # noqa: BLE001
            r.error("index-corrupt", f"{name}: cannot decompress: {exc}")
    for name in sha256:
        if name.startswith("Packages") and name not in CLIENT_TRIES:
            r.warn("index-extra", f"Release lists unexpected index {name}")
    if not texts:
        r.error("index-none", "no readable Packages index")
        res.checked_urls = f.log
        return res
    plain = texts.get("Packages", next(iter(texts.values())))
    for name, text in texts.items():
        if text != plain:
            r.error("index-inconsistent", f"{name} does not decompress to the same content as Packages")

    # ---- signatures
    code_gpg, gpg_data, _ = f.get(repo_public + "Release.gpg")
    code_in, inrel, _ = f.get(repo_public + "InRelease")
    res.signed = code_gpg == 200
    if res.signed:
        if pubkey and pubkey.exists():
            from .sign import verify_signatures
            with tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "Release").write_bytes(release_raw)
                Path(tmp, "Release.gpg").write_bytes(gpg_data)
                Path(tmp, "InRelease").write_bytes(inrel if code_in == 200 else b"")
                ok, detail = verify_signatures(Path(tmp), pubkey)
            res.signature_verified = ok
            if not ok:
                r.error("signature-invalid", f"signature check failed: {detail}")
        else:
            r.warn("signature-unverified", "Release.gpg is present but no public key was given; signature NOT verified")
    elif require_signature:
        r.error("signature-missing", "Release.gpg is missing but a signature is required")
    if code_in == 200 and not res.signed:
        r.error("signature-inrelease-only", "InRelease without Release.gpg: Sileo only reads Release.gpg")

    # ---- packages
    if looks_like_html(plain):
        r.error("packages-html", "Packages is an HTML page, not APT metadata")
    try:
        stanzas = debian.parse_stanzas(plain.decode("utf-8"))
    except (debian.ControlError, UnicodeDecodeError) as exc:
        r.error("packages-unparseable", f"Packages: {exc}")
        stanzas = []
    seen = set()
    protected_seen = False
    for st in stanzas:
        pkg, ver, arch = (debian.field(st, k) for k in ("Package", "Version", "Architecture"))
        label = f"{pkg} {ver} {arch}"
        for k in ("Package", "Version", "Architecture", "Filename", "Size", "SHA256"):
            if not debian.field(st, k):
                r.error("stanza-field", f"{label}: missing {k}")
        if (pkg, ver, arch) in seen:
            r.error("stanza-duplicate", f"{label} appears more than once in Packages")
        seen.add((pkg, ver, arch))
        if arch and arch != "all" and arch not in archs:
            r.error("stanza-arch", f"{label}: architecture not announced in Release")
        fn = debian.field(st, "Filename") or ""
        parts = fn.split("/")
        if "://" in fn or fn.startswith("/") or ".." in parts or fn.startswith("./") or "" in parts:
            r.error("filename-unsafe", f"{label}: Filename {fn!r} must be a clean path relative to the source root")
            continue
        if fn.startswith(sub):
            r.error("filename-double-prefix", f"{label}: Filename {fn!r} repeats the source directory; it would resolve to {repo_public}{fn}")
        url = urljoin(repo_public, fn)
        if not url.startswith(repo_public):
            r.error("filename-escapes", f"{label}: Filename {fn!r} resolves outside the source ({url})")
            continue
        tags = [t.strip() for t in (debian.field(st, "Tag") or "").split(",")]
        if fn.startswith("protected/"):
            # connect/paid: delivered only through the payment provider. The public path must not serve it.
            if "cydia::commercial" not in tags:
                r.error("protected-not-commercial", f"{label}: protected entry lacks Tag cydia::commercial")
            code_p, _, _ = f.get(url)
            if code_p == 200:
                r.error("protected-exposed", f"{label}: protected package is publicly downloadable at {url}")
            protected_seen = True
        elif check_debs:
            _check_deb(f, r, st, url, label)
        for k in PRESENTATION:
            v = debian.field(st, k)
            if v is None:
                continue
            if not v.startswith(public_base):
                r.error("presentation-url-base", f"{label}: {k} {v} is not under the public base {public_base}")
                continue
            code, data, _ = f.get(v)
            if code != 200:
                r.error("presentation-url-broken", f"{label}: {k} {v} returns HTTP {code}")
                continue
            if k == "SileoDepiction":
                _check_sileo_json(r, label, data)
            elif k in ("Icon", "Header") and not (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"):
                r.error("presentation-image", f"{label}: {k} is not a PNG or JPEG")
    res.packages = len(stanzas)

    # ---- payment provider (only when protected packages are indexed)
    code, data, _ = f.get(repo_public + "payment_endpoint")
    if protected_seen:
        endpoint = data.decode("utf-8", "replace").strip() if code == 200 else ""
        if not endpoint.startswith("https://"):
            r.error("payment-endpoint", "protected packages are indexed but payment_endpoint is missing or not https")
    elif code == 200 and not looks_like_html(data):
        r.warn("payment-endpoint-unused", "payment_endpoint is published but no protected package is indexed")

    # ---- repository icons (Sileo tries CydiaIcon@<scale>x.png, then CydiaIcon.png) and featured banners
    for icon in ("CydiaIcon.png", "CydiaIcon@2x.png", "CydiaIcon@3x.png"):
        code, data, _ = f.get(repo_public + icon)
        if code != 200 or data[:8] != b"\x89PNG\r\n\x1a\n":
            r.error("repo-icon", f"{icon} missing or not a PNG (HTTP {code})")
    code, data, _ = f.get(repo_public + "sileo-featured.json")
    if code == 200:
        _check_featured(f, r, data, public_base, {debian.field(st, "Package") for st in stanzas})
    code, data, _ = f.get(public_base)
    if code != 200:
        r.error("site-missing", f"website root returns HTTP {code}")
    elif f"sileo://source/{repo_public}".encode() not in data:
        r.error("site-sileo-link", f"website does not link sileo://source/{repo_public}")
    else:
        for st in stanzas:
            if f"sileo://package/{debian.field(st, 'Package')}".encode() not in data:
                r.error("site-missing-package", f"website does not offer {debian.field(st, 'Package')}")
        for product in cfg.products if cfg else []:
            if f'href="products/{product.key}/"'.encode() not in data:
                r.error("site-product-link", f"website does not link the product page products/{product.key}/")
    if cfg:
        _check_store_pages(f, r, cfg, public_base, {debian.field(st, "Package") for st in stanzas})
    res.checked_urls = f.log
    return res


def _check_store_pages(f: Fetcher, r: Report, cfg: RepoConfig, public_base: str, indexed: set[str]) -> None:
    """Product pages link HCG Store, never link a .deb, and offer installation only for indexed packages."""
    for p in cfg.products:
        url = f"{public_base}products/{p.key}/"
        code, data, _ = f.get(url)
        if code != 200:
            r.error("product-page-missing", f"product page {url} returns HTTP {code}")
            continue
        text = data.decode("utf-8", "replace")
        for what, link in (("sign-in", cfg.store_login_url(p)), ("HCG Store", cfg.store_product_url(p))):
            if f'href="{html.escape(link)}"' not in text:
                r.error("product-page-store-link", f"{url} does not link the {what} page {link}")
        if INLINE_SCRIPT.search(text):
            r.error("product-page-inline-script", f"{url} contains an inline script")
        if DEB_LINK.search(text):
            r.error("product-page-install-link", f"{url} links a .deb; product pages never offer a direct download")
        offered = set(re.findall(r"sileo://package/([^\"'<>\s]+)", text))
        for pkg in sorted(offered - indexed):
            r.error("product-page-install-link", f"{url} offers sileo://package/{pkg}, which is not in the index")
        if not set(p.package_ids) & indexed and NO_DOWNLOADS not in text:
            r.error("product-page-download-notice", f"{url} does not say {NO_DOWNLOADS!r}")


def _check_deb(f: Fetcher, r: Report, st, url: str, label: str) -> None:
    code, data, _ = f.get(url)
    if code != 200:
        r.error("deb-download", f"{label}: {url} returns HTTP {code}")
        return
    size = debian.field(st, "Size")
    if str(len(data)) != (size or "").strip():
        r.error("deb-size", f"{label}: downloaded {len(data)} bytes, index says {size}")
    for name, algo in (("MD5sum", "md5"), ("SHA1", "sha1"), ("SHA256", "sha256"), ("SHA512", "sha512")):
        want = debian.field(st, name)
        if want and hashlib.new(algo, data).hexdigest() != want.lower():
            r.error("deb-checksum", f"{label}: {name} mismatch for {url}")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pkg.deb"
        p.write_bytes(data)
        try:
            info = inspect_deb(p)
        except InspectError as exc:
            r.error("deb-invalid", f"{label}: downloaded file is not a valid package: {exc}")
            return
    for n, v in info.control:
        if n in PRESENTATION:
            continue  # the repository supplies these (see repo.PRESENTATION_FIELDS)
        if debian.field(st, n) != v:
            r.error("stanza-control-mismatch", f"{label}: index field {n} differs from the package's control file")


def looks_like_html(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith((b"<!doctype", b"<html", b"<head", b"<body", b"<?xml"))


def _check_featured(f: "Fetcher", r: Report, data: bytes, public_base: str, indexed: set) -> None:
    try:
        d = json.loads(data)
    except ValueError as exc:
        r.error("featured-json", f"sileo-featured.json is not JSON: {exc}")
        return
    if d.get("class") != "FeaturedBannersView" or not isinstance(d.get("banners"), list) or not d["banners"]:
        r.error("featured-shape", "sileo-featured.json must be a FeaturedBannersView with banners")
        return
    if not isinstance(d.get("itemSize"), str) or not isinstance(d.get("itemCornerRadius"), (int, float)):
        r.error("featured-shape", "sileo-featured.json needs itemSize (string) and itemCornerRadius (number)")
    for b in d["banners"]:
        if not b.get("url") or not b.get("title"):
            r.error("featured-banner", "every banner needs url and title (Sileo drops the view otherwise)")
            continue
        if not b["url"].startswith(public_base):
            r.error("featured-url-base", f"banner image {b['url']} is not under {public_base}")
            continue
        code, img, _ = f.get(b["url"])
        if code != 200 or not (img[:8] == b"\x89PNG\r\n\x1a\n" or img[:3] == b"\xff\xd8\xff" or img[8:12] == b"WEBP"):
            r.error("featured-image", f"banner image {b['url']} missing or not an image (HTTP {code})")
        if b.get("package") and b["package"] not in indexed:
            r.error("featured-package", f"banner links package {b['package']}, which is not in the index")


def _check_sileo_json(r: Report, label: str, data: bytes) -> None:
    try:
        d = json.loads(data)
    except ValueError as exc:
        r.error("sileo-depiction-json", f"{label}: SileoDepiction is not JSON: {exc}")
        return
    if d.get("class") != "DepictionTabView" or not isinstance(d.get("tabs"), list) or not d["tabs"]:
        r.error("sileo-depiction-shape", f"{label}: SileoDepiction must be a DepictionTabView with tabs")
        return
    for t in d["tabs"]:
        if not t.get("tabname") or not t.get("class"):
            r.error("sileo-depiction-tab", f"{label}: every tab needs tabname and class (Sileo drops the whole view otherwise)")

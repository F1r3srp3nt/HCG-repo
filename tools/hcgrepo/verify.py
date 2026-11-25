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
    protected_indexed: bool = False
    exchanges: list = field(default_factory=list)

    def summary(self) -> str:
        return (f"{len(self.checked_urls)} URLs fetched, {self.packages} packages, "
                f"signature: {'verified' if self.signature_verified else 'present, NOT verified' if self.signed else 'none'}, "
                f"{len(self.report.errors)} errors, {len(self.report.warnings)} warnings")


# Sileo sends "<app>/<version> CoreFoundation/<cf> Darwin/<kernel>" (URLManager.urlRequest). The verifier
# sends the same shape so a server or CDN that treats package managers differently is tested as Sileo
# sees it. It sends no device identifiers (X-Unique-ID, X-Machine, X-Firmware): GitHub Pages ignores
# them, and real identifiers must never be invented or leaked.
SILEO_USER_AGENT = "Sileo/2.5 CoreFoundation/1953.100 Darwin/22.5.0 (hcgrepo-verify)"
MAX_REDIRECTS = 5


@dataclass
class Exchange:
    """One request as a client saw it: every redirect hop, the final status, headers and body digest."""
    url: str
    status: int
    final_url: str
    content_type: str
    redirects: list[tuple[int, str]]
    headers: dict[str, str]
    size: int
    sha256: str
    error: str = ""
    body_file: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib hook
        return None


class Fetcher:
    def __init__(self, public_base: str, fetch_base: str, evidence: Path | None = None):
        self.public_base = public_base
        self.fetch_base = fetch_base
        host = urlsplit(fetch_base).hostname or ""
        handlers: list = [_NoRedirect()]
        if host in ("127.0.0.1", "localhost", "::1") or fetch_base.startswith("file:"):
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)
        self.log: list[str] = []
        self.exchanges: list[Exchange] = []
        self.evidence = evidence
        if evidence:
            evidence.mkdir(parents=True, exist_ok=True)

    def map(self, url: str) -> str:
        if url.startswith(self.public_base):
            return self.fetch_base + url[len(self.public_base):]
        return url

    def _once(self, url: str, method: str, body: bytes | None) -> tuple[int, bytes, dict[str, str], str]:
        headers = {"User-Agent": SILEO_USER_AGENT, "Cache-Control": "no-cache"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=60) as resp:
                return getattr(resp, "status", 200) or 200, resp.read(), dict(resp.headers.items()), ""
        except urllib.error.HTTPError as exc:
            # Keep error bodies: Sileo reads some responses (payment_endpoint) whatever the status.
            try:
                data = exc.read()
            except Exception:  # noqa: BLE001
                data = b""
            return exc.code, data, dict(exc.headers.items()) if exc.headers else {}, ""
        except (urllib.error.URLError, FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError) as exc:
            return 0 if not url.startswith("file:") else 404, b"", {}, str(getattr(exc, "reason", exc))

    def request(self, url: str, method: str = "GET", body: bytes | None = None) -> tuple[Exchange, bytes]:
        real = self.map(url)
        if real.startswith("file:") and real.endswith("/"):
            real += "index.html"
        self.log.append(real if method == "GET" else f"{method} {real}")
        hops: list[tuple[int, str]] = []
        current, seen = real, {real}
        while True:
            code, data, headers, err = self._once(current, method, body)
            location = next((v for k, v in headers.items() if k.lower() == "location"), "")
            if code in (301, 302, 303, 307, 308) and location:
                nxt = urljoin(current, location)
                hops.append((code, nxt))
                if nxt in seen or len(hops) > MAX_REDIRECTS:
                    err = "redirect loop" if nxt in seen else f"more than {MAX_REDIRECTS} redirects"
                    break
                seen.add(nxt)
                current = nxt
                if code == 303:
                    method, body = "GET", None
                continue
            break
        ctype = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
        keep = {k: v for k, v in headers.items() if k.lower() in (
            "content-type", "content-length", "location", "server", "cache-control", "etag", "last-modified",
            "age", "x-cache", "via", "cf-ray", "access-control-allow-origin", "strict-transport-security")}
        ex = Exchange(real, code, current, ctype, hops, keep, len(data), hashlib.sha256(data).hexdigest(), err)
        if self.evidence is not None:
            tail = "/".join(urlsplit(real).path.strip("/").split("/")[-2:]) or "root"
            name = re.sub(r"[^A-Za-z0-9._@-]+", "_", tail)[:80]
            ex.body_file = f"{len(self.exchanges):03d}-{method}-{name}"
            (self.evidence / ex.body_file).write_bytes(data)
        self.exchanges.append(ex)
        return ex, data

    def get(self, url: str) -> tuple[int, bytes, str]:
        ex, data = self.request(url)
        if ex.error and ex.status in (301, 302, 303, 307, 308):
            return 0, b"", ""
        return ex.status, data, ex.content_type

    def write_evidence(self) -> None:
        if self.evidence is None:
            return
        rows = [ex.__dict__ for ex in self.exchanges]
        (self.evidence / "responses.json").write_text(json.dumps(rows, indent=2) + "\n")


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
           require_signature: bool = False, check_debs: bool = True, evidence: Path | None = None,
           check_provider: bool = False, site_checks: bool = True, icons: bool = True,
           allow_preview: bool = False) -> VerifyResult:
    """Walk the repository as Sileo does. With `evidence`, every response body and a responses.json
    (status, redirect hops, headers, size, SHA-256) are saved there, so a report can be checked later."""
    from .config import normalize_base_url

    public_base = normalize_base_url(public_base)
    if not fetch_base.endswith("/"):
        fetch_base += "/"
    sub = (cfg.repo_subdir if cfg else "repo") + "/"
    repo_public = public_base + sub
    f = Fetcher(public_base, fetch_base, evidence)
    r = Report(repo_public)
    res = VerifyResult(r)
    _walk(f, r, res, public_base, repo_public, sub, cfg, pubkey, require_signature, check_debs, site_checks, icons)
    if site_checks and cfg:
        _check_commercial(f, r, cfg, public_base, allow_preview)
    _check_payment_endpoint(f, r, cfg, repo_public, res.protected_indexed, check_provider)
    _check_transport(f, r, repo_public)
    res.checked_urls = f.log
    res.exchanges = f.exchanges
    f.write_evidence()
    return res


def _walk(f: "Fetcher", r: Report, res: "VerifyResult", public_base: str, repo_public: str, sub: str,
          cfg: RepoConfig | None, pubkey: Path | None, require_signature: bool, check_debs: bool,
          site_checks: bool = True, icons: bool = True) -> None:

    # ---- Release
    code, release_raw, _ = f.get(repo_public + "Release")
    if code != 200:
        r.error("release-missing", f"Release: HTTP {code} at {f.map(repo_public + 'Release')}")
        return
    if looks_like_html(release_raw):
        r.error("release-html", "Release is an HTML page (error/login page?), not APT metadata")
        return
    try:
        release = debian.parse_control(release_raw.decode("utf-8"))
    except (debian.ControlError, UnicodeDecodeError) as exc:
        r.error("release-unparseable", f"Release: {exc}")
        return
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
        return
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
                ok, detail = verify_signatures(Path(tmp), pubkey, cfg.signing_fingerprint if cfg else "")
            res.signature_verified = ok
            if not ok:
                r.error("signature-invalid", f"signature check failed: {detail}")
        else:
            r.warn("signature-unverified", "Release.gpg is present but no public key was given; signature NOT verified")
    elif require_signature or (cfg is not None and cfg.signing_fingerprint):
        # Fail closed: once production signing is configured, an unsigned source is never accepted.
        r.error("signature-missing", "Release.gpg is missing but a signature is required"
                + (" (config/repo.toml [signing] fingerprint is set)" if cfg is not None and cfg.signing_fingerprint else ""))
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
        if cfg and cfg.channel == "stable":
            product = cfg.product_for_package(pkg)
            c = product.commercial if product else None
            if c is not None and not (c.installable and ver == c.approved_version):
                r.error("commercial-indexed", f"{label}: {product.key} is a commercial product without an approved, "
                        "activated release for this version; the main index must not list it (docs/COMMERCIAL.md)")
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
            elif k == "Depiction":
                _check_web_depiction_assets(f, r, label, v, data)
            elif k in ("Icon", "Header") and not (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"):
                r.error("presentation-image", f"{label}: {k} is not a PNG or JPEG")
    res.packages = len(stanzas)

    res.protected_indexed = protected_seen

    # ---- repository icons. Sileo (RepoManager._checkUpdatesInBackground) tries
    # <source>/CydiaIcon@<screen scale>x.png, then lower scales, then CydiaIcon.png, and stops at the
    # first download that returns any data, so every variant must be a real PNG of the right size.
    for icon, side in (REPO_ICONS.items() if icons else ()):
        code, data, _ = f.get(repo_public + icon)
        problem = icon_problem(data, side) if code == 200 else f"HTTP {code}"
        if problem:
            r.error("repo-icon", f"{icon}: {problem}")
        elif cfg is not None:
            approved = cfg.root / "branding" / icon
            if approved.is_file() and approved.read_bytes() != data:
                r.error("repo-icon-mismatch", f"{icon} is not the approved icon in branding/{icon}")
    code, data, _ = f.get(repo_public + "sileo-featured.json")
    if code == 200:
        _check_featured(f, r, data, public_base, {debian.field(st, "Package") for st in stanzas})
    if not site_checks:
        return
    code, data, _ = f.get(public_base)
    if code != 200:
        r.error("site-missing", f"website root returns HTTP {code}")
    elif f"sileo://source/{repo_public}".encode() not in data:
        r.error("site-sileo-link", f"website does not link sileo://source/{repo_public}")
    else:
        for st in stanzas:
            if f"sileo://package/{debian.field(st, 'Package')}".encode() not in data:
                r.error("site-missing-package", f"website does not offer {debian.field(st, 'Package')}")
        for product in cfg.visible_products if cfg else []:
            if f'href="products/{product.key}/"'.encode() not in data:
                r.error("site-product-link", f"website does not link the product page products/{product.key}/")
    if cfg:
        cand = set()
        if cfg.channel == "stable":
            from .config import CANDIDATE_DIR
            code, data, _ = f.get(repo_public + CANDIDATE_DIR + "/Packages")
            if code == 200 and not looks_like_html(data):
                cand = {debian.field(st, "Package") for st in debian.parse_stanzas(data.decode("utf-8", "replace"))}
        _check_store_pages(f, r, cfg, public_base, {debian.field(st, "Package") for st in stanzas}, cand)


def _check_store_pages(f: Fetcher, r: Report, cfg: RepoConfig, public_base: str, indexed: set[str],
                       candidate_indexed: set[str] | None = None) -> None:
    """Product pages link HCG Store, never link a .deb, and offer installation only for indexed packages."""
    for p in cfg.visible_products:
        url = f"{public_base}products/{p.key}/"
        code, data, _ = f.get(url)
        if code != 200:
            r.error("product-page-missing", f"product page {url} returns HTTP {code}")
            continue
        text = data.decode("utf-8", "replace")
        store_links = (("sign-in", cfg.store_login_url(p)), ("HCG Store", cfg.store_product_url(p)))
        if p.commercial is not None and p.commercial.purchases != "enabled":
            store_links = store_links[1:]   # no sign-in or purchase path while purchases are disabled (_check_commercial)
            if p.commercial.store_page != "live":
                store_links = ()            # no Store page exists yet; _check_commercial refuses any Store link
        for what, link in (store_links if p.in_store else ()):
            if f'href="{html.escape(link)}"' not in text:
                r.error("product-page-store-link", f"{url} does not link the {what} page {link}")
        if not p.in_store and cfg.store_base_url and cfg.store_base_url in text:
            r.error("product-page-store-link", f"{url} links HCG Store, but {p.key} is not in HCG Store")
        if INLINE_SCRIPT.search(text):
            r.error("product-page-inline-script", f"{url} contains an inline script")
        if DEB_LINK.search(text):
            r.error("product-page-install-link", f"{url} links a .deb; product pages never offer a direct download")
        offered = set(re.findall(r"sileo://package/([^\"'<>\s]+)", text))
        for pkg in sorted(offered - indexed):
            r.error("product-page-install-link", f"{url} offers sileo://package/{pkg}, which is not in the index")
        # A commercial product's page never points to a candidate build (_check_commercial checks it).
        in_testing = set(p.package_ids) & (candidate_indexed or set()) if p.commercial is None else set()
        if not set(p.package_ids) & indexed and not in_testing and NO_DOWNLOADS not in text:
            r.error("product-page-download-notice", f"{url} does not say {NO_DOWNLOADS!r}")
        if in_testing and not set(p.package_ids) & indexed and "HCG Repo Candidate" not in text:
            r.error("product-page-candidate", f"{url} does not point to the testing channel where {sorted(in_testing)} is")


def _check_commercial(f: "Fetcher", r: Report, cfg: RepoConfig, public_base: str, allow_preview: bool) -> None:
    """docs/COMMERCIAL.md: a prepared listing is not published; a published one shows its price and availability,
    offers no purchase while purchases are disabled, no installation unless activated, and never the free
    candidate build. A preview build (prepared listings rendered for review) is never accepted as deployable."""
    from .site import COMMERCIAL_NOT_READY, PREVIEW_MARKER
    if not allow_preview:
        code, _, _ = f.get(public_base + PREVIEW_MARKER)
        if code == 200:
            r.error("preview-build", f"{PREVIEW_MARKER} is served: this is a preview build of prepared listings and "
                    "must never be deployed")
    code, home, _ = f.get(public_base)
    home_text = home.decode("utf-8", "replace") if code == 200 else ""
    for p in cfg.products:
        c = p.commercial
        if c is None:
            continue
        url = f"{public_base}products/{p.key}/"
        code, data, _ = f.get(url)
        if not p.listed(cfg.preview_listings):
            if code == 200:
                r.error("commercial-listing-exposed", f"{url} is served, but the {p.key} listing is {c.listing!r}, "
                        "not approved by the owner")
            if f'data-commercial="{p.key}"' in home_text or f'href="products/{p.key}/"' in home_text:
                r.error("commercial-listing-exposed", f"the home page lists {p.key}, whose listing is not approved")
            continue
        if code != 200:
            r.error("product-page-missing", f"product page {url} returns HTTP {code}")
            continue
        text = data.decode("utf-8", "replace")
        for what, needle in (("price", html.escape(c.price_text)), ("availability", html.escape(c.availability))):
            if needle not in text:
                r.error("commercial-page-content", f"{url} does not show the {what} {needle!r}")
        if "data-preview" in text and not allow_preview:
            r.error("preview-build", f"{url} is a preview page; prepared listings must never be deployed")
        if "/candidate/" in text or "sileo://source/" in text:
            r.error("commercial-candidate-link", f"{url} points to the candidate (testing) source; a commercial product "
                    "page must not offer the free demo build")
        if c.store_page != "live" and cfg.store_base_url and cfg.store_base_url in text:
            r.error("commercial-store-link", f"{url} links HCG Store, but store_page is {c.store_page!r}: no Store page "
                    "exists for this product yet")
        if c.purchases != "enabled":
            if "store-access.js" in text or "data-store-access" in text:
                r.error("commercial-purchase-enabled", f"{url} runs the HCG Store purchase check while purchases are disabled")
            if 'data-purchases="disabled"' not in text or "disabled" not in text:
                r.error("commercial-purchase-enabled", f"{url} lacks the disabled purchase control")
        if not c.installable:
            if "sileo://package/" in text:
                r.error("commercial-install-link", f"{url} offers installation, but the commercial release is not activated")
            if NO_DOWNLOADS not in text:
                r.error("commercial-page-content", f"{url} does not say that downloads are not available")
        if c.purchases != "enabled" and COMMERCIAL_NOT_READY not in text:
            r.error("commercial-page-content", f"{url} does not say that purchasing is not available yet")


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


REPO_ICONS = {"CydiaIcon.png": 60, "CydiaIcon@2x.png": 120, "CydiaIcon@3x.png": 180}


def png_size(data: bytes) -> tuple[int, int] | None:
    """Width and height from a PNG's IHDR chunk, or None if this is not a PNG."""
    import struct
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR" or len(data) < 24:
        return None
    return struct.unpack(">II", data[16:24])


def icon_problem(data: bytes, side: int) -> str | None:
    if looks_like_html(data):
        return "is an HTML page, not an image"
    size = png_size(data)
    if size is None:
        return "is not a PNG"
    if size[0] != size[1]:
        return f"is not square ({size[0]}x{size[1]})"
    if size[0] != side:
        return f"is {size[0]}x{size[1]}, expected {side}x{side} for its scale"
    return None


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


STYLESHEET = re.compile(r"""<link[^>]+rel=["']stylesheet["'][^>]*href=["']([^"']+)["']""", re.I)


def _check_web_depiction_assets(f: "Fetcher", r: Report, label: str, url: str, data: bytes) -> None:
    """Zebra (and Sileo for packages without SileoDepiction) shows the web depiction. Its stylesheet must load from
    where the page actually is, or the page renders as unstyled browser-default text."""
    sheets = STYLESHEET.findall(data.decode("utf-8", "replace"))
    if not sheets:
        r.error("depiction-stylesheet", f"{label}: web depiction {url} links no stylesheet")
    for href in sheets:
        target = urljoin(url, html.unescape(href))
        code, css, ctype = f.get(target)
        if code != 200 or looks_like_html(css) or b"{" not in css:
            r.error("depiction-stylesheet", f"{label}: web depiction stylesheet {target} does not load (HTTP {code}); "
                    "the page would render unstyled")


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
    _check_sileo_view(r, label, d, "root")


# Required keys and types of each depiction view, from each class's init?(dictionary:) guards in Sileo 2.5.1
# (Sileo/UI/PackageViewController/DepictionView). A missing key makes that view return nil; a stack or tab
# child without a "class" string makes Sileo drop the WHOLE stack.
SILEO_VIEWS = {
    "DepictionTabView": {"tabs": list},
    "DepictionStackView": {"views": list},
    "DepictionMarkdownView": {"markdown": str},
    "DepictionSeparatorView": {},
    "DepictionHeaderView": {"title": str},
    "DepictionSubheaderView": {"title": str},
    "DepictionTableTextView": {"title": str, "text": str},
    "DepictionTableButtonView": {"title": str, "action": str},
    "DepictionScreenshotsView": {"itemSize": str, "screenshots": list},
}


def _check_sileo_view(r: Report, label: str, d, where: str) -> None:
    if not isinstance(d, dict) or not isinstance(d.get("class"), str):
        r.error("sileo-depiction-class", f"{label}: {where} has no class string; Sileo drops the enclosing stack")
        return
    cls = d["class"]
    need = SILEO_VIEWS.get(cls)
    if need is None:
        r.error("sileo-depiction-class", f"{label}: {where} uses {cls!r}, which this repository does not check against Sileo")
        return
    for key, typ in need.items():
        if not isinstance(d.get(key), typ):
            r.error("sileo-depiction-field", f"{label}: {where} ({cls}) needs {key} as {typ.__name__}; Sileo would draw nothing")
    for i, child in enumerate(d.get("tabs") or d.get("views") or []):
        _check_sileo_view(r, label, child, f"{where}/{cls}[{i}]")


def sileo_payment_endpoint(body: bytes) -> str | None:
    """What Sileo concludes from <source>/payment_endpoint (PaymentManager.getPaymentProvider).

    Sileo ignores the HTTP status: it reads whatever body came back, trims whitespace, appends "/"
    and accepts the result when it parses as an https URL. So a 404 page that happened to be a bare
    https URL would register a payment provider. Returns the endpoint Sileo would use, or None."""
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text.endswith("/"):
        text += "/"
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.netloc or any(c.isspace() for c in text) or "<" in text:
        return None
    return text


def _check_payment_endpoint(f: "Fetcher", r: Report, cfg: RepoConfig | None, repo_public: str, protected_indexed: bool,
                            check_provider: bool = False) -> None:
    code, data, _ = f.get(repo_public + "payment_endpoint")
    seen = sileo_payment_endpoint(data)
    configured = (cfg.payment_endpoint if cfg else "") or ""
    want = (configured.rstrip("/") + "/") if configured else None
    if seen != want:
        if want is None:
            r.error("payment-endpoint-unexpected",
                    f"Sileo would register payment provider {seen} from payment_endpoint (HTTP {code}), but none is configured")
        elif seen is None:
            r.error("payment-endpoint", f"payment_endpoint is configured as {want} but Sileo would not accept what is served (HTTP {code})")
        else:
            r.error("payment-endpoint", f"Sileo would use {seen}, but {want} is configured")
    if protected_indexed and want is None:
        r.error("payment-endpoint", "protected packages are indexed but no payment_endpoint is configured; Sileo could never download them")
    if not protected_indexed and want is not None:
        r.warn("payment-endpoint-unused", "payment_endpoint is published but no protected package is indexed")
    if want is None or seen != want or not check_provider:
        return
    # Sileo's PaymentProvider.fetchInfo: GET <endpoint>info, JSON object with string name and description.
    code, data, _ = f.get(want + "info")
    try:
        info = json.loads(data) if data else None
    except ValueError:
        info = None
    if not isinstance(info, dict) or not isinstance(info.get("name"), str) or not isinstance(info.get("description"), str):
        r.error("payment-info", f"{want}info (HTTP {code}) is not the JSON object Sileo needs (string name and description)")
        return
    if info.get("success") is False or isinstance(info.get("error"), str):
        r.error("payment-info", f"{want}info reports an error: {info.get('error')}")
    icon = info.get("icon")
    if icon is not None and not (isinstance(icon, str) and icon.startswith("https://")):
        r.error("payment-info", f"{want}info icon must be an https URL")
    banner = info.get("authentication_banner")
    if banner is not None and not (isinstance(banner, dict) and all(isinstance(v, str) for v in banner.values())):
        r.error("payment-info", "authentication_banner must map strings to strings (Sileo casts it to [String: String])")


# Files Sileo fetches while refreshing a source and drawing it in Sources. A redirect costs a hop and,
# if it leaves https, exposes the request; text/html means an error or login page answered instead.
_SILEO_FILES = ("Release", "Release.gpg", "Packages", "Packages.xz", "Packages.lzma", "Packages.bz2", "Packages.gz",
                "Packages.zst", "CydiaIcon.png", "CydiaIcon@2x.png", "CydiaIcon@3x.png", "sileo-featured.json",
                "payment_endpoint")


def _check_transport(f: "Fetcher", r: Report, repo_public: str) -> None:
    for ex in f.exchanges:
        name = ex.url[len(f.map(repo_public)):] if ex.url.startswith(f.map(repo_public)) else None
        if ex.error and ex.redirects:
            r.error("redirect-loop", f"{ex.url}: {ex.error} ({' -> '.join(u for _, u in ex.redirects)})")
        for _, hop in ex.redirects:
            if ex.url.startswith("https://") and hop.startswith("http://"):
                r.error("redirect-insecure", f"{ex.url} redirects to plain http ({hop})")
        if name is None or name not in _SILEO_FILES:
            continue
        if ex.redirects and ex.status == 200:
            r.warn("redirect", f"{name} is redirected ({len(ex.redirects)} hop(s) to {ex.final_url}); serve it directly")
        if ex.status == 200 and ex.content_type.split(";")[0].strip().lower() == "text/html":
            r.error("content-type-html", f"{name} is served as text/html; Sileo expects APT data or an image")
        if ex.status == 0:
            r.error("unreachable", f"{name}: {ex.error or 'no response'}")

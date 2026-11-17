"""Isolated diagnostic sources for a Sileo crash investigation (docs/SILEO_CRASH.md).

Each variant is a separate flat APT source under <site>/diag/<variant>/, built with the same functions
as the production source, that adds ONE component to a minimal source, so a device test can tell which
component makes Sileo fail. Production metadata, package IDs and /repo/ are not changed.

The variants are only written when the build is asked for them (`hcgrepo build --diagnostics`), and the
Publish workflow only deploys them when its `diagnostics` input is set. They are not linked from the
website, carry noindex, and their names say "Diagnostic" so they cannot be mistaken for HCG Repo.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

from . import debian
from .config import CANDIDATE_DIR, Report, RepoConfig
from .repo import (build_info_date, compress_indexes, presentation_fields, protected_packages, protected_stanza,
                   render_release)

PRESENTATION_KEYS = ("Depiction", "SileoDepiction", "Icon", "Header")
ICONS = ("CydiaIcon.png", "CydiaIcon@2x.png", "CydiaIcon@3x.png")
DIAG_PACKAGE = "com.hakmicapitalgroup.hcgnexus"   # real metadata of a real package; its file is never served


@dataclasses.dataclass(frozen=True)
class Variant:
    key: str
    title: str
    adds: str
    components: frozenset


VARIANTS = (
    Variant("d1-minimal", "Diagnostic 1 - minimal", "Release and an empty Packages index (plain, .xz, .gz, .bz2) only.", frozenset()),
    Variant("d2-icon", "Diagnostic 2 - icon", "Minimal source plus the source icon (CydiaIcon.png, @2x, @3x).", frozenset({"icons"})),
    Variant("d3-featured", "Diagnostic 3 - banner", "Minimal source plus sileo-featured.json and the HR banner.", frozenset({"featured"})),
    Variant("d4-package", "Diagnostic 4 - one package",
            "Minimal source plus one real package entry (HCG Nexus metadata, cydia::commercial, file not served), no depiction.",
            frozenset({"package"})),
    Variant("d5-depiction", "Diagnostic 5 - depiction",
            "Diagnostic 4 plus the package's Sileo depiction, web depiction, icon and header.", frozenset({"package", "depiction"})),
    Variant("d6-payment", "Diagnostic 6 - payment provider",
            "Diagnostic 4 plus payment_endpoint and a static provider that answers only `info`.", frozenset({"package", "payment"})),
)
CONTROL = Variant("d7-production-copy", "HCG Repo", "The production source's files, byte for byte, at another URL.", frozenset({"production"}))
ALL = VARIANTS + (CONTROL,)


def variant_cfg(cfg: RepoConfig, v: Variant, base_url: str) -> RepoConfig:
    if v is CONTROL:
        return dataclasses.replace(cfg, repo_subdir=f"diag/{v.key}")
    endpoint = f"{base_url}diag/{v.key}/provider/" if "payment" in v.components else ""
    return dataclasses.replace(cfg, origin=f"HCG Repo {v.title}", label=f"HCG Repo {v.title}", repo_subdir=f"diag/{v.key}",
                               description=f"HCG Repo diagnostic source, not for installing packages. {v.adds}",
                               payment_endpoint=endpoint)


def _package(cfg: RepoConfig, package: str = DIAG_PACKAGE):
    p = next((p for p in protected_packages(cfg) if p.info.package == package), None)
    if p is None:
        raise ValueError(f"{package} is not recorded in repo-lock.json; diagnostic package variants need it")
    return p


def build_variant(cfg: RepoConfig, base_url: str, site: Path, v: Variant, package: str = DIAG_PACKAGE) -> Path:
    vcfg = variant_cfg(cfg, v, base_url)
    out = site / "diag" / v.key
    out.mkdir(parents=True)
    repo_url = f"{base_url}diag/{v.key}/"
    if v is CONTROL:
        out.rmdir()
        # Every file of the stable source (debs/ and depictions/ come along when present), but not the candidate
        # source nested inside it: the control is the stable source only.
        shutil.copytree(site / cfg.repo_subdir, out, ignore=shutil.ignore_patterns(CANDIDATE_DIR))
        return out

    text = ""
    if "package" in v.components:
        p = _package(cfg, package)
        fields = protected_stanza(vcfg, p, repo_url)
        if "depiction" not in v.components:
            fields = [(k, val) for k, val in fields if k not in PRESENTATION_KEYS]
        text = debian.format_stanza(fields)
        if "depiction" in v.components:
            from .site import sileo_depiction, web_depiction
            ddir = out / "depictions" / p.info.package
            ddir.mkdir(parents=True)
            (ddir / "index.html").write_text(web_depiction(vcfg, repo_url, [p], [p]), encoding="utf-8")
            (ddir / "sileo.json").write_text(json.dumps(sileo_depiction(vcfg, repo_url, [p], [p]), indent=2) + "\n", encoding="utf-8")
            meta = p.meta
            if meta.directory:
                dst = out / "assets" / p.info.package
                for a in (meta.icon, meta.header, *(s["file"] for s in meta.screenshots)):
                    if a:
                        dst.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(meta.directory / a, dst / a)
            assert {k for k, _ in presentation_fields(vcfg, p.meta, repo_url)} <= {k for k, _ in fields}
    indexes = compress_indexes(text)
    for name, data in indexes.items():
        (out / name).write_bytes(data)
    (out / "Release").write_text(render_release(vcfg, indexes, build_info_date()), encoding="utf-8")
    if "icons" in v.components:
        for icon in ICONS:
            shutil.copyfile(cfg.root / "branding" / icon, out / icon)
    if "featured" in v.components:
        (out / "assets").mkdir(exist_ok=True)
        shutil.copyfile(cfg.root / "branding" / "hcg-repo-banner-1200.jpg", out / "assets" / "hcg-repo-banner.jpg")
        featured = {"class": "FeaturedBannersView", "itemSize": "{330, 110}", "itemCornerRadius": 10,
                    "banners": [{"url": f"{repo_url}assets/hcg-repo-banner.jpg", "title": "HCG Repo",
                                 "displayText": False, "hideShadow": True}]}
        (out / "sileo-featured.json").write_text(json.dumps(featured, indent=2) + "\n", encoding="utf-8")
    if "payment" in v.components:
        (out / "payment_endpoint").write_text(vcfg.payment_endpoint + "\n", encoding="utf-8")
        (out / "provider").mkdir()
        # Static: GET info works; every POST (sign-in result, user_info, package info, downloads) gets
        # GitHub Pages' 405/404 HTML, which Sileo must treat as a failed request. No sign-in banner is
        # offered, so the device never sends its identifier to the static host.
        (out / "provider" / "info").write_text(json.dumps({
            "name": "HCG Store (diagnostic)",
            "description": "Diagnostic payment provider. It answers only this info request; sign-in and downloads are unavailable.",
        }, indent=2) + "\n", encoding="utf-8")
    return out


def build_all(cfg: RepoConfig, base_url: str, site: Path, package: str = DIAG_PACKAGE) -> list[Path]:
    """Write every variant plus diag/index.html (noindex) into an already built site."""
    paths = [build_variant(cfg, base_url, site, v, package) for v in ALL]
    rows = "".join(
        f'<tr><td><code>{v.key}</code></td><td>{v.adds}</td><td><a href="sileo://source/{base_url}diag/{v.key}/">Add to Sileo</a>'
        f'<br><code>{base_url}diag/{v.key}/</code></td></tr>' for v in ALL)
    (site / "diag" / "index.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow"><title>HCG Repo diagnostics</title></head><body>'
        "<h1>HCG Repo diagnostic sources</h1><p>For a Sileo crash investigation only (docs/SILEO_CRASH.md in the HCG Repo "
        "repository). These sources are not for installing anything. Add one at a time, record the result, then remove it.</p>"
        f"<table><tr><th>Variant</th><th>Adds</th><th>Source</th></tr>{rows}</table></body></html>\n", encoding="utf-8")
    return paths


# Findings each variant produces ON PURPOSE (it lacks or adds exactly one thing). Anything else is a defect.
EXPECTED = {
    "d4-package": {"payment-endpoint"},     # a commercial entry without a provider: what d4 isolates
    "d5-depiction": {"payment-endpoint"},
}


def verify_all(cfg: RepoConfig, base_url: str, fetch_base: str, evidence: Path | None = None) -> dict[str, Report]:
    from .verify import verify
    out = {}
    for v in ALL:
        vcfg = variant_cfg(cfg, v, base_url)
        res = verify(base_url, fetch_base, cfg=vcfg, check_debs=False, site_checks=False,
                     icons=bool({"icons", "production"} & v.components),
                     evidence=(evidence / v.key) if evidence else None)
        allowed = EXPECTED.get(v.key, set())
        r = Report(f"diag/{v.key}")
        for level, code, msg in res.report.items:
            if level == "error" and code in allowed:
                r.warn(code, f"expected for this variant: {msg}")
            else:
                r.items.append((level, code, msg))
        out[v.key] = r
    return out

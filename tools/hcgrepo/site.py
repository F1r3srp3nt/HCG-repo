"""Build the complete static snapshot: APT repository + depictions + website.

Everything is written into a fresh temporary directory, verified, and only then moved into place,
so the published indexes and package files always come from the same build.

The website is generated from the index the build has just written: only packages that are really
in Packages get a card, an install action or a detail page. Planned products come from
config/products.toml and never get an install action. Every product also gets a page at
products/<key>/ that links to HCG Store (see docs/STORE_INTEGRATION.md).
"""

from __future__ import annotations

import functools
import html
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, debian
from .config import CANDIDATE_DIR, Product, Report, RepoConfig, entry_channel, load_lock
from .repo import (PoolPackage, build_info_date, check_repository, commercial_gate, compress_indexes, git_revision,
                   load_pool, render_packages, render_release)

TINT = "#2FD3E6"
ASSET_DIR = Path(__file__).parent / "assets"
SOURCE_REPO = "https://github.com/F1r3srp3nt/HCG-repo"
BANNER_WIDTHS = (800, 1200, 2000)
REPO_BANNER = "hcg-repo-banner.jpg"   # 1200x400 JPEG under <repo>/assets/, used by Sileo
# Branding files copied into the site (assets/brand/) and the site root (iOS looks for this name).
BRAND_FILES = ("favicon.svg", "favicon-16.png", "favicon-32.png", "favicon-48.png", "favicon-192.png",
               "apple-touch-icon.png", "hcg-repo-icon-512.png", "hcg-repo-banner-1200.jpg",
               *(f"hcg-repo-banner-{w}.webp" for w in BANNER_WIDTHS))


class BuildError(Exception):
    def __init__(self, message: str, report: Report | None = None):
        super().__init__(message)
        self.report = report


@dataclass
class BuildResult:
    out_dir: Path
    base_url: str
    repo_url: str
    packages: list[PoolPackage]
    report: Report
    signed: bool = False
    signature_verified: bool = False
    signature_detail: str = "unsigned"
    files: list[str] = field(default_factory=list)
    groups: dict = field(default_factory=dict)


def root_rel(cfg: RepoConfig, extra: int = 0) -> str:
    """Relative path from <source>/<extra dirs>/ back to the site root, for any source depth
    (repo/ = 1, repo/candidate/ = 2). Depictions sit 2 levels below the source."""
    return "../" * (len(cfg.repo_subdir.strip("/").split("/")) + extra)


# ---------------------------------------------------------------------------- text helpers

e = html.escape


def md_inline(text: str) -> str:
    out = e(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])", r"<em>\1</em>", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', out)
    return out


def md_block(text: str) -> str:
    """Tiny, safe Markdown subset: paragraphs, '-' and '1.' lists, '###' headings, inline code/bold/links."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    ordered = False

    def flush():
        nonlocal ordered
        if para:
            out.append(f"<p>{md_inline(' '.join(para))}</p>")
            para.clear()
        if items:
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{md_inline(i)}</li>" for i in items) + f"</{tag}>")
            items.clear()

    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^(\d+)\.\s+(.*)", s)
        if not s:
            flush()
        elif s.startswith("### "):
            flush()
            out.append(f"<h4>{md_inline(s[4:])}</h4>")
        elif s.startswith(("- ", "* ")) or m:
            is_ordered = bool(m)
            if para or (items and is_ordered != ordered):
                flush()
            ordered = is_ordered
            items.append(m.group(2) if m else s[2:])
        elif items and line.startswith("  "):
            items[-1] += " " + s
        else:
            if items:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


def describe_arch(arch: str) -> str:
    return {"iphoneos-arm64": "Rootless", "iphoneos-arm": "Rootful", "iphoneos-arm64e": "Roothide",
            "all": "Any layout"}.get(arch, arch)


def ios_requirement(p: PoolPackage) -> tuple[str, str]:
    """(short label, sentence) describing the iOS range, distinguishing declared from binary-derived."""
    lo, hi = debian.firmware_bounds(p.info.get("Depends"))
    need = p.info.binary_min_os
    if lo:
        short = f"iOS {lo}+" + (f" ({hi})" if hi else "")
        return short, (f"The package installs on iOS {lo} or later" + (f" (firmware {hi})" if hi else "")
                       + ". This is the declared install minimum, not tested compatibility; see Tested devices.")
    if need:
        return (f"iOS {need}+ (binary)",
                f"The package declares no iOS range; its binaries are built for iOS {need} or later.")
    return "Any iOS (undeclared)", "The package declares no iOS range."


def size_text(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(1, round(n / 1000))} KB"


def dependency_text(p: PoolPackage) -> str:
    deps = p.info.get("Depends")
    if not deps:
        return "None"
    try:
        groups = debian.parse_relations(deps)
    except debian.ControlError:
        return deps
    shown = [" or ".join(r.name for r in g) for g in groups if not (len(g) == 1 and g[0].name == "firmware")]
    return ", ".join(shown) or "None"


def short_description(p: PoolPackage) -> tuple[str, str]:
    d = p.info.get("Description") or ""
    first, _, rest = d.partition("\n")
    long = "\n".join(l[1:] if l.startswith(" ") else l for l in rest.splitlines())
    long = "\n".join("" if l.strip() == "." else l for l in long.splitlines())
    return first.strip(), long.strip()


def display_name(p: PoolPackage) -> str:
    return p.info.get("Name") or p.info.package


def latest_by_package(pkgs: list[PoolPackage]) -> dict[str, list[PoolPackage]]:
    """package id -> its pool packages, newest version first."""
    groups: dict[str, list[PoolPackage]] = {}
    for p in pkgs:
        groups.setdefault(p.info.package, []).append(p)
    cmp = functools.cmp_to_key(debian.compare_versions)
    for k in groups:
        groups[k].sort(key=lambda p: (cmp(p.info.version), p.info.architecture), reverse=True)
    return dict(sorted(groups.items(), key=lambda kv: display_name(kv[1][0]).lower()))


def status_badge(status: str) -> str:
    if status == "device-tested":
        return '<span class="badge badge-ok">Device-tested</span>'
    return '<span class="badge badge-warn">Experimental</span>'


def channel_badge(p: PoolPackage) -> str:
    return '<span class="badge badge-beta">Beta</span>' if p.meta.channel == "beta" else ""


RESULT_TEXT = {"pass": "Works", "fail": "Does not work", "partial": "Partly works"}
STAGE_TEXT = {"install": "Installation", "runtime": "Runtime"}


def install_failed(p: PoolPackage) -> list[dict]:
    """Recorded installation failures of this version, when no installation has succeeded."""
    tests = p.meta.device_tests(p.info.version)
    ok = [t for t in tests if t.get("stage", "runtime") == "install" and t.get("result") == "pass"]
    runtime = [t for t in tests if t.get("stage", "runtime") == "runtime"]
    fails = [t for t in tests if t.get("stage", "runtime") == "install" and t.get("result") in ("fail", "partial")]
    return [] if ok or runtime else fails


def label_html(p: PoolPackage) -> str:
    lab = p.meta.label(p.info.version)
    return f'<p class="card note note-warn" role="note"><strong>{e(lab)}</strong></p>' if lab else ""


def install_failure_html(p: PoolPackage) -> str:
    fails = install_failed(p)
    if not fails:
        return ""
    where = "; ".join(f"{t.get('device')}, iOS {t.get('ios')}, {t.get('jailbreak')}" for t in fails)
    return ('<p class="card note note-warn" role="alert"><strong>Installation failed in testing.</strong> '
            f"This build did not install on {e(where)}. It is listed for testing and diagnosis only; "
            "see Known issues.</p>")


def status_text(p: PoolPackage) -> str:
    """One honest line: beta or not, and whether ANY device test exists. Never says "working" without one."""
    st = "Device-tested" if p.meta.status(p.info.version) == "device-tested" else "Experimental"
    if p.meta.channel == "beta":
        st = "Beta, " + st[0].lower() + st[1:]
    tests = p.meta.device_tests(p.info.version)
    label = p.meta.label(p.info.version)
    if label and not tests:
        return ("Beta: " if p.meta.channel == "beta" else "") + label
    if not tests:
        st += ", not yet device-tested"
    elif install_failed(p):
        st += f", installation failed on {len(install_failed(p))} tested device(s); runtime not tested"
    elif not [t for t in tests if t.get("stage", "runtime") == "runtime"]:
        st += ", runtime not yet tested"
    return st


def tested_devices(p: PoolPackage) -> list[dict]:
    """Every recorded device test of this version (pass, partial and fail), exactly as recorded."""
    return p.meta.device_tests(p.info.version)


def tested_devices_html(p: PoolPackage) -> str:
    tests = tested_devices(p)
    if not tests:
        return ('<p class="card note note-warn"><strong>Not yet device-tested.</strong> '
                "No device, iOS version or jailbreak is known to work with this build.</p>")
    rows = "".join(f"<tr><td>{e(t.get('device', ''))}</td><td>iOS {e(str(t.get('ios', '')))}</td><td>{e(t.get('jailbreak', ''))}</td>"
                   f"<td>{e(STAGE_TEXT.get(t.get('stage', 'runtime'), ''))}</td>"
                   f"<td>{e(RESULT_TEXT.get(t.get('result'), str(t.get('result'))))}</td><td>{e(str(t.get('date', '')))}</td></tr>"
                   for t in tests)
    return ('<div class="table-wrap"><table><caption class="sr-only">Device tests of this version</caption>'
            '<thead><tr><th scope="col">Device</th><th scope="col">iOS</th><th scope="col">Jailbreak</th>'
            f'<th scope="col">Stage</th><th scope="col">Result</th><th scope="col">Date</th></tr></thead><tbody>{rows}</tbody></table></div>'
            '<p class="small muted">Only these exact combinations have been tested. Others are untested.</p>')


def passing_tests(p: PoolPackage) -> list[dict]:
    return [t for t in p.meta.device_tests(p.info.version) if t.get("result") == "pass"]


def tests_text(p: PoolPackage) -> str:
    """Never implies a build works beyond its recorded device tests, and never hides a failed one."""
    every = p.meta.device_tests(p.info.version)
    tests = passing_tests(p)
    if not every:
        return "Not yet device-tested."
    if install_failed(p):
        return (f"Installation failed on {len(install_failed(p))} tested device setup(s). Runtime behaviour has not been "
                "tested. See Tested devices.")
    if not tests:
        return f"Tested on {len(every)} device setup(s); none passed. See Tested devices."
    text = "Works on " + "; ".join(f"{t['device']}, iOS {t['ios']}, {t['jailbreak']} ({t['date']})" for t in tests) + "."
    if len(tests) < len(every):
        text += f" {len(every) - len(tests)} other tested setup(s) did not fully work; see Tested devices."
    return text


def provenance(cfg: RepoConfig, p: PoolPackage) -> dict:
    f = cfg.root / (p.rel + ".provenance.json")
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def provenance_html(prov: dict) -> str:
    repo, commit = prov.get("repository"), prov.get("commit")
    if repo and commit:
        src = (f'<a href="https://github.com/{e(repo)}/commit/{e(commit)}">{e(repo)} @ <code>{e(commit[:10])}</code></a>')
    else:
        src = "local import"
    how = {"github-workflow-artifact": "CI artifact", "github-release": "GitHub release",
           "source-build": "built from source", "local-file": "local file"}.get(prov.get("source", ""), prov.get("source", ""))
    run = prov.get("run_url") or prov.get("builder_run_url") or prov.get("release_url")
    run_html = f' · <a href="{e(run)}">build run</a>' if run else ""
    return f"{src} · {e(how)}{run_html}"


# ---------------------------------------------------------------------------- page shell


def page(title: str, body: str, rel: str, description: str, canonical: str, current: str = "",
         scripts: tuple[str, ...] = (), noindex: bool = False) -> str:
    extra = "".join(f'\n<script src="{rel}assets/{e(name)}" defer></script>' for name in scripts)
    robots = '\n<meta name="robots" content="noindex, nofollow">' if noindex else ""

    def nav(href: str, label: str) -> str:
        cur = ' aria-current="page"' if current == label else ""
        return f'<a href="{rel}{href}"{cur}>{label}</a>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#05080b">{robots}
<meta name="description" content="{e(description)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:image" content="{e(canonical_root(canonical, rel))}assets/brand/hcg-repo-banner-1200.jpg">
<title>{e(title)}</title>
<link rel="canonical" href="{e(canonical)}">
<link rel="icon" href="{rel}assets/brand/favicon.svg" type="image/svg+xml">
<link rel="icon" href="{rel}assets/brand/favicon-32.png" sizes="32x32" type="image/png">
<link rel="icon" href="{rel}assets/brand/favicon-16.png" sizes="16x16" type="image/png">
<link rel="apple-touch-icon" href="{rel}apple-touch-icon.png">
<link rel="stylesheet" href="{rel}assets/site.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="circuit" aria-hidden="true"></div>
<header class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="{rel or './'}"><img src="{rel}repo/CydiaIcon@2x.png" alt="" width="34" height="34"><span>HCG Repo</span></a>
    <nav aria-label="Primary">{nav('#packages', 'Packages')}{nav('#install', 'Install')}{nav('#help', 'Help')}</nav>
  </div>
</header>
<main id="main" tabindex="-1">
{body}
</main>
<footer class="footer">
  <p><strong>HCG Repo</strong> · Hakmi Capital Group</p>
  <p><a href="{SOURCE_REPO}">Repository source on GitHub</a></p>
</footer>
<div class="sr-only" role="status" aria-live="polite" id="live"></div>
<script src="{rel}assets/site.js" defer></script>{extra}
</body>
</html>
"""


def canonical_root(canonical: str, rel: str) -> str:
    """Absolute site root, derived from a page's canonical URL and its relative depth."""
    depth = rel.count("../")
    root = canonical
    for _ in range(depth):
        root = root.rstrip("/").rsplit("/", 1)[0] + "/"
    return root


def url_breaks(url: str) -> str:
    """Escaped URL with <wbr> after '//' and each later '/', so narrow screens wrap at path boundaries."""
    scheme, sep, rest = url.partition("://")
    return e(scheme + sep) + "<wbr>" + "/<wbr>".join(e(part) for part in rest.split("/"))


def source_actions(repo_url: str, rel: str = "", uid: str = "source-url") -> str:
    sileo = f"sileo://source/{repo_url}"
    zebra = f"zbra://sources/add/{repo_url}"
    return f"""<div class="source">
  <div class="actions actions-primary">
    <a class="btn btn-primary" href="{e(sileo)}">Add to Sileo</a>
    <a class="btn btn-metal" href="{e(zebra)}">Add to Zebra</a>
  </div>
  <p class="source-label" id="{uid}-label">Repository URL</p>
  <div class="source-row">
    <code id="{uid}" class="source-url" aria-labelledby="{uid}-label">{url_breaks(repo_url)}</code>
    <button type="button" class="btn btn-metal btn-copy" data-copy="#{uid}">Copy URL</button>
  </div>
  <p class="hint">Open this page on your iPhone or iPad. <strong>Add to Sileo</strong> opens Sileo’s Add Source prompt with this URL filled in, and you confirm it there.</p>
</div>"""


# ---------------------------------------------------------------------------- landing page


def banner_html(rel: str) -> str:
    srcset = ", ".join(f"{rel}assets/brand/hcg-repo-banner-{w}.webp {w}w" for w in BANNER_WIDTHS)
    return f"""<div class="banner-frame">
  <picture>
    <source type="image/webp" srcset="{srcset}" sizes="(min-width: 1120px) 1080px, calc(100vw - 32px)">
    <img class="banner" src="{rel}assets/brand/hcg-repo-banner-1200.jpg" width="2000" height="667" alt="" fetchpriority="high" decoding="async">
  </picture>
</div>"""


def package_card(cfg: RepoConfig, versions: list[PoolPackage], repo_rel: str) -> str:
    p = versions[0]
    pkg = p.info.package
    summary = p.meta.summary or short_description(p)[0]
    icon = (f'<img src="{repo_rel}assets/{e(pkg)}/{e(p.meta.icon)}" alt="" width="64" height="64" class="pkg-icon" loading="lazy">'
            if p.meta.icon else '<span class="pkg-icon pkg-icon-blank" aria-hidden="true"></span>')
    current = [v for v in versions if v.info.version == p.info.version]
    layouts = ", ".join(f"{describe_arch(v.info.architecture)} <span class=\"muted\">({e(v.info.architecture)})</span>" for v in current)
    ios_short, _ = ios_requirement(p)
    statuses = {v.meta.status(v.info.version) for v in current}
    badge = (channel_badge(p) + status_badge("device-tested" if statuses == {"device-tested"} else "experimental")
             + access_badge(p.meta.access_for(p.info.version)))
    detail = f"{repo_rel}depictions/{e(pkg)}/"
    product = cfg.visible_product_for_package(pkg)
    product_link = f'\n    <a class="btn btn-metal" href="products/{e(product.key)}/">Product page</a>' if product else ""
    install_btn = (f'<a class="btn btn-metal" href="sileo://package/{e(pkg)}">Open in Sileo (installation failed in testing)</a>'
                   if install_failed(p) else f'<a class="btn btn-primary" href="sileo://package/{e(pkg)}">Install in Sileo</a>')
    return f"""<li class="card pkg-card">
  <div class="pkg-head">
    {icon}
    <div class="pkg-title">
      <h3><a href="{detail}">{e(display_name(p))}</a></h3>
      <p class="pkg-meta">{badge} <span class="pkg-version">v{e(p.info.version)}</span></p>
    </div>
  </div>
  <p class="pkg-id"><code>{e(pkg)}</code></p>
  <p class="pkg-desc">{e(summary)}</p>
  <dl class="pkg-facts">
    <div><dt>Environment</dt><dd>{layouts}</dd></div>
    <div><dt>Download</dt><dd>{size_text(sum(v.info.size for v in current) // max(len(current), 1))}</dd></div>
    <div><dt>iOS</dt><dd>{e(ios_short)}</dd></div>
    <div><dt>Requires</dt><dd>{e(dependency_text(p))}</dd></div>
  </dl>
  {f'<p class="pkg-tested"><strong>{e(p.meta.label(p.info.version))}</strong></p>' if p.meta.label(p.info.version) else ''}
  <p class="pkg-tested">{e(tests_text(p))}</p>
  <div class="actions">
    {install_btn}
    <a class="btn btn-metal" href="{detail}">Details</a>{product_link}
  </div>
</li>"""


def landing(cfg: RepoConfig, base: str, repo_url: str, groups: dict[str, list[PoolPackage]], info: dict,
            candidate: dict | None = None) -> str:
    n = len(groups)
    cand_groups = (candidate or {}).get("groups", {})
    if groups:
        cards = "".join(package_card(cfg, v, "repo/") for v in groups.values())
        packages_html = (f'<p class="section-lead">{n} package{"s" if n != 1 else ""} in the repository index. '
                         'Each card shows the release status and the environment the package declares.</p>'
                         f'<ul class="cards" role="list">{cards}</ul>')
    else:
        packages_html = """<div class="card empty">
  <img src="assets/brand/favicon.svg" alt="" width="56" height="56">
  <div>
    <h3>No packages are published yet</h3>
    <p>The source is live and valid, so you can add it now and Sileo will refresh it without errors. Public packages appear here as soon as a validated build is published. Packages that need HCG Connect sign-in or an HCG Store purchase are listed under <a href="#sign-in-required">Sign-in required</a>; projects still being built are under <a href="#in-development">In development</a>.</p>
  </div>
</div>"""

    indexed = set(groups) | set(cand_groups)
    commercial = [p for p in cfg.visible_products if p.commercial]
    not_indexed = [p for p in cfg.visible_products if not p.commercial and not (set(p.package_ids) & indexed)]
    cand_html = ""
    if candidate:
        ccards = "".join(package_card(cfg, v, f"{cfg.repo_subdir}/{CANDIDATE_DIR}/") for v in cand_groups.values())
        cand_html = f"""<section id="candidate" class="section" aria-labelledby="cand-h">
<h2 id="cand-h">Testing channel: HCG Repo Candidate</h2>
<p class="section-lead">Betas of HCG tweaks, jailbreak apps and command-line tools, in a separate source. They may fail to install
or misbehave; each one lists what has and has not been tested. Nothing here appears in the stable source above.</p>
{source_actions(candidate["url"], uid="candidate-url")}
<p><a href="{cfg.repo_subdir}/{CANDIDATE_DIR}/">About the testing channel</a>: migration from the old diagnostic source, and how
candidate and stable versions update.</p>
{f'<ul class="cards" role="list">{ccards}</ul>' if ccards else '<p class="muted">No builds are in testing right now.</p>'}
</section>"""
    restricted = [p for p in not_indexed if p.restricted]
    planned = [p for p in not_indexed if p not in restricted]
    restricted_html = restricted_section(restricted, info.get("protected", {}), bool(cfg.payment_endpoint))
    dev_items = "".join(f"""<li class="card dev-card">
  <div class="dev-head"><h3>{e(p.name)}</h3><span class="badge badge-muted">{e(p.status)}</span></div>
  {f'<p class="dev-kind">{e(p.kind)}</p>' if p.kind else ''}
  <p>{e(p.summary)}</p>
  {f'<p class="small muted">{e(p.detail)}</p>' if p.detail else ''}
  <div class="actions"><a class="btn btn-metal" href="products/{e(p.key)}/">Product page</a></div>
</li>""" for p in planned)
    dev_html = f"""<section id="in-development" class="section" aria-labelledby="dev-h">
<h2 id="dev-h">Pending builds</h2>
<p class="section-lead">No installable build exists yet, in either source. Nothing here has an install action.</p>
<ul class="cards cards-dev" role="list">{dev_items}</ul>
</section>""" if planned else ""

    archs = ", ".join(f"{describe_arch(a)} (<code>{e(a)}</code>)" for a in cfg.architectures)
    rev = info["revision"]
    rev_html = (f'<a href="{SOURCE_REPO}/commit/{e(rev)}"><code>{e(rev[:10])}</code></a>' if re.fullmatch(r"[0-9a-f]{40}", rev)
                else f"<code>{e(rev)}</code>")
    body = f"""
<section class="hero" aria-labelledby="hero-h">
  {banner_html("")}
  <div class="intro card">
    <p class="eyebrow">Hakmi Capital Group</p>
    <h1 id="hero-h">HCG Repo</h1>
    <p class="lead">Jailbreak apps, tweaks and command-line tools by HCG, for Sileo and compatible package managers.</p>
    {source_actions(repo_url)}
  </div>
</section>

<section id="packages" class="section" aria-labelledby="packages-h">
<h2 id="packages-h">Available packages</h2>
{packages_html}
</section>
{commercial_section(cfg, commercial)}{restricted_html}
{cand_html}
{dev_html}

<section id="install" class="section prose" aria-labelledby="install-h">
<h2 id="install-h">Install</h2>
<ol class="steps">
  <li><strong>Add the source.</strong> On the jailbroken device, tap <em>Add to Sileo</em> above, or in Sileo open <em>Sources → +</em> and enter <code>{e(repo_url)}</code>.</li>
  <li><strong>Refresh.</strong> The source appears as “{e(cfg.origin)}” with the HCG icon.</li>
  <li><strong>Install.</strong> Open a package, read its limitations, review the dependencies Sileo lists, then install.</li>
</ol>
<div class="grid-2">
  <div class="card note">
    <h3>Rootless and rootful</h3>
    <p>Rootless jailbreaks (files under <code>/var/jb</code>) use <code>iphoneos-arm64</code>; rootful ones use <code>iphoneos-arm</code>. Sileo lists only packages built for your layout. Roothide is not supported.</p>
  </div>
  <div class="card note">
    <h3>Zebra or the command line</h3>
    <p>Use <em>Add to Zebra</em>, or add this line under <code>/var/jb/etc/apt/sources.list.d/</code> (rootless) and run <code>apt update</code>:</p>
    <pre><code>deb {e(repo_url)} ./</code></pre>
  </div>
</div>
<p class="small muted">Release labels: <span class="badge badge-warn">Experimental</span> passed repository validation but has no recorded device test. <span class="badge badge-ok">Device-tested</span> has a recorded test of that exact version on a physical device, shown on its card.</p>
</section>

<section id="help" class="section prose" aria-labelledby="help-h">
<h2 id="help-h">Help</h2>
<div class="faq">
<details><summary>Sileo cannot refresh the source</summary>
<p>Check that the URL is exactly <code>{e(repo_url)}</code>, including the trailing slash, and that the device is online. Pull down to refresh again, and note any message Sileo shows if it keeps failing.</p></details>
<details><summary>“Add to Sileo” does nothing</summary>
<p>It only works on a device with Sileo installed. Otherwise use <em>Copy URL</em> and add the source manually.</p></details>
<details><summary>“Install in Sileo” says the package is not found</summary>
<p>Add the source first and let Sileo refresh, then try again. Packages are also hidden when they don’t match your layout or iOS version.</p></details>
<details><summary>An installed app does not appear on the home screen</summary>
<p>Run <code>uicache -a</code> (from <code>uikittools</code>), or respring.</p></details>
<details><summary>About signatures</summary>
<p>{e(info['signature_help'])} Never turn off APT signature checking globally to work around any repository.</p></details>
</div>
<h3>Removing packages and the source</h3>
<ul>
  <li><strong>Package:</strong> Sileo → <em>Installed</em> → the package → <em>Remove</em>. Each package page lists anything it leaves behind.</li>
  <li><strong>Source:</strong> Sileo → <em>Sources</em> → swipe left on “{e(cfg.origin)}” → <em>Delete</em>. Installed packages stay but stop receiving updates.</li>
</ul>
</section>

<section id="details" class="section" aria-labelledby="details-h">
<h2 id="details-h" class="sr-only">Repository details</h2>
<details class="card repo-details">
  <summary>Repository details</summary>
  <dl class="kv">
    <div><dt>APT source</dt><dd><code>{e(repo_url)}</code> (flat, <code>./</code>)</dd></div>
    <div><dt>Packages in index</dt><dd>{n} package{"s" if n != 1 else ""}, {info['builds']} build{"s" if info['builds'] != 1 else ""}</dd></div>
    <div><dt>Layouts accepted</dt><dd>{archs}</dd></div>
    <div><dt>Index formats</dt><dd><code>Packages</code>, <code>.xz</code>, <code>.bz2</code>, <code>.gz</code>; SHA-256 and SHA-512 for every file</dd></div>
    <div><dt>Metadata signature</dt><dd>{e(info['signature'])}</dd></div>
    <div><dt>Built</dt><dd><time datetime="{e(info['date_iso'])}">{e(info['date_human'])}</time> from {rev_html}</dd></div>
    <div><dt>Machine-readable</dt><dd><a href="build-info.json">build-info.json</a> · <a href="repo/Release">Release</a> · <a href="repo/Packages">Packages</a></dd></div>
  </dl>
</details>
</section>
"""
    return page("HCG Repo", body, "", "HCG Repo: jailbreak apps, tweaks and tools by Hakmi Capital Group for Sileo.", base)


ACCESS_LABELS = {"public": "Free · no sign-in", "connect": "HCG Connect sign-in", "paid": "Paid · HCG Store"}


def access_badge(level: str) -> str:
    if level not in ACCESS_LABELS:
        return ""
    return f'<span class="badge badge-access">{e(ACCESS_LABELS[level])}</span>'


def restricted_section(products: list[Product], protected: dict[str, list[dict]], endpoint_live: bool) -> str:
    """Products that need HCG Connect sign-in or Store ownership and are not installable yet."""
    if not products:
        return ""
    items = []
    for p in products:
        prepared = [x for pid in p.package_ids for x in protected.get(pid, [])]
        prep = ", ".join(f"{e(x['version'])} ({e(describe_arch(x['architecture']))})" for x in prepared)
        items.append(f"""<li class="card dev-card restricted-card">
  <div class="dev-head"><h3>{e(p.name)}</h3>{access_badge(p.access)}</div>
  {f'<p class="dev-kind">{e(p.kind)}</p>' if p.kind else ''}
  <p>{e(p.summary)}</p>
  {f'<p class="small">Build prepared: {prep}. Not downloadable here.</p>' if prep else ''}
  <p class="small muted"><span class="badge badge-muted">Not yet available</span> {e(p.detail)}</p>
  <div class="actions"><a class="btn btn-metal" href="products/{e(p.key)}/">Product page</a></div>
</li>""")
    lead = ("These need HCG Connect sign-in; paid products also need HCG Store ownership. Authenticated delivery is not "
            "live yet, so they cannot be installed, and their packages are not published as public files."
            if not endpoint_live else "These need HCG Connect sign-in in Sileo.")
    return f"""<section id="sign-in-required" class="section" aria-labelledby="restricted-h">
<h2 id="restricted-h">Sign-in required</h2>
<p class="section-lead">{lead}</p>
<ul class="cards cards-dev" role="list">{''.join(items)}</ul>
</section>"""


# ---------------------------------------------------------------------------- package pages


DEFAULT_INSTALL = """1. Add the HCG Repo source to Sileo.
2. Open this package in Sileo, review the dependencies it lists, and install."""
DEFAULT_REMOVAL = """Sileo → *Installed* → the package → *Remove*."""


def web_depiction(cfg: RepoConfig, repo_url: str, versions: list[PoolPackage], all_versions: list[PoolPackage]) -> str:
    p = versions[0]
    pkg = p.info.package
    summary, long = short_description(p)
    rel = root_rel(cfg, 2)   # depictions/<pkg>/ below the source, at any source depth
    icon = (f'<img src="../../assets/{e(pkg)}/{e(p.meta.icon)}" alt="" width="88" height="88" class="pkg-icon pkg-icon-lg">'
            if p.meta.icon else "")
    header = (f'<div class="dep-header-frame"><img class="dep-header" src="../../assets/{e(pkg)}/{e(p.meta.header)}" alt=""></div>'
              if p.meta.header else "")
    ios_short, ios_long = ios_requirement(p)
    builds_rows = "".join(
        f"""<tr><td>{describe_arch(v.info.architecture)} <code>{e(v.info.architecture)}</code></td>
<td>{status_badge(v.meta.status(v.info.version))}</td>
<td>{'Through HCG Connect in Sileo' if v.protected else f'<a href="../../debs/{e(v.info.canonical_filename())}" download>.deb</a>'} <span class="muted">{v.info.size:,} bytes</span></td></tr>"""
        for v in versions)
    compat = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in (
        ("Version", e(p.info.version)),
        ("Environment", ", ".join(f"{describe_arch(v.info.architecture)} (<code>{e(v.info.architecture)}</code>)" for v in versions)),
        ("iOS", e(ios_long)),
        ("Requires", e(dependency_text(p))),
        ("Device testing", e(tests_text(p))),
        ("Section", e(p.info.get("Section") or "—")),
        ("Maintainer", e(p.info.get("Maintainer") or "—")),
    ))
    shots = ""
    if p.meta.screenshots:
        figs = "".join(f'<figure><img src="../../assets/{e(pkg)}/{e(s["file"])}" alt="{e(s["caption"])}" loading="lazy">'
                       f'<figcaption>{e(s["caption"])}</figcaption></figure>' for s in p.meta.screenshots)
        shots = f'<section class="section-tight" aria-labelledby="shots-h"><h2 id="shots-h">Screenshots</h2><div class="shots">{figs}</div></section>'
    seen: list[str] = []
    changes = []
    for v in all_versions:
        if v.info.version in seen:
            continue
        seen.append(v.info.version)
        text = p.meta.changelog.get(v.info.version)
        body_html = md_block(text) if text else '<p class="muted">No changelog entry.</p>'
        changes.append(f'<article class="change"><h3>{e(v.info.version)} {status_badge(v.meta.status(v.info.version))}</h3>'
                       f"{body_html}</article>")
    downloads = "".join(
        f"""<li>{e(v.info.canonical_filename()) + ' (delivered through HCG Connect after sign-in)' if v.protected else f'<a href="../../debs/{e(v.info.canonical_filename())}" download>{e(v.info.canonical_filename())}</a>'}
<span class="small muted">{v.info.size:,} bytes</span><br>
<span class="small">SHA-256 <code class="hash">{v.info.sha256}</code></span><br>
<span class="small muted">Source: {provenance_html(provenance(cfg, v))}</span></li>""" for v in all_versions)
    issues = (f'<section class="section-tight" aria-labelledby="iss-h"><h2 id="iss-h">Known issues</h2>'
              f'<div class="card note note-warn">{md_block(p.meta.known_issues)}</div></section>') if p.meta.known_issues else ""
    beta = ('<p class="card note note-warn" role="note"><strong>Experimental beta.</strong> Free to install. It may change, '
            "break or be withdrawn, and it has only been tested where the table below says so.</p>") if p.meta.channel == "beta" else ""
    limits = (f'<section class="section-tight" aria-labelledby="lim-h"><h2 id="lim-h">Limitations</h2>'
              f'<div class="card note note-warn">{md_block(p.meta.limitations)}</div></section>') if p.meta.limitations else ""
    body = f"""
<article class="dep prose">
<p class="crumbs"><a href="{rel}">HCG Repo</a> <span aria-hidden="true">›</span> <a href="{rel}#packages">Packages</a></p>
{header}
<div class="dep-title card">
  {icon}
  <div>
    <h1>{e(display_name(p))}</h1>
    <p class="pkg-id"><code>{e(pkg)}</code> · version <strong>{e(p.info.version)}</strong> · {channel_badge(p)}{status_badge(p.meta.status(p.info.version))}</p>
    <p class="lead">{e(p.meta.summary or summary)}</p>
  </div>
</div>
{install_failure_html(p)}{label_html(p)}
<div class="actions actions-primary">
  <a class="btn {'btn-metal' if install_failed(p) else 'btn-primary'}" href="sileo://package/{e(pkg)}">{'Open in Sileo' if install_failed(p) else 'Install in Sileo'}</a>
  <a class="btn btn-metal" href="sileo://source/{e(repo_url)}">Add source first</a>
</div>
<p class="hint">{'“Open in Sileo” shows' if install_failed(p) else '“Install in Sileo” opens'} this package once the {e(cfg.origin)} source has been added.</p>
{beta}
<section class="section-tight" aria-labelledby="about-h">
<h2 id="about-h">About</h2>
{md_block(long) if long and long != summary else f"<p>{e(summary)}</p>"}
{md_block(p.meta.notes) if p.meta.notes else ""}
</section>

<section class="section-tight" aria-labelledby="compat-h">
<h2 id="compat-h">Compatibility</h2>
<dl class="kv card">{compat}</dl>
<div class="table-wrap"><table>
<caption class="sr-only">Builds of version {e(p.info.version)}</caption>
<thead><tr><th scope="col">Build</th><th scope="col">Status</th><th scope="col">Download</th></tr></thead>
<tbody>{builds_rows}</tbody></table></div>
</section>

<section class="section-tight" aria-labelledby="dev-h">
<h2 id="dev-h">Tested devices</h2>
{tested_devices_html(p)}
</section>
{issues}
{shots}
<section class="section-tight" aria-labelledby="inst-h">
<h2 id="inst-h">Installation</h2>
{md_block(p.meta.install or DEFAULT_INSTALL)}
</section>
{limits}
<section class="section-tight" aria-labelledby="chg-h">
<h2 id="chg-h">Changelog</h2>
{''.join(changes)}
</section>

<section class="section-tight" aria-labelledby="rm-h">
<h2 id="rm-h">Removal</h2>
{md_block(p.meta.removal or DEFAULT_REMOVAL)}
</section>

<section class="section-tight" aria-labelledby="dl-h">
<h2 id="dl-h">Downloads and checksums</h2>
<ul class="downloads">{downloads}</ul>
</section>
</article>"""
    return page(f"{display_name(p)} · HCG Repo", body, rel, p.meta.summary or summary, f"{repo_url}depictions/{pkg}/")


def sileo_depiction(cfg: RepoConfig, repo_url: str, versions: list[PoolPackage], all_versions: list[PoolPackage]) -> dict:
    p = versions[0]
    pkg = p.info.package
    summary, long = short_description(p)
    status = status_text(p)
    _, ios_long = ios_requirement(p)
    beta_note = ("**Experimental beta.** Free to install. It may change, break or be withdrawn, and it has only been "
                 "tested where Tested devices says so.") if p.meta.channel == "beta" else ""
    fail_note = ""
    if install_failed(p):
        where = "; ".join(f"{t.get('device')}, iOS {t.get('ios')}, {t.get('jailbreak')}" for t in install_failed(p))
        fail_note = (f"**Installation failed in testing** on {where}. Listed for testing and diagnosis only; see Known issues.")
    lab = p.meta.label(p.info.version)
    lab_note = f"**{lab}**" if lab else ""
    about = "\n\n".join(x for x in (fail_note, lab_note, beta_note, p.meta.summary or summary, long if long != summary else "", p.meta.notes) if x)
    details = [
        {"class": "DepictionMarkdownView", "markdown": about, "useSpacing": True},
        {"class": "DepictionSeparatorView"},
        {"class": "DepictionHeaderView", "title": "Information"},
        {"class": "DepictionTableTextView", "title": "Version", "text": p.info.version},
        {"class": "DepictionTableTextView", "title": "Status", "text": status},
        {"class": "DepictionTableTextView", "title": "Layout",
         "text": ", ".join(describe_arch(v.info.architecture) for v in versions)},
        {"class": "DepictionTableTextView", "title": "Requires", "text": dependency_text(p)},
        {"class": "DepictionMarkdownView", "markdown": f"{ios_long} {tests_text(p)}", "useSpacing": True},
    ]
    if p.meta.screenshots:
        details.append({"class": "DepictionScreenshotsView", "itemSize": "{160, 346}", "itemCornerRadius": 8,
                        "screenshots": [{"url": f"{repo_url}assets/{pkg}/{s['file']}", "accessibilityText": s["caption"]}
                                        for s in p.meta.screenshots]})
    details.append({"class": "DepictionHeaderView", "title": "Tested devices"})
    tests = tested_devices(p)
    if tests:
        details += [{"class": "DepictionTableTextView", "title": f"{t.get('device')}",
                     "text": f"iOS {t.get('ios')}, {t.get('jailbreak')}: {STAGE_TEXT.get(t.get('stage', 'runtime'))} "
                             f"{RESULT_TEXT.get(t.get('result'), t.get('result')).lower()}"}
                    for t in tests]
        details.append({"class": "DepictionMarkdownView", "markdown": "Only these exact combinations have been tested.", "useSpacing": True})
    else:
        details.append({"class": "DepictionMarkdownView", "useSpacing": True,
                        "markdown": "**Not yet device-tested.** No device, iOS version or jailbreak is known to work with this build."})
    if p.meta.known_issues:
        details += [{"class": "DepictionHeaderView", "title": "Known issues"},
                    {"class": "DepictionMarkdownView", "markdown": p.meta.known_issues, "useSpacing": True}]
    if p.meta.limitations:
        details += [{"class": "DepictionHeaderView", "title": "Limitations"},
                    {"class": "DepictionMarkdownView", "markdown": p.meta.limitations, "useSpacing": True}]
    details += [
        {"class": "DepictionHeaderView", "title": "Installation"},
        {"class": "DepictionMarkdownView", "markdown": p.meta.install or DEFAULT_INSTALL, "useSpacing": True},
        {"class": "DepictionHeaderView", "title": "Removal"},
        {"class": "DepictionMarkdownView", "markdown": p.meta.removal or DEFAULT_REMOVAL, "useSpacing": True},
        {"class": "DepictionSeparatorView"},
        {"class": "DepictionTableButtonView", "title": "Web page, downloads and checksums", "action": f"{repo_url}depictions/{pkg}/"},
    ]
    seen: list[str] = []
    changes = []
    for v in all_versions:
        if v.info.version in seen:
            continue
        seen.append(v.info.version)
        changes.append({"class": "DepictionSubheaderView", "title": v.info.version, "useBoldText": True})
        changes.append({"class": "DepictionMarkdownView",
                        "markdown": p.meta.changelog.get(v.info.version) or "No changelog entry.", "useSpacing": True})
    dep = {
        "class": "DepictionTabView", "minVersion": "0.1", "tintColor": p.meta.tint or TINT,
        "tabs": [
            {"tabname": "Details", "class": "DepictionStackView", "views": details},
            {"tabname": "Changelog", "class": "DepictionStackView", "views": changes},
        ],
    }
    dep["headerImage"] = (f"{repo_url}assets/{pkg}/{p.meta.header}" if p.meta.header
                          else f"{repo_url}assets/{REPO_BANNER}")
    return dep


def repo_index(cfg: RepoConfig, repo_url: str) -> str:
    body = f"""<section class="section prose">
<div class="intro card">
<p class="eyebrow">APT source root</p>
<h1>HCG Repo source</h1>
<p>Add this address to Sileo or another package manager; it is not meant to be browsed.</p>
{source_actions(repo_url)}
<p><a href="{root_rel(cfg)}">Go to the HCG Repo page</a></p>
</div>
</section>"""
    return page("HCG Repo source", body, root_rel(cfg), "HCG Repo APT source root.", repo_url)


def candidate_index(cfg: RepoConfig, ccfg: RepoConfig, base: str, groups: dict, cand_groups: dict) -> str:
    """<source>/candidate/: what the testing channel is, how to add it, migration, and version ordering."""
    url = ccfg.repo_url(base)
    stable_url = cfg.repo_url(base)
    rows = "".join(
        f"<li><a href=\"depictions/{e(pkg)}/\">{e(display_name(v[0]))}</a> <code>{e(v[0].info.version)}</code> "
        f"<span class=\"small muted\">{e(status_text(v[0]))}</span></li>" for pkg, v in cand_groups.items())
    indexed = set(groups) | set(cand_groups)
    pending = [p for p in cfg.visible_products if not (set(p.package_ids) & indexed) and not p.restricted]
    held = [p for p in cfg.visible_products if p.restricted and not p.commercial and not (set(p.package_ids) & indexed)]
    pend = "".join(f"<li>{e(p.name)}: {e(p.status)}</li>" for p in pending)
    needs = {"connect": "HCG Connect sign-in", "paid": "HCG Connect sign-in and HCG Store ownership"}
    held_html = "".join(f"<li>{e(p.name)}: needs {needs.get(cfg.product_level(p) or p.access, 'sign-in')}, so it is never "
                        "published in this public channel.</li>" for p in held)
    body = f"""<section class="section prose">
<div class="intro card">
<p class="eyebrow">Testing channel</p>
<h1>HCG Repo Candidate</h1>
<p class="lead">Betas of HCG tweaks, jailbreak apps and command-line tools before they reach the stable HCG Repo source.
Builds here may fail to install or misbehave. Each package lists exactly what has been tested.</p>
{source_actions(url)}
<p><a href="{root_rel(ccfg)}">Go to the HCG Repo page</a></p>
</div>
<h2>In testing</h2>
{f'<ul>{rows}</ul>' if rows else '<p class="muted">No builds are in testing right now.</p>'}
<h2>Pending builds</h2>
{f'<ul>{pend}</ul>' if pend else '<p class="muted">None.</p>'}
{f'<h2>Not in this channel: sign-in required</h2><ul>{held_html}</ul>' if held_html else ''}
<h2>Moving from the old diagnostic source</h2>
<ol>
<li>In Sileo or Zebra, remove <strong>HCG Repo Candidate</strong> at <code>https://repo.hakmicapitalgroup.com/diag/d8-candidate/</code>
(and any <code>/diag/</code> source). That address is no longer served.</li>
<li>Add <code>{e(url)}</code> and refresh.</li>
<li>Nothing installed from the old source needs to be removed: package IDs are the same, so updates arrive from this source.</li>
</ol>
<h2>Candidate and stable versions</h2>
<ul>
<li>The two sources are independent: the stable source <code>{e(stable_url)}</code> never lists candidate builds.</li>
<li>Package IDs are the same in both. With both sources added, your package manager installs the highest version.</li>
<li>Betas use Debian pre-release versions such as <code>0.1.0~beta1</code>, which sort <em>before</em> <code>0.1.0</code>.
So when <code>0.1.0</code> is released to the stable source, testers are upgraded to it automatically.</li>
<li>To stop testing, remove this source. An installed beta stays until the stable source has a higher version.</li>
</ul>
</section>"""
    return page("HCG Repo Candidate", body, root_rel(ccfg), "HCG Repo Candidate: the testing channel for HCG betas.", url)


# ---------------------------------------------------------------------------- product pages

ACCESS_TEXT = {
    "public": "Public. No sign-in is needed to install.",
    "connect": "HCG Connect sign-in. Free for signed-in users.",
    "paid": "Paid. HCG Connect sign-in and HCG Store ownership.",
}
NO_DOWNLOADS = "Downloads are not available yet."

# ---------------------------------------------------------------------------- commercial listings (docs/COMMERCIAL.md)

PREVIEW_MARKER = "PREVIEW-NOT-FOR-DEPLOYMENT.txt"
PREVIEW_NOTICE = "Preview of a prepared listing. Not published: awaiting the owner's approval."
COMMERCIAL_NOT_READY = ("Purchasing and installation will become available once HCG Store’s payment and licensing "
                        "infrastructure is ready.")
STORE_PAGE_PENDING = "HCG Store page · Coming Soon"
COMMERCIAL_NO_INSTALL = ("It is not in the HCG Repo package index, so Sileo, Zebra and other package managers cannot "
                         "install it from HCG Repo. No package file is published here.")
CHANNEL_NAMES = {"commercial-prerelease": "Commercial Prerelease", "commercial-release": "Commercial Release"}


def commercial_price_line(c) -> str:
    note = f" | {e(c.price_note)}" if c.price_note else ""
    return f'<p class="price"><strong>{e(c.price_text)}</strong>{note}</p>'


def commercial_section(cfg: RepoConfig, products: list[Product]) -> str:
    """Products HCG Store will sell. Each shows its announced price and availability, never an install action."""
    if not products:
        return ""
    items = []
    for p in products:
        c = p.commercial
        preview = (f'<p class="small"><span class="badge badge-warn">Preview</span> {e(PREVIEW_NOTICE)}</p>'
                   if c.listing != "approved" else "")
        items.append(f"""<li class="card dev-card commercial-card" data-commercial="{e(p.key)}">
  <div class="dev-head"><h3>{e(p.name)}</h3><span class="badge badge-muted">{e(c.availability)}</span></div>
  <p class="dev-kind">{e(CHANNEL_NAMES[c.release_channel])} · {e(c.category or p.kind)}</p>
  <p>{e(p.summary)}</p>
  {commercial_price_line(c)}
  {preview}
  <p class="small muted">{e(c.availability)} to HCG Store. {e(COMMERCIAL_NOT_READY)}</p>
  <div class="actions"><a class="btn btn-metal" href="products/{e(p.key)}/">Product page</a></div>
</li>""")
    return f"""<section id="coming-soon" class="section" aria-labelledby="coming-h">
<h2 id="coming-h">Coming soon to HCG Store</h2>
<p class="section-lead">Paid HCG products that are announced but not on sale yet. They cannot be bought or installed until HCG Store
is ready and the release is approved.</p>
<ul class="cards cards-dev" role="list">{''.join(items)}</ul>
</section>
"""


def commercial_product_page(cfg: RepoConfig, base: str, repo_url: str, p: Product, groups: dict) -> str:
    """products/<key>/ for a product HCG Store will sell. Purchase and install actions follow the owner-controlled
    commercial settings, never the visitor's browser: while purchases are disabled the page has no access check and
    a disabled purchase control; it offers installation only for the approved version in the main index; and it
    never points to a candidate (free demo) build or links a .deb."""
    c = p.commercial
    rel = "../../"
    store_url = cfg.store_product_url(p)
    installable = [pkg for pkg in p.package_ids if pkg in groups]
    preview = c.listing != "approved"
    yes_no = {"enabled": "Enabled", "disabled": "Disabled"}
    rows = [
        ("Publisher", e(c.publisher)),
        ("Category", e(c.category or p.kind)),
        ("Release channel", e(CHANNEL_NAMES[c.release_channel])),
        ("Price", f"{e(c.price_text)}{' | ' + e(c.price_note) if c.price_note else ''}"),
        ("Availability", e(c.availability)),
        ("Purchases", e(yes_no[c.purchases])),
        ("Installation", e(yes_no["enabled" if installable else "disabled"])),
        ("Package", ", ".join(f"<code>{e(x)}</code>" for x in p.package_ids)),
    ]
    if p.layouts:
        rows.append(("Planned layouts" if not installable else "Layouts",
                     ", ".join(f"{describe_arch(a)} (<code>{e(a)}</code>)" for a in p.layouts)))
    table = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in rows)
    desc = "".join(f"<p>{e(x)}</p>" for x in c.description)
    if installable:
        install = "".join(
            f"""<div class="actions actions-primary"><a class="btn btn-primary" href="sileo://package/{e(pkg)}">Install in Sileo</a>
<a class="btn btn-metal" href="{rel}{cfg.repo_subdir}/depictions/{e(pkg)}/">Details</a></div>""" for pkg in installable)
        install += ('<p class="hint">Sileo asks you to sign in with HCG Connect, and HCG Store confirms your purchase '
                    "before any download.</p>")
    else:
        install = (f'<div class="card note note-warn" role="note"><p><strong>{NO_DOWNLOADS}</strong> '
                   f'{e(COMMERCIAL_NO_INSTALL)}</p></div>')
    if c.store_page == "live":
        store_link = f'<a class="btn btn-metal" href="{e(store_url)}">HCG Store product page</a>'
        store_hint = "Nothing can be bought yet. The HCG Store page is informational until HCG Store opens sales."
    else:   # no Store page exists yet: a placeholder, never an invented URL
        store_link = f'<span class="btn btn-metal" aria-disabled="true">{e(STORE_PAGE_PENDING)}</span>'
        store_hint = "Nothing can be bought yet. HCG Store does not have a page for this product yet."
    if c.purchases == "enabled" and not preview:
        login_url = cfg.store_login_url(p)
        buy = f"""<section class="section-tight card store" aria-labelledby="store-h" data-store-access data-store-base="{e(cfg.store_base_url)}"
  data-access-url="{e(cfg.store_access_url(p))}" data-store-url="{e(store_url)}" data-purchases="enabled"
  data-repo-url="{e(repo_url)}" data-product-name="{e(p.name)}" data-access-level="paid">
<h2 id="store-h">Buy Product</h2>
<p class="muted">Purchases and access are handled by HCG Store and HCG Connect. HCG Repo never asks for your password.</p>
<div class="actions" data-store-actions>
  <a class="btn btn-primary" href="{e(login_url)}">Sign in to check access</a>
  <a class="btn btn-metal" href="{e(store_url)}">View in HCG Store</a>
</div>
<p class="hint" data-store-status></p>
</section>
"""
        scripts: tuple[str, ...] = ("store-access.js",)
    else:
        buy = f"""<section class="section-tight card store" aria-labelledby="store-h" data-purchases="disabled">
<h2 id="store-h">{e(c.availability)} to HCG Store</h2>
<p>{e(COMMERCIAL_NOT_READY)}</p>
<div class="actions">
  <button type="button" class="btn btn-metal" disabled aria-disabled="true">Purchase unavailable · {e(c.availability)}</button>
  {store_link}
</div>
<p class="hint">{store_hint}</p>
</section>
"""
        scripts = ()
    banner = (f'<div class="card note note-warn" role="note" data-preview><p><strong>Preview.</strong> {e(PREVIEW_NOTICE)}</p></div>'
              if preview else "")
    body = f"""
<article class="dep prose product commercial">
<p class="crumbs"><a href="{rel}">HCG Repo</a> <span aria-hidden="true">›</span> <a href="{rel}#coming-soon">Coming soon</a></p>
{banner}
<div class="dep-title card">
  <div>
    <h1>{e(p.name)}</h1>
    <p class="pkg-id"><span class="badge badge-muted">{e(c.availability)}</span>{access_badge("paid")} · {e(CHANNEL_NAMES[c.release_channel])}</p>
    <p class="lead">{e(p.summary)}</p>
    {commercial_price_line(c)}
  </div>
</div>
{desc}
<section class="section-tight" aria-labelledby="facts-h">
<h2 id="facts-h">Details</h2>
<dl class="kv card">{table}</dl>
</section>
{buy}<section class="section-tight" aria-labelledby="inst-h">
<h2 id="inst-h">Install</h2>
{install}
</section>
</article>"""
    return page(f"{p.name} · HCG Repo", body, rel, p.summary or p.name, f"{base}products/{p.key}/",
                scripts=scripts, noindex=preview)


def product_page(cfg: RepoConfig, base: str, repo_url: str, product: Product,
                 groups: dict[str, list[PoolPackage]], candidate: dict | None = None) -> str:
    """products/<key>/: what the product is, whether it can be installed, and its HCG Store actions.

    An install action appears only for a package that is in the generated index (public, or
    protected and indexed as cydia::commercial once a payment endpoint is live). A product page never
    links a .deb. The store actions are plain links; assets/store-access.js upgrades them once
    HCG Store says what this visitor may do."""
    p = product
    if p.commercial is not None:
        return commercial_product_page(cfg, base, repo_url, p, groups)
    rel = "../../"
    level = cfg.product_level(p)
    store_url = cfg.store_product_url(p) if p.in_store else ""
    login_url = cfg.store_login_url(p) if p.in_store else ""
    installable = [pkg for pkg in p.package_ids if pkg in groups]
    cand_groups = (candidate or {}).get("groups", {})
    in_candidate = [pkg for pkg in p.package_ids if pkg in cand_groups and pkg not in groups]
    pkgs = (", ".join(f"<code>{e(x)}</code>" for x in p.package_ids) if p.package_ids
            else "None. This product has no Debian package.")
    rows = [("Package", pkgs)]
    if p.layouts:
        rows.append(("Layouts" if installable else "Planned layouts",
                     ", ".join(f"{describe_arch(a)} (<code>{e(a)}</code>)" for a in p.layouts)))
    if level:
        rows.append(("Access", e(ACCESS_TEXT[level])))
    rows.append(("In HCG Repo", "In the package index" if installable else
                 "Testing channel only (HCG Repo Candidate)" if in_candidate else "Not in the package index"))
    table = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in rows)
    if installable:
        install = "".join(
            f"""<div class="actions actions-primary"><a class="btn btn-primary" href="sileo://package/{e(pkg)}">Install in Sileo</a>
<a class="btn btn-metal" href="{rel}{cfg.repo_subdir}/depictions/{e(pkg)}/">Details</a></div>""" for pkg in installable)
        install += ('<p class="hint">“Install in Sileo” opens the package once the HCG Repo source has been added.'
                    + (" Sileo asks you to sign in with HCG Connect first." if level in ("connect", "paid") else "") + "</p>")
    elif in_candidate:
        cand_url = candidate["url"]
        parts = []
        for pkg in in_candidate:
            v = cand_groups[pkg][0]
            dep = f"{rel}{cfg.repo_subdir}/{CANDIDATE_DIR}/depictions/{e(pkg)}/"
            parts.append(f"""{install_failure_html(v)}
<p>Version <strong>{e(v.info.version)}</strong> is in the <strong>testing channel</strong>, HCG Repo Candidate. It is not in the
stable HCG Repo source. {e(tests_text(v))}</p>
<div class="actions actions-primary"><a class="btn btn-metal" href="{dep}">Details and known issues</a>
<a class="btn btn-metal" href="{e('sileo://source/' + cand_url)}">Add HCG Repo Candidate</a></div>""")
        install = "".join(parts) + (f'<p class="hint">Testing channel: <code>{e(cand_url)}</code>. Betas there may fail to '
                                    "install or misbehave; read Known issues first.</p>")
    else:
        if not p.package_ids:
            reason = "This product has no Debian package, so it cannot be installed from HCG Repo."
        elif level in ("connect", "paid"):
            reason = ("It needs HCG Connect sign-in" + (" and HCG Store ownership" if level == "paid" else "")
                      + ". Authenticated delivery through Sileo is not live yet, and its package is never published "
                      "here as a public file.")
        else:
            reason = "No installable build has been published yet."
        install = f'<div class="card note note-warn" role="note"><p><strong>{NO_DOWNLOADS}</strong> {e(reason)}</p></div>'
    heading = "Buy Product" if level == "paid" else "Get access"
    store_section = f"""<section class="section-tight card store" aria-labelledby="store-h" data-store-access data-store-base="{e(cfg.store_base_url)}"
  data-access-url="{e(cfg.store_access_url(p))}" data-store-url="{e(store_url)}"
  data-repo-url="{e(repo_url)}" data-product-name="{e(p.name)}" data-access-level="{e(level)}">
<h2 id="store-h">{heading}</h2>
<p class="muted">Purchases and access are handled by HCG Store and HCG Connect. HCG Repo never asks for your password.</p>
<div class="actions" data-store-actions>
  <a class="btn btn-primary" href="{e(login_url)}">Sign in to check access</a>
  <a class="btn btn-metal" href="{e(store_url)}">View in HCG Store</a>
</div>
<p class="hint" data-store-status></p>
</section>
""" if p.in_store else ""
    body = f"""
<article class="dep prose product">
<p class="crumbs"><a href="{rel}">HCG Repo</a> <span aria-hidden="true">›</span> <a href="{rel}#{'sign-in-required' if p.restricted else 'in-development'}">Products</a></p>
<div class="dep-title card">
  <div>
    <h1>{e(p.name)}</h1>
    <p class="pkg-id"><span class="badge badge-muted">{e(p.status)}</span>{access_badge(level)}{f' · {e(p.kind)}' if p.kind else ''}</p>
    <p class="lead">{e(p.summary)}</p>
  </div>
</div>
{f'<p>{e(p.detail)}</p>' if p.detail else ''}
<section class="section-tight" aria-labelledby="facts-h">
<h2 id="facts-h">Details</h2>
<dl class="kv card">{table}</dl>
</section>
<section class="section-tight" aria-labelledby="inst-h">
<h2 id="inst-h">Install</h2>
{install}
</section>
{store_section}</article>"""
    return page(f"{p.name} · HCG Repo", body, rel, p.summary or p.name, f"{base}products/{p.key}/",
                scripts=("store-access.js",) if p.in_store else ())


# ---------------------------------------------------------------------------- build


def build(cfg: RepoConfig, base_url: str, out_dir: Path, sign_key: str | None = None,
          require_signature: bool | None = None, nested: bool = False, preview_listings: bool = False) -> BuildResult:
    """preview_listings also renders prepared commercial listings, each marked as an unpublished preview, writes
    PREVIEW_MARKER and sets noindex. verify refuses such a build, so it can be reviewed but never deployed."""
    from . import sign as signmod
    from . import verify as verifymod

    if preview_listings and not cfg.preview_listings:
        import dataclasses
        cfg = dataclasses.replace(cfg, preview_listings=True)

    repo_url = cfg.repo_url(base_url)
    report = Report("repository")
    pkgs = load_pool(cfg, report)
    check_repository(cfg, pkgs, report)
    if not report.ok:
        raise BuildError("repository validation failed", report)
    lock = load_lock(cfg)
    published = {e["filename"] for e in lock["packages"]
                 if e.get("status", "published") == "published" and entry_channel(e) == cfg.channel}
    pkgs = [p for p in pkgs if p.rel in published]       # only this channel's packages
    from .repo import protected_packages
    protected = protected_packages(cfg)
    if cfg.payment_endpoint and cfg.channel == "stable":
        # Indexed as cydia::commercial (files are never deployed), but only when a person approved this exact
        # file for public delivery in HCG Store (import-store-release). Otherwise Sileo would list a package that
        # the provider can never deliver; drafts, rejected and unapproved releases stay out of the index.
        # A commercial product's build also needs every owner-controlled commercial gate open, for exactly the
        # approved version (docs/COMMERCIAL.md). A prepared listing, disabled purchases or downloads, a pending
        # HCG Store or an unapproved release keep it out of the index.
        pkgs = pkgs + [p for p in protected if p.store_release and commercial_gate(cfg, p)]

    out_dir = Path(out_dir).resolve()
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=out_dir.name + ".building-", dir=out_dir.parent))
    try:
        repo_dir = tmp / cfg.repo_subdir
        (repo_dir / "debs").mkdir(parents=True)
        (tmp / "assets" / "brand").mkdir(parents=True)
        (tmp / ".nojekyll").write_text("")

        # APT metadata
        packages_text = render_packages(cfg, pkgs, repo_url)
        indexes = compress_indexes(packages_text)
        for name, data in indexes.items():
            (repo_dir / name).write_bytes(data)
        date, valid_until = build_info_date(), None
        if cfg.signing_valid_days > 0:
            # Replay protection: freshness is measured from the moment of this build, not the commit time
            # (SOURCE_DATE_EPOCH), otherwise an older commit would be published already close to expiry.
            from datetime import datetime, timedelta, timezone
            now = datetime.now(tz=timezone.utc).replace(microsecond=0)
            # APT refuses a Release whose Date is in the future (tested: 5 minutes ahead is refused), so Date is set
            # one hour back to tolerate devices whose clocks run slow.
            date = now - timedelta(hours=1)
            valid_until = now + timedelta(days=cfg.signing_valid_days)
            if not (sign_key or os.environ.get("HCG_REPO_SIGNING_KEY")):
                raise BuildError("[signing] valid_days needs a signed build: an unsigned Valid-Until protects nothing")
        (repo_dir / "Release").write_text(render_release(cfg, indexes, date, valid_until), encoding="utf-8")
        for p in pkgs:
            if not p.protected:
                shutil.copyfile(cfg.root / p.rel, repo_dir / "debs" / p.info.canonical_filename())
        if cfg.payment_endpoint:
            (repo_dir / "payment_endpoint").write_text(cfg.payment_endpoint.rstrip("/") + "/\n", encoding="utf-8")
        for icon in ("CydiaIcon.png", "CydiaIcon@2x.png", "CydiaIcon@3x.png"):
            shutil.copyfile(cfg.root / "branding" / icon, repo_dir / icon)
        # Sileo shows sileo-featured.json at the top of this source's page (not in the Sources list,
        # which only shows the icon). Images are drawn aspect-fill at itemSize, so itemSize is exactly
        # 3:1 like the banner (1200x400), and displayText is off so no title covers the lettering.
        (repo_dir / "assets").mkdir(exist_ok=True)
        shutil.copyfile(cfg.root / "branding" / "hcg-repo-banner-1200.jpg", repo_dir / "assets" / REPO_BANNER)
        featured = {"class": "FeaturedBannersView", "itemSize": "{330, 110}", "itemCornerRadius": 10,
                    "banners": [{"url": f"{repo_url}assets/{REPO_BANNER}", "title": "HCG Repo",
                                 "displayText": False, "hideShadow": True}]}
        (repo_dir / "sileo-featured.json").write_text(json.dumps(featured, indent=2) + "\n", encoding="utf-8")
        if cfg.publish_trust and cfg.channel == "stable":
            from . import trust
            (repo_dir / "trust.json").write_text(json.dumps(trust.trust_document(cfg, base_url), indent=2) + "\n",
                                                 encoding="utf-8")
        if cfg.publish_compatibility:
            # Off by default ([compatibility] publish in config/repo.toml): the owner decides when it goes public.
            from . import compat
            compat.write(cfg, repo_dir / "compatibility.json", channel=cfg.channel)

        # Per-package assets and depictions
        groups = _groups_from_index(pkgs, repo_dir / "Packages")
        for pkg, versions in groups.items():
            meta = versions[0].meta
            if meta.directory:
                dst = repo_dir / "assets" / pkg
                for a in (meta.icon, meta.header, *(s["file"] for s in meta.screenshots)):
                    if a:
                        dst.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(meta.directory / a, dst / a)
            ddir = repo_dir / "depictions" / pkg
            ddir.mkdir(parents=True)
            latest = [v for v in versions if v.info.version == versions[0].info.version]
            (ddir / "index.html").write_text(web_depiction(cfg, repo_url, latest, versions), encoding="utf-8")
            (ddir / "sileo.json").write_text(json.dumps(sileo_depiction(cfg, repo_url, latest, versions), indent=2) + "\n",
                                             encoding="utf-8")

        # Signing
        res = BuildResult(out_dir=out_dir, base_url=base_url, repo_url=repo_url, packages=pkgs, report=report)
        key = sign_key or os.environ.get("HCG_REPO_SIGNING_KEY") or ""
        must_sign = cfg.signing_required if require_signature is None else require_signature
        # Fail closed: once a production fingerprint is configured, unsigned output is never produced, whatever
        # the workflow input says. There is no silent fallback to unsigned publication.
        must_sign = must_sign or bool(cfg.signing_fingerprint)
        if key:
            sr = signmod.sign_release(repo_dir, key, cfg.root / cfg.public_key_file, cfg.signing_fingerprint)
            res.signed, res.signature_verified, res.signature_detail = sr.signed, sr.verified, sr.detail
            if not sr.verified:
                raise BuildError(f"signature verification failed: {sr.detail}")
        elif must_sign:
            raise BuildError("signing is required (config/repo.toml [signing] required = true, a configured fingerprint, "
                             "or --require-signature) but no signing key was given; refusing to build unsigned output")

        res.groups = groups
        # The candidate (testing) channel: an independent source built into <source>/candidate/ by every
        # ordinary build, so publishing never drops it. It is built from its own lock entries only.
        cand = None
        if cfg.channel == "stable" and not nested:
            ccfg = cfg.candidate()
            with tempfile.TemporaryDirectory(prefix="hcgrepo-candidate-") as ctmp:
                cres = build(ccfg, base_url, Path(ctmp) / "site", sign_key=sign_key, require_signature=require_signature,
                             nested=True)
                shutil.move(str(cres.out_dir / ccfg.repo_subdir), str(repo_dir / CANDIDATE_DIR))
            cand = {"url": ccfg.repo_url(base_url), "groups": cres.groups, "packages": cres.packages}
            (repo_dir / CANDIDATE_DIR / "index.html").write_text(candidate_index(cfg, ccfg, base_url, groups, cres.groups),
                                                                  encoding="utf-8")

        # Website
        revision = git_revision(cfg.root)
        fpr = cfg.signing_fingerprint
        prot_entries: dict[str, list[dict]] = {}
        for e_ in load_lock(cfg)["packages"]:
            if e_.get("status") == "protected":
                prot_entries.setdefault(e_["package"], []).append(e_)
        info = {
            "protected": prot_entries,
            "date_iso": date.isoformat(), "date_human": date.strftime("%d %b %Y, %H:%M UTC"), "revision": revision,
            "builds": len(pkgs),
            "signature": (f"Signed; verified at build time with gpgv against key {fpr[-16:]}" if res.signature_verified
                          else "Not signed: no archive key has been set up yet"),
            "signature_help": (f"Release files are signed with the HCG archive key {fpr}. Compare that fingerprint before trusting the key."
                               if res.signature_verified else
                               "This repository's metadata is not signed yet. Sileo accepts unsigned sources; every package "
                               "is still checked against the SHA-256 checksum in the index."),
        }
        for asset in ASSET_DIR.iterdir():
            if asset.is_file():
                shutil.copyfile(asset, tmp / "assets" / asset.name)
        for name in BRAND_FILES:
            shutil.copyfile(cfg.root / "branding" / name, tmp / "assets" / "brand" / name)
        shutil.copyfile(cfg.root / "branding" / "apple-touch-icon.png", tmp / "apple-touch-icon.png")
        (tmp / "index.html").write_text(landing(cfg, base_url, repo_url, groups, info, cand), encoding="utf-8")
        (repo_dir / "index.html").write_text(repo_index(cfg, repo_url), encoding="utf-8")
        for product in cfg.visible_products:    # a prepared commercial listing is not rendered (docs/COMMERCIAL.md)
            pdir = tmp / "products" / product.key
            pdir.mkdir(parents=True)
            (pdir / "index.html").write_text(product_page(cfg, base_url, repo_url, product, groups, cand), encoding="utf-8")
        (tmp / "404.html").write_text(page("Not found · HCG Repo", '<section class="section prose"><div class="intro card">'
                                           '<h1>Page not found</h1><p>The page you asked for does not exist.</p>'
                                           f'<p><a class="btn btn-primary" href="{e(base_url)}">Go to HCG Repo</a></p></div></section>',
                                           base_url, "Page not found", base_url), encoding="utf-8")
        build_info = {
            "builder": f"hcgrepo {__version__}", "revision": revision, "date": date.isoformat(),
            "base_url": base_url, "repo_url": repo_url, "packages": [
                {"package": p.info.package, "version": p.info.version, "architecture": p.info.architecture,
                 "sha256": p.info.sha256} for p in sorted(pkgs, key=lambda p: p.rel)],
            "signed": res.signed, "signature_verified": res.signature_verified, "signature_detail": res.signature_detail,
        }
        (tmp / "build-info.json").write_text(json.dumps(build_info, indent=2) + "\n", encoding="utf-8")

        # Containment: no protected package may appear as a file or an install link in the public output.
        prot_names = {p.info.canonical_filename() for p in protected}
        leaked = [str(f.relative_to(tmp)) for f in tmp.rglob("*") if f.name in prot_names]
        if leaked:
            raise BuildError(f"protected package files in the public output: {leaked}")
        if not cfg.payment_endpoint:
            home = (tmp / "index.html").read_text(encoding="utf-8")
            # A package ID may have an approved public build (e.g. a free candidate beta of a paid tweak) next
            # to protected releases; its install link then opens the public build. Only an install link for a
            # package with no public build in this output exposes a protected one.
            public_ids = {p.info.package for p in pkgs}
            if cand:   # the home page also lists the candidate channel's public builds
                public_ids |= {p.info.package for p in cand["packages"]}
            exposed = sorted({p.info.package for p in protected
                              if p.info.package not in public_ids and f"sileo://package/{p.info.package}" in home})
            if exposed:
                raise BuildError(f"install links for protected packages without a payment endpoint: {exposed}")

        # Commercial gate (docs/COMMERCIAL.md): a commercial product's build is in the main index only when every
        # owner-controlled gate is open, for the approved version. Checked again here, after the index is written.
        if cfg.channel == "stable":
            gated = sorted({f"{p.info.package} {p.info.version}" for p in pkgs if not commercial_gate(cfg, p)})
            if gated:
                raise BuildError(f"commercial packages in the main index without an approved, activated release: {gated}")
        if cfg.preview_listings and not nested:
            (tmp / PREVIEW_MARKER).write_text("Preview of prepared commercial listings. Never deploy this build.\n",
                                              encoding="utf-8")

        # Channel isolation: the stable index and stable debs/ never carry a candidate build.
        if cand:
            stable_keys = {(p.info.package, p.info.version, p.info.architecture) for p in pkgs}
            cand_keys = {(p.info.package, p.info.version, p.info.architecture) for p in cand["packages"]}
            if stable_keys & cand_keys:
                raise BuildError(f"the same build is in both channels: {sorted(stable_keys & cand_keys)}")
            stable_debs = {f.name for f in (repo_dir / "debs").iterdir()}
            leaked_c = sorted(stable_debs & {p.info.canonical_filename() for p in cand["packages"]})
            if leaked_c:
                raise BuildError(f"candidate package files in the stable debs/: {leaked_c}")

        # Verify the finished tree offline before it replaces anything.
        vr = verifymod.verify(base_url, tmp.as_uri() + "/", cfg=cfg,
                              pubkey=(cfg.root / cfg.public_key_file) if res.signed else None,
                              site_checks=not nested, allow_preview=cfg.preview_listings)
        if not vr.report.ok:
            raise BuildError("the generated snapshot failed verification", vr.report)
        report.extend(vr.report)
        if cand:
            cv = verifymod.verify(base_url, tmp.as_uri() + "/", cfg=cfg.candidate(),
                                  pubkey=(cfg.root / cfg.public_key_file) if res.signed else None, site_checks=False)
            if not cv.report.ok:
                raise BuildError("the candidate source failed verification", cv.report)
            report.extend(cv.report)

        if out_dir.exists():
            old = out_dir.with_name(out_dir.name + ".old")
            if old.exists():
                shutil.rmtree(old)
            os.replace(out_dir, old)
            os.replace(tmp, out_dir)
            shutil.rmtree(old)
        else:
            os.replace(tmp, out_dir)
        res.files = sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file())
        return res
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _groups_from_index(pkgs: list[PoolPackage], packages_file: Path) -> dict[str, list[PoolPackage]]:
    """Only packages that are actually in the generated index get a card or depiction."""
    stanzas = debian.parse_stanzas(packages_file.read_text(encoding="utf-8"))
    indexed = {(debian.field(s, "Package"), debian.field(s, "Version"), debian.field(s, "Architecture")) for s in stanzas}
    return latest_by_package([p for p in pkgs if p.info.key in indexed])

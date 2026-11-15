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
from .config import Product, Report, RepoConfig, load_lock
from .repo import (PoolPackage, build_info_date, check_repository, compress_indexes, git_revision,
                   load_pool, render_packages, render_release)

TINT = "#2FD3E6"
ASSET_DIR = Path(__file__).parent / "assets"
SOURCE_REPO = "https://github.com/F1r3srp3nt/HCG-repo"
BANNER_WIDTHS = (800, 1200, 2000)
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
        return short, f"iOS {lo} or later, declared by the package" + (f"; firmware {hi}" if hi else "") + "."
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


def passing_tests(p: PoolPackage) -> list[dict]:
    return [t for t in p.meta.device_tests(p.info.version) if t.get("result") == "pass"]


def tests_text(p: PoolPackage) -> str:
    tests = passing_tests(p)
    if not tests:
        return "Not yet tested on a physical device."
    return "Tested on " + "; ".join(f"{t['device']}, iOS {t['ios']}, {t['jailbreak']} ({t['date']})" for t in tests) + "."


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
         scripts: tuple[str, ...] = ()) -> str:
    extra = "".join(f'\n<script src="{rel}assets/{e(name)}" defer></script>' for name in scripts)

    def nav(href: str, label: str) -> str:
        cur = ' aria-current="page"' if current == label else ""
        return f'<a href="{rel}{href}"{cur}>{label}</a>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#05080b">
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


def source_actions(repo_url: str, rel: str = "") -> str:
    sileo = f"sileo://source/{repo_url}"
    zebra = f"zbra://sources/add/{repo_url}"
    return f"""<div class="source">
  <div class="actions actions-primary">
    <a class="btn btn-primary" href="{e(sileo)}">Add to Sileo</a>
    <a class="btn btn-metal" href="{e(zebra)}">Add to Zebra</a>
  </div>
  <p class="source-label" id="source-url-label">Repository URL</p>
  <div class="source-row">
    <code id="source-url" class="source-url" aria-labelledby="source-url-label">{url_breaks(repo_url)}</code>
    <button type="button" class="btn btn-metal btn-copy" data-copy="#source-url">Copy URL</button>
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
    badge = status_badge("device-tested" if statuses == {"device-tested"} else "experimental")
    detail = f"{repo_rel}depictions/{e(pkg)}/"
    product = cfg.product_for_package(pkg)
    product_link = f'\n    <a class="btn btn-metal" href="products/{e(product.key)}/">Product page</a>' if product else ""
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
  <p class="pkg-tested">{e(tests_text(p))}</p>
  <div class="actions">
    <a class="btn btn-primary" href="sileo://package/{e(pkg)}">Install in Sileo</a>
    <a class="btn btn-metal" href="{detail}">Details</a>{product_link}
  </div>
</li>"""


def landing(cfg: RepoConfig, base: str, repo_url: str, groups: dict[str, list[PoolPackage]], info: dict) -> str:
    n = len(groups)
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
    <p>The source is live and valid, so you can add it now and Sileo will refresh it without errors. Packages appear here as soon as a validated build is published. Projects still being built are listed under <a href="#in-development">In development</a>.</p>
  </div>
</div>"""

    indexed = set(groups)
    planned = [p for p in cfg.products if not (set(p.package_ids) & indexed)]
    dev_items = "".join(f"""<li class="card dev-card">
  <div class="dev-head"><h3>{e(p.name)}</h3><span class="badge badge-muted">{e(p.status)}</span></div>
  {f'<p class="dev-kind">{e(p.kind)}</p>' if p.kind else ''}
  <p>{e(p.summary)}</p>
  {f'<p class="small muted">{e(p.detail)}</p>' if p.detail else ''}
  <div class="actions"><a class="btn btn-metal" href="products/{e(p.key)}/">Product page</a></div>
</li>""" for p in planned)
    dev_html = f"""<section id="in-development" class="section" aria-labelledby="dev-h">
<h2 id="dev-h">In development</h2>
<p class="section-lead">Not in the repository index and not installable yet. Nothing here has an install action.</p>
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


# ---------------------------------------------------------------------------- package pages


DEFAULT_INSTALL = """1. Add the HCG Repo source to Sileo.
2. Open this package in Sileo, review the dependencies it lists, and install."""
DEFAULT_REMOVAL = """Sileo → *Installed* → the package → *Remove*."""


def web_depiction(cfg: RepoConfig, repo_url: str, versions: list[PoolPackage], all_versions: list[PoolPackage]) -> str:
    p = versions[0]
    pkg = p.info.package
    summary, long = short_description(p)
    rel = "../../../"
    icon = (f'<img src="../../assets/{e(pkg)}/{e(p.meta.icon)}" alt="" width="88" height="88" class="pkg-icon pkg-icon-lg">'
            if p.meta.icon else "")
    header = (f'<div class="dep-header-frame"><img class="dep-header" src="../../assets/{e(pkg)}/{e(p.meta.header)}" alt=""></div>'
              if p.meta.header else "")
    ios_short, ios_long = ios_requirement(p)
    builds_rows = "".join(
        f"""<tr><td>{describe_arch(v.info.architecture)} <code>{e(v.info.architecture)}</code></td>
<td>{status_badge(v.meta.status(v.info.version))}</td>
<td><a href="../../debs/{e(v.info.canonical_filename())}" download>.deb</a> <span class="muted">{v.info.size:,} bytes</span></td></tr>"""
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
        f"""<li><a href="../../debs/{e(v.info.canonical_filename())}" download>{e(v.info.canonical_filename())}</a>
<span class="small muted">{v.info.size:,} bytes</span><br>
<span class="small">SHA-256 <code class="hash">{v.info.sha256}</code></span><br>
<span class="small muted">Source: {provenance_html(provenance(cfg, v))}</span></li>""" for v in all_versions)
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
    <p class="pkg-id"><code>{e(pkg)}</code> · version <strong>{e(p.info.version)}</strong> · {status_badge(p.meta.status(p.info.version))}</p>
    <p class="lead">{e(p.meta.summary or summary)}</p>
  </div>
</div>
<div class="actions actions-primary">
  <a class="btn btn-primary" href="sileo://package/{e(pkg)}">Install in Sileo</a>
  <a class="btn btn-metal" href="sileo://source/{e(repo_url)}">Add source first</a>
</div>
<p class="hint">“Install in Sileo” opens this package once the HCG Repo source has been added.</p>

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
    status = "Device-tested" if p.meta.status(p.info.version) == "device-tested" else "Experimental"
    _, ios_long = ios_requirement(p)
    about = "\n\n".join(x for x in (p.meta.summary or summary, long if long != summary else "", p.meta.notes) if x)
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
    if p.meta.header:
        dep["headerImage"] = f"{repo_url}assets/{pkg}/{p.meta.header}"
    return dep


def repo_index(cfg: RepoConfig, repo_url: str) -> str:
    body = f"""<section class="section prose">
<div class="intro card">
<p class="eyebrow">APT source root</p>
<h1>HCG Repo source</h1>
<p>Add this address to Sileo or another package manager; it is not meant to be browsed.</p>
{source_actions(repo_url)}
<p><a href="../">Go to the HCG Repo page</a></p>
</div>
</section>"""
    return page("HCG Repo source", body, "../", "HCG Repo APT source root.", repo_url)


# ---------------------------------------------------------------------------- product pages

ACCESS_TEXT = {
    "public": "Public. No sign-in is needed to install.",
    "login-required": "Sign-in required. Access is granted through HCG Store with an HCG account.",
    "paid": "Paid. Bought and granted through HCG Store.",
}
NO_DOWNLOADS = "Downloads are not available yet."


def product_installable(product: Product, groups: dict[str, list[PoolPackage]]) -> list[str]:
    """Package IDs of `product` that anyone can install from this repository right now."""
    if not product.public:
        return []
    return [pkg for pkg in product.package_ids if pkg in groups]


def product_page(cfg: RepoConfig, base: str, repo_url: str, product: Product,
                 groups: dict[str, list[PoolPackage]]) -> str:
    """products/<key>/: what the product is, whether it can be installed, and its HCG Store actions.

    Only a `public` product with an indexed package gets an install link. The store actions are
    plain links; assets/store-access.js upgrades them once HCG Store says what this visitor may do."""
    p = product
    rel = "../../"
    store_url = cfg.store_product_url(p)
    login_url = cfg.store_login_url(p)
    installable = product_installable(p, groups)
    pkgs = (", ".join(f"<code>{e(x)}</code>" for x in p.package_ids) if p.package_ids
            else "None. This product has no Debian package.")
    rows = [("Package", pkgs)]
    if p.layouts:
        rows.append(("Layouts" if installable else "Planned layouts",
                     ", ".join(f"{describe_arch(a)} (<code>{e(a)}</code>)" for a in p.layouts)))
    rows.append(("Access", e(ACCESS_TEXT[p.access])))
    rows.append(("In HCG Repo", "In the package index" if installable else "Not in the package index"))
    table = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in rows)
    if installable:
        install = "".join(
            f"""<div class="actions actions-primary"><a class="btn btn-primary" href="sileo://package/{e(pkg)}">Install in Sileo</a>
<a class="btn btn-metal" href="{rel}{cfg.repo_subdir}/depictions/{e(pkg)}/">Details</a></div>""" for pkg in installable)
        install += '<p class="hint">“Install in Sileo” opens the package once the HCG Repo source has been added.</p>'
    else:
        if not p.package_ids:
            reason = "This product has no Debian package, so it cannot be installed from HCG Repo."
        elif p.public:
            reason = "No package has been published yet."
        else:
            reason = ("Its packages " + ("are sold through HCG Store" if p.access == "paid" else "require signing in to HCG Store")
                      + ". HCG Repo publishes only packages that anyone may download, and protected downloads through "
                      "Sileo are not implemented yet.")
        install = f'<div class="card note note-warn" role="note"><p><strong>{NO_DOWNLOADS}</strong> {e(reason)}</p></div>'
    body = f"""
<article class="dep prose product">
<p class="crumbs"><a href="{rel}">HCG Repo</a> <span aria-hidden="true">›</span> <a href="{rel}#in-development">Products</a></p>
<div class="dep-title card">
  <div>
    <h1>{e(p.name)}</h1>
    <p class="pkg-id"><span class="badge badge-muted">{e(p.status)}</span>{f' · {e(p.kind)}' if p.kind else ''}</p>
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
<section class="section-tight card store" aria-labelledby="store-h" data-store-access data-store-base="{e(cfg.store_base_url)}"
  data-access-url="{e(cfg.store_access_url(p))}" data-store-url="{e(store_url)}"
  data-repo-url="{e(repo_url)}" data-product-name="{e(p.name)}">
<h2 id="store-h">Buy Product</h2>
<p class="muted">Purchases and access are handled by HCG Store. HCG Repo never asks for your password.</p>
<div class="actions" data-store-actions>
  <a class="btn btn-primary" href="{e(login_url)}">Sign in to check access</a>
  <a class="btn btn-metal" href="{e(store_url)}">View in HCG Store</a>
</div>
<p class="hint" data-store-status></p>
</section>
</article>"""
    return page(f"{p.name} · HCG Repo", body, rel, p.summary or p.name, f"{base}products/{p.key}/",
                scripts=("store-access.js",))


# ---------------------------------------------------------------------------- build


def build(cfg: RepoConfig, base_url: str, out_dir: Path, sign_key: str | None = None,
          require_signature: bool | None = None) -> BuildResult:
    from . import sign as signmod
    from . import verify as verifymod

    repo_url = cfg.repo_url(base_url)
    report = Report("repository")
    pkgs = load_pool(cfg, report)
    check_repository(cfg, pkgs, report)
    if not report.ok:
        raise BuildError("repository validation failed", report)
    lock = load_lock(cfg)
    published = {e["filename"] for e in lock["packages"] if e.get("status", "published") == "published"}
    pkgs = [p for p in pkgs if p.rel in published]

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
        date = build_info_date()
        (repo_dir / "Release").write_text(render_release(cfg, indexes, date), encoding="utf-8")
        for p in pkgs:
            shutil.copyfile(cfg.root / p.rel, repo_dir / "debs" / p.info.canonical_filename())
        for icon in ("CydiaIcon.png", "CydiaIcon@2x.png", "CydiaIcon@3x.png"):
            shutil.copyfile(cfg.root / "branding" / icon, repo_dir / icon)

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
        if key:
            sr = signmod.sign_release(repo_dir, key, cfg.root / cfg.public_key_file, cfg.signing_fingerprint)
            res.signed, res.signature_verified, res.signature_detail = sr.signed, sr.verified, sr.detail
            if not sr.verified:
                raise BuildError(f"signature verification failed: {sr.detail}")
        elif must_sign:
            raise BuildError("signing is required (config/repo.toml [signing] required = true) but no key was given")

        # Website
        revision = git_revision(cfg.root)
        fpr = cfg.signing_fingerprint
        info = {
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
        (tmp / "index.html").write_text(landing(cfg, base_url, repo_url, groups, info), encoding="utf-8")
        (repo_dir / "index.html").write_text(repo_index(cfg, repo_url), encoding="utf-8")
        for product in cfg.products:
            pdir = tmp / "products" / product.key
            pdir.mkdir(parents=True)
            (pdir / "index.html").write_text(product_page(cfg, base_url, repo_url, product, groups), encoding="utf-8")
        if cfg.payment_endpoint:
            # Sileo reads <source>/payment_endpoint. Written only once HCG Store's provider is live.
            (repo_dir / "payment_endpoint").write_text(cfg.payment_endpoint, encoding="utf-8")
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

        # Verify the finished tree offline before it replaces anything.
        vr = verifymod.verify(base_url, tmp.as_uri() + "/", cfg=cfg,
                              pubkey=(cfg.root / cfg.public_key_file) if res.signed else None)
        if not vr.report.ok:
            raise BuildError("the generated snapshot failed verification", vr.report)
        report.extend(vr.report)

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

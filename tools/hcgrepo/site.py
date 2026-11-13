"""Build the complete static snapshot: APT repository + depictions + website.

Everything is written into a fresh temporary directory, verified, and only then moved into place,
so the published indexes and package files always come from the same build.
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
from .config import Report, RepoConfig, load_lock
from .repo import (PoolPackage, build_info_date, check_repository, compress_indexes, git_revision,
                   load_pool, render_packages, render_release)

TINT = "#2FD3E6"
ASSET_DIR = Path(__file__).parent / "assets"


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


# ---------------------------------------------------------------------------- helpers

e = html.escape


def md_inline(text: str) -> str:
    out = e(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])", r"<em>\1</em>", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', out)
    return out


def md_block(text: str) -> str:
    """Tiny, safe Markdown subset: paragraphs, '-' lists, '###' headings, inline code/bold/links."""
    out, para, items = [], [], []

    def flush():
        if para:
            out.append(f"<p>{md_inline(' '.join(para))}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{md_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("### "):
            flush()
            out.append(f"<h4>{md_inline(s[4:])}</h4>")
        elif s.startswith(("- ", "* ")):
            if para:
                flush()
            items.append(s[2:])
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


def compat_text(p: PoolPackage) -> str:
    lo, hi = debian.firmware_bounds(p.info.get("Depends"))
    parts = []
    if lo:
        parts.append(f"iOS {lo} or later")
    if hi:
        parts.append(f"firmware {hi}")
    return ", ".join(parts) + " (declared by the package)" if parts else "No iOS version range declared"


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


def tests_text(p: PoolPackage) -> str:
    tests = [t for t in p.meta.device_tests(p.info.version) if t.get("result") == "pass"]
    if not tests:
        return "Not yet tested on a device."
    return "Tested on: " + "; ".join(f"{t['device']}, iOS {t['ios']}, {t['jailbreak']} ({t['date']})" for t in tests)


# ---------------------------------------------------------------------------- page shell


def page(title: str, body: str, rel: str, description: str, canonical: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#05080B">
<meta name="description" content="{e(description)}">
<title>{e(title)}</title>
<link rel="canonical" href="{e(canonical)}">
<link rel="icon" href="{rel}repo/CydiaIcon.png">
<link rel="apple-touch-icon" href="{rel}repo/CydiaIcon@3x.png">
<link rel="stylesheet" href="{rel}assets/site.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="topbar">
  <a class="brand" href="{rel}"><img src="{rel}repo/CydiaIcon@2x.png" alt="" width="36" height="36"><span>HCG Repo</span></a>
  <nav aria-label="Primary"><a href="{rel}#packages">Packages</a><a href="{rel}#install">Install</a><a href="{rel}#help">Help</a></nav>
</header>
<main id="main">
{body}
</main>
<footer class="footer">
  <p>HCG Repo · Hakmi Capital Group · <a href="https://github.com/F1r3srp3nt/HCG-repo">Repository source</a></p>
</footer>
<div class="sr-only" role="status" aria-live="polite" id="live"></div>
<script src="{rel}assets/site.js" defer></script>
</body>
</html>
"""


def source_box(repo_url: str) -> str:
    sileo = f"sileo://source/{repo_url}"
    zebra = f"zbra://sources/add/{repo_url}"
    return f"""<div class="source">
  <label for="source-url">Repository URL</label>
  <div class="source-row">
    <input id="source-url" class="source-url" value="{e(repo_url)}" readonly spellcheck="false" autocomplete="off">
    <button type="button" class="btn btn-ghost" data-copy="#source-url">Copy URL</button>
  </div>
  <div class="actions">
    <a class="btn btn-primary" href="{e(sileo)}">Add to Sileo</a>
    <a class="btn btn-ghost" href="{e(zebra)}">Add to Zebra</a>
  </div>
  <p class="hint">Open this page on your iPhone or iPad. “Add to Sileo” opens Sileo’s Add Source prompt with this URL filled in; you confirm it there.</p>
</div>"""


# ---------------------------------------------------------------------------- landing page


def landing(cfg: RepoConfig, base: str, repo_url: str, groups: dict[str, list[PoolPackage]],
            info: dict) -> str:
    cards = []
    for pkg, versions in groups.items():
        cards.append(package_card(versions, rel="", repo_rel="repo/"))
    if cards:
        packages_html = '<ul class="cards" role="list">' + "".join(cards) + "</ul>"
    else:
        packages_html = ('<div class="empty"><p><strong>No packages are published yet.</strong></p>'
                         "<p>The repository is live and valid but empty. Packages appear here only after a real, "
                         "validated build has been imported. Nothing below the “Planned” heading can be installed.</p></div>")

    indexed = set(groups)
    planned = [p for p in cfg.products if not (set(p.get("package_ids", [])) & indexed)]
    planned_html = ""
    if planned:
        items = "".join(
            f"""<li class="card card-planned">
  <div class="card-head"><h3>{e(p['name'])}</h3><span class="badge badge-muted">{e(p.get('status', 'Not available'))}</span></div>
  <p>{e(p.get('summary', ''))}</p>
  {f'<p class="small">{e(p["detail"])}</p>' if p.get('detail') else ''}
  <p class="small muted">Not available for installation.</p>
</li>""" for p in planned)
        planned_html = f"""<section aria-labelledby="planned-h" class="section">
<h2 id="planned-h">Planned</h2>
<p class="muted">These are not in the repository index and cannot be installed yet.</p>
<ul class="cards" role="list">{items}</ul>
</section>"""

    archs = ", ".join(f"{describe_arch(a)} <code>{e(a)}</code>" for a in cfg.architectures)
    sig = info["signature"]
    body = f"""
<section class="hero" aria-labelledby="hero-h">
  <img class="hero-mark" src="repo/CydiaIcon@3x.png" alt="HCG logo" width="96" height="96">
  <div>
    <p class="eyebrow">Hakmi Capital Group</p>
    <h1 id="hero-h">HCG Repo</h1>
    <p class="lead">Jailbreak apps, tweaks and command-line tools by HCG, distributed through Sileo and compatible APT package managers.</p>
  </div>
</section>
{source_box(repo_url)}
<dl class="facts">
  <div><dt>Packages</dt><dd>{len(groups)}</dd></div>
  <div><dt>Layouts</dt><dd>{archs}</dd></div>
  <div><dt>Metadata signature</dt><dd>{e(sig)}</dd></div>
  <div><dt>Built</dt><dd><time datetime="{e(info['date_iso'])}">{e(info['date_human'])}</time></dd></div>
</dl>

<section id="packages" aria-labelledby="packages-h" class="section">
<h2 id="packages-h">Available packages</h2>
{packages_html}
</section>
{planned_html}

<section id="install" aria-labelledby="install-h" class="section prose">
<h2 id="install-h">Installation</h2>
<ol>
  <li>On the jailbroken device, open this page in Safari and tap <strong>Add to Sileo</strong>, or in Sileo go to <em>Sources → +</em> and enter <code>{e(repo_url)}</code>.</li>
  <li>Confirm the source and let Sileo refresh. The source should appear as “{e(cfg.origin)}”.</li>
  <li>Find the package under the source, review its dependencies on the confirmation screen, and install.</li>
</ol>
<h3>Rootless and rootful</h3>
<p>Each package declares one architecture. Rootless jailbreaks (files under <code>/var/jb</code>) use <code>iphoneos-arm64</code>; rootful jailbreaks use <code>iphoneos-arm</code>. Sileo lists only packages whose architecture matches your device, so you will not be offered a build for the other layout. Roothide (<code>iphoneos-arm64e</code>) is not supported by this repository.</p>
<h3>Other package managers</h3>
<p>Zebra: <em>Sources → Add</em>, or the “Add to Zebra” button above. Command line (APT on the device):</p>
<pre><code>deb {e(repo_url)} ./</code></pre>
<p>Put that line in a file under <code>/var/jb/etc/apt/sources.list.d/</code> (rootless) or <code>/etc/apt/sources.list.d/</code> (rootful), then run <code>apt update</code>.</p>
<p class="small muted">Labels: <span class="badge badge-warn">Experimental</span> means the package passed repository validation but has no recorded device test. <span class="badge badge-ok">Device-tested</span> means at least one physical-device test is recorded for that exact version, with the device, iOS version and jailbreak shown.</p>
</section>

<section id="help" aria-labelledby="help-h" class="section prose">
<h2 id="help-h">Troubleshooting</h2>
<details><summary>Sileo cannot refresh the source</summary>
<p>Check the URL ends in <code>/{e(cfg.repo_subdir)}/</code> exactly as shown above, including the trailing slash, and that the device has network access. Pull down to refresh again. If an error persists, note the message Sileo shows and report it.</p></details>
<details><summary>“Add to Sileo” does nothing</summary>
<p>The button only works on a device where Sileo is installed. Otherwise copy the URL and add it in your package manager manually.</p></details>
<details><summary>A package is missing on my device</summary>
<p>Packages are built for one layout. A rootless package is hidden on rootful devices and vice versa. Packages may also require a minimum iOS version, shown on each card.</p></details>
<details><summary>An installed app does not appear on the home screen</summary>
<p>Run <code>uicache -a</code> from a terminal (it is part of <code>uikittools</code>), or respring.</p></details>
<details><summary>HCG Terminal Unleashed says “Permission denied” when starting a shell</summary>
<p>That is what a sandboxed install (Xcode, TestFlight, an IPA) reports. Installing the jailbreak package does not change iOS security globally; it installs a separately signed copy of the app outside the app sandbox, where your jailbreak allows that. See the package page for exact requirements once it is published.</p></details>
<details><summary>Signature warnings</summary>
<p>{e(info['signature_help'])} Do not turn off signature checking globally in APT to work around a problem with any repository.</p></details>

<h2>Removing packages and the source</h2>
<ul>
  <li><strong>Remove a package:</strong> Sileo → <em>Installed</em> → the package → <em>Remove</em> → confirm. The package manager removes the files the package installed and runs its removal scripts. Settings or documents an app stored in its own data folders may remain.</li>
  <li><strong>Remove the source:</strong> Sileo → <em>Sources</em> → swipe left on “{e(cfg.origin)}” → <em>Delete</em>. Installed packages stay installed but will no longer receive updates from here.</li>
</ul>
</section>
"""
    return page("HCG Repo", body, "", "HCG Repo: jailbreak apps, tweaks and tools by Hakmi Capital Group for Sileo.", base)


def package_card(versions: list[PoolPackage], rel: str, repo_rel: str) -> str:
    p = versions[0]
    summary, _ = short_description(p)
    pkg = p.info.package
    icon = (f'<img src="{repo_rel}assets/{e(pkg)}/{e(p.meta.icon)}" alt="" width="56" height="56" class="pkg-icon">'
            if p.meta.icon else '<span class="pkg-icon pkg-icon-blank" aria-hidden="true"></span>')
    builds = "".join(
        f"""<li><span class="badge badge-arch">{describe_arch(v.info.architecture)}</span> <code>{e(v.info.architecture)}</code>
 · {status_badge(v.meta.status(v.info.version))}
 · <a href="{repo_rel}debs/{e(v.info.canonical_filename())}" download>.deb</a> <span class="small muted">{v.info.size:,} bytes</span></li>"""
        for v in versions if v.info.version == p.info.version)
    return f"""<li class="card">
  <div class="card-head">{icon}<div><h3>{e(display_name(p))}</h3><p class="small muted"><code>{e(pkg)}</code> · version <strong>{e(p.info.version)}</strong></p></div></div>
  <p>{e(summary)}</p>
  <ul class="builds" role="list">{builds}</ul>
  <p class="small">{e(compat_text(p))}. {e(tests_text(p))}</p>
  <div class="actions">
    <a class="btn btn-primary" href="sileo://package/{e(pkg)}">Install in Sileo</a>
    <a class="btn btn-ghost" href="{repo_rel}depictions/{e(pkg)}/">Details &amp; changelog</a>
  </div>
  <p class="hint">“Install in Sileo” opens the package once this source has been added.</p>
</li>"""


# ---------------------------------------------------------------------------- depictions


def web_depiction(cfg: RepoConfig, repo_url: str, versions: list[PoolPackage], all_versions: list[PoolPackage]) -> str:
    p = versions[0]
    pkg = p.info.package
    summary, long = short_description(p)
    header = (f'<img class="dep-header" src="../../assets/{e(pkg)}/{e(p.meta.header)}" alt="">' if p.meta.header else "")
    rows = [
        ("Package", f"<code>{e(pkg)}</code>"), ("Version", e(p.info.version)),
        ("Builds", ", ".join(f"{describe_arch(v.info.architecture)} (<code>{e(v.info.architecture)}</code>)" for v in versions if v.info.version == p.info.version)),
        ("Compatibility", e(compat_text(p))),
        ("Status", status_badge(p.meta.status(p.info.version)) + " " + e(tests_text(p))),
        ("Depends", f"<code>{e(p.info.get('Depends') or 'nothing')}</code>"),
        ("Section", e(p.info.get("Section") or "—")),
        ("Maintainer", e(p.info.get("Maintainer") or "—")),
    ]
    table = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in rows)
    seen_versions = []
    for v in all_versions:
        if v.info.version not in seen_versions:
            seen_versions.append(v.info.version)
    changes = []
    for ver in seen_versions:
        text = p.meta.changelog.get(ver)
        vp = next(v for v in all_versions if v.info.version == ver)
        changes.append(f"<article class=\"change\"><h3>{e(ver)} {status_badge(vp.meta.status(ver))}</h3>"
                       f"{md_block(text) if text else '<p class=muted>No changelog entry.</p>'}</article>")
    downloads = "".join(
        f"<li><a href=\"../../debs/{e(v.info.canonical_filename())}\" download>{e(v.info.canonical_filename())}</a>"
        f"<br><span class=\"small muted\">SHA-256 <code class=\"hash\">{v.info.sha256}</code></span></li>" for v in all_versions)
    notes = md_block(p.meta.notes) if p.meta.notes else ""
    body = f"""
{header}
<article class="section prose">
<p><a href="../../../">← HCG Repo</a></p>
<h1>{e(display_name(p))}</h1>
<p class="lead">{e(summary)}</p>
{md_block(long) if long else ''}
{notes}
<dl class="facts facts-table">{table}</dl>
<div class="actions"><a class="btn btn-primary" href="sileo://package/{e(pkg)}">Install in Sileo</a></div>
<h2>Changelog</h2>
{''.join(changes)}
<h2>Downloads</h2>
<ul class="downloads">{downloads}</ul>
</article>"""
    return page(f"{display_name(p)} · HCG Repo", body, "../../../", summary,
                f"{repo_url}depictions/{pkg}/")


def sileo_depiction(cfg: RepoConfig, repo_url: str, versions: list[PoolPackage], all_versions: list[PoolPackage]) -> dict:
    p = versions[0]
    pkg = p.info.package
    summary, long = short_description(p)
    status = "Device-tested" if p.meta.status(p.info.version) == "device-tested" else "Experimental"
    details = [
        {"class": "DepictionMarkdownView", "markdown": "\n\n".join(x for x in (summary, long, p.meta.notes) if x), "useSpacing": True},
        {"class": "DepictionSeparatorView"},
        {"class": "DepictionHeaderView", "title": "Information"},
        {"class": "DepictionTableTextView", "title": "Version", "text": p.info.version},
        {"class": "DepictionTableTextView", "title": "Status", "text": status},
        {"class": "DepictionTableTextView", "title": "Layout",
         "text": ", ".join(describe_arch(v.info.architecture) for v in versions if v.info.version == p.info.version)},
        {"class": "DepictionTableTextView", "title": "Compatibility", "text": compat_text(p)},
        {"class": "DepictionMarkdownView", "markdown": tests_text(p), "useSpacing": True},
        {"class": "DepictionSeparatorView"},
        {"class": "DepictionTableButtonView", "title": "Web page and downloads", "action": f"{repo_url}depictions/{pkg}/"},
    ]
    seen, changes = [], []
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
<h1>HCG Repo source</h1>
<p>This is the APT source root. Add it to Sileo or another package manager; it is not meant to be browsed.</p>
{source_box(repo_url)}
<p><a href="../">Back to the HCG Repo page</a></p>
</section>"""
    return page("HCG Repo source", body, "../", "HCG Repo APT source root.", repo_url)


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
        (tmp / "assets").mkdir()
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
                for a in (meta.icon, meta.header):
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
        info = {
            "date_iso": date.isoformat(), "date_human": date.strftime("%d %b %Y, %H:%M UTC"),
            "signature": ("Signed and verified (key " + cfg.signing_fingerprint[-16:] + ")") if res.signature_verified
                         else "Not signed yet",
            "signature_help": ("Release files are signed. Install the HCG archive key only from this site, and compare its fingerprint "
                               f"{cfg.signing_fingerprint} with the one published here." if res.signature_verified else
                               "This repository's metadata is not signed yet. Sileo accepts unsigned sources; packages are still "
                               "protected in transit by HTTPS and checked against the SHA-256 checksums in the index."),
        }
        for asset in ASSET_DIR.iterdir():
            shutil.copyfile(asset, tmp / "assets" / asset.name)
        (tmp / "index.html").write_text(landing(cfg, base_url, repo_url, groups, info), encoding="utf-8")
        (repo_dir / "index.html").write_text(repo_index(cfg, repo_url), encoding="utf-8")
        (tmp / "404.html").write_text(page("Not found · HCG Repo", '<section class="section prose"><h1>Not found</h1>'
                                           f'<p><a href="{e(base_url)}">Go to HCG Repo</a></p></section>', base_url,
                                           "Page not found", base_url), encoding="utf-8")
        build_info = {
            "builder": f"hcgrepo {__version__}", "revision": git_revision(cfg.root), "date": date.isoformat(),
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

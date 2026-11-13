"""Repository-level validation and APT index generation."""

from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import lzma
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from . import debian
from .config import (PackageMeta, Report, RepoConfig, check_package_meta, load_lock,
                     load_package_meta)
from .debinspect import DebInfo, InspectError, inspect_deb, validate_deb

# Presentation fields the repository may add to a Packages stanza. They never change what is
# installed, so they come from packages/<id>/package.toml rather than from the .deb.
PRESENTATION_FIELDS = ("Icon", "Depiction", "SileoDepiction", "Header")
REPO_FIELDS = ("Filename", "Size", "MD5sum", "SHA1", "SHA256", "SHA512")
INDEX_NAMES = ("Packages", "Packages.gz", "Packages.bz2", "Packages.xz")


@dataclass
class PoolPackage:
    info: DebInfo
    meta: PackageMeta
    rel: str            # path relative to the repository root, e.g. pool/iphoneos-arm64/x.deb
    report: Report


def pool_files(cfg: RepoConfig) -> list[Path]:
    if not cfg.pool_dir.exists():
        return []
    return sorted(p for p in cfg.pool_dir.rglob("*") if p.is_file() and p.suffix == ".deb")


def expected_pool_rel(info: DebInfo) -> str:
    return f"pool/{info.architecture}/{info.canonical_filename()}"


def load_pool(cfg: RepoConfig, report: Report, extra: list[tuple[DebInfo, str]] | None = None) -> list[PoolPackage]:
    """Inspect every .deb in the pool (plus `extra` candidates) and validate each one."""
    out: list[PoolPackage] = []
    items: list[tuple[Path | DebInfo, str]] = [(p, p.relative_to(cfg.root).as_posix()) for p in pool_files(cfg)]
    items += list(extra or [])
    for src, rel in items:
        try:
            info = src if isinstance(src, DebInfo) else inspect_deb(src)
        except InspectError as exc:
            report.error("deb-unreadable", f"{rel}: {exc}")
            continue
        meta = load_package_meta(cfg, info.package)
        pr = validate_deb(info, cfg, meta)
        check_package_meta(meta, pr)
        if not isinstance(src, DebInfo) and rel != expected_pool_rel(info):
            pr.error("pool-noncanonical-name", f"{rel} should be stored as {expected_pool_rel(info)}")
        out.append(PoolPackage(info, meta, rel, pr))
    return out


def check_repository(cfg: RepoConfig, pkgs: list[PoolPackage], report: Report, lock: dict | None = None) -> None:
    lock = lock if lock is not None else load_lock(cfg)
    # Per-package findings
    for p in pkgs:
        report.extend(p.report)

    # Duplicates
    seen: dict[tuple[str, str, str], PoolPackage] = {}
    for p in pkgs:
        k = p.info.key
        if k in seen:
            other = seen[k]
            kind = "identical" if other.info.sha256 == p.info.sha256 else "conflicting"
            report.error("duplicate-package",
                         f"{kind} duplicate {k[0]} {k[1]} {k[2]}: {other.rel} and {p.rel}")
        else:
            seen[k] = p

    # Lock consistency: every pool file locked with the same content; every published entry present.
    by_file = {e["filename"]: e for e in lock["packages"]}
    by_key = {}
    for e in lock["packages"]:
        by_key.setdefault((e["package"], e["version"], e["architecture"]), []).append(e)
    pool_rels = {p.rel for p in pkgs}
    for p in pkgs:
        e = by_file.get(p.rel)
        if e is None:
            report.error("lock-missing", f"{p.rel} is not recorded in repo-lock.json (import it with `hcgrepo import-deb`)")
            continue
        if e["sha256"] != p.info.sha256 or e["size"] != p.info.size:
            report.error("lock-content-changed",
                         f"{p.rel} differs from the content recorded at import (sha256 {e['sha256'][:12]}…); published versions are immutable")
        if e.get("status") == "withdrawn":
            report.error("lock-withdrawn-present", f"{p.rel} is withdrawn in repo-lock.json but still in the pool")
        prov = Path(cfg.root / (p.rel + ".provenance.json"))
        if not prov.exists():
            report.error("provenance-missing", f"{p.rel}: missing {prov.name}")
    for e in lock["packages"]:
        if e.get("status", "published") == "published" and e["filename"] not in pool_rels:
            report.error("lock-file-missing", f"repo-lock.json lists {e['filename']} but the file is missing from the pool")
    for k, entries in by_key.items():
        hashes = {e["sha256"] for e in entries}
        if len(hashes) > 1:
            report.error("lock-conflict", f"repo-lock.json records different contents for {' '.join(k)}")

    # Dependencies
    check_dependencies(cfg, [p.info for p in pkgs], report)


def check_dependencies(cfg: RepoConfig, infos: list[DebInfo], report: Report) -> None:
    provided: dict[str, list[tuple[str | None, str]]] = {}  # name -> [(version or None for virtual, arch)]
    for i in infos:
        provided.setdefault(i.package, []).append((i.version, i.architecture))
        try:
            for g in debian.parse_relations(i.get("Provides")):
                for rel in g:
                    provided.setdefault(rel.name, []).append((rel.version if rel.op == "=" else None, i.architecture))
        except debian.ControlError:
            pass
    for i in infos:
        for fld in ("Pre-Depends", "Depends"):
            try:
                groups = debian.parse_relations(i.get(fld))
            except debian.ControlError:
                continue  # already reported by package validation
            for group in groups:
                if any(_satisfiable(cfg, rel, i.architecture, provided) for rel in group):
                    continue
                desc = " | ".join(str(r) for r in group)
                internal = any(r.name.startswith(cfg.namespace) for r in group) if cfg.namespace else False
                hint = ("no package in this repository satisfies it" if internal else
                        "not provided by this repository and not listed in config/external-dependencies.toml")
                report.error("dependency-missing", f"{i.package} {i.version} ({i.architecture}) {fld}: {desc}: {hint}")


def _satisfiable(cfg: RepoConfig, rel: debian.Relation, arch: str, provided) -> bool:
    for ver, parch in provided.get(rel.name, []):
        if parch not in (arch, "all") and arch != "all":
            continue
        if rel.op is None:
            return True
        if ver is not None and rel.satisfied_by(ver):
            return True
    if rel.name in cfg.external_deps and not (cfg.namespace and rel.name.startswith(cfg.namespace)):
        return True
    return False


# ---------------------------------------------------------------------------- index generation


def build_info_date() -> datetime:
    """Release Date: SOURCE_DATE_EPOCH if set (reproducible builds), else the current time."""
    sde = os.environ.get("SOURCE_DATE_EPOCH")
    if sde:
        return datetime.fromtimestamp(int(sde), tz=timezone.utc)
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


def presentation_fields(cfg: RepoConfig, meta: PackageMeta, repo_url: str) -> list[tuple[str, str]]:
    pkg = meta.package
    out = [("Depiction", f"{repo_url}depictions/{pkg}/"),
           ("SileoDepiction", f"{repo_url}depictions/{pkg}/sileo.json")]
    if meta.icon:
        out.append(("Icon", f"{repo_url}assets/{pkg}/{meta.icon}"))
    if meta.header:
        out.append(("Header", f"{repo_url}assets/{pkg}/{meta.header}"))
    return out


def package_stanza(cfg: RepoConfig, p: PoolPackage, repo_url: str) -> list[tuple[str, str]]:
    override = dict(presentation_fields(cfg, p.meta, repo_url))
    fields = [(n, v) for n, v in p.info.control if n not in override and n not in REPO_FIELDS]
    fields += list(override.items())
    fields += [
        ("Filename", f"debs/{p.info.canonical_filename()}"),
        ("Size", str(p.info.size)),
        ("MD5sum", p.info.md5),
        ("SHA1", p.info.sha1),
        ("SHA256", p.info.sha256),
        ("SHA512", p.info.sha512),
    ]
    return fields


def sort_key(p: PoolPackage):
    import functools
    return (p.info.package, functools.cmp_to_key(debian.compare_versions)(p.info.version), p.info.architecture)


def render_packages(cfg: RepoConfig, pkgs: list[PoolPackage], repo_url: str) -> str:
    stanzas = [debian.format_stanza(package_stanza(cfg, p, repo_url)) for p in sorted(pkgs, key=sort_key)]
    return "\n".join(stanzas)


def compress_indexes(packages_text: str) -> dict[str, bytes]:
    raw = packages_text.encode("utf-8")
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", compresslevel=9, mtime=0, filename="") as g:
        g.write(raw)
    return {
        "Packages": raw,
        "Packages.gz": gz.getvalue(),
        "Packages.bz2": bz2.compress(raw, 9),
        "Packages.xz": lzma.compress(raw, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=6),
    }


def render_release(cfg: RepoConfig, files: dict[str, bytes], date: datetime) -> str:
    lines = [
        f"Origin: {cfg.origin}",
        f"Label: {cfg.label}",
        f"Suite: {cfg.suite}",
        f"Version: {cfg.version}",
        f"Codename: {cfg.codename}",
        f"Date: {format_datetime(date, usegmt=True)}",
        f"Architectures: {' '.join(cfg.architectures)}",
        "Components: main",
        f"Description: {cfg.description}",
    ]
    for title, algo in (("MD5Sum", "md5"), ("SHA1", "sha1"), ("SHA256", "sha256"), ("SHA512", "sha512")):
        lines.append(f"{title}:")
        for name in sorted(files):
            data = files[name]
            lines.append(f" {hashlib.new(algo, data).hexdigest()} {len(data):>16} {name}")
    return "\n".join(lines) + "\n"


def git_revision(root: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def check_lock_history(base_lock: dict, head_lock: dict, report: Report) -> None:
    """Entries already recorded on the base branch are immutable: they may only move from
    'published' to 'withdrawn'. Nothing may be deleted or have its content changed."""
    head = {e["filename"]: e for e in head_lock["packages"]}
    for b in base_lock["packages"]:
        h = head.get(b["filename"])
        if h is None:
            report.error("lock-history-removed", f"{b['filename']} was removed from repo-lock.json (withdraw it instead)")
            continue
        for k in ("package", "version", "architecture", "sha256", "size"):
            if h.get(k) != b.get(k):
                report.error("lock-history-changed", f"{b['filename']}: recorded {k} changed from {b.get(k)!r} to {h.get(k)!r}")
        if b.get("status", "published") == "withdrawn" and h.get("status", "published") != "withdrawn":
            report.error("lock-history-republished", f"{b['filename']} was withdrawn and cannot be republished; use a new version")


def lock_at_ref(root: Path, ref: str) -> dict:
    p = subprocess.run(["git", "-C", str(root), "show", f"{ref}:repo-lock.json"], capture_output=True, text=True)
    if p.returncode != 0:
        if "exists on disk, but not in" in p.stderr or "does not exist in" in p.stderr:
            return {"schema": 1, "packages": []}
        raise RuntimeError(f"git show {ref}:repo-lock.json failed: {p.stderr.strip()}")
    import json
    return json.loads(p.stdout)

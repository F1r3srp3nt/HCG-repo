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
from .config import (LOCK_CHANNELS, PackageMeta, Report, RepoConfig, check_package_meta, entry_channel, load_lock,
                     load_package_meta)
from .debinspect import DebInfo, InspectError, inspect_deb, validate_deb

# Presentation fields the repository may add to a Packages stanza. They never change what is
# installed, so they come from packages/<id>/package.toml rather than from the .deb.
PRESENTATION_FIELDS = ("Icon", "Depiction", "SileoDepiction", "Header")
HCG_NAMESPACE = "com.hakmicapitalgroup."
REPO_FIELDS = ("Filename", "Size", "MD5sum", "SHA1", "SHA256", "SHA512")
INDEX_NAMES = ("Packages", "Packages.gz", "Packages.bz2", "Packages.xz")


@dataclass
class PoolPackage:
    info: DebInfo
    meta: PackageMeta
    rel: str            # path relative to the repository root, e.g. pool/iphoneos-arm64/x.deb
    report: Report
    protected: bool = False   # connect/paid: delivered only through the payment provider, never as a public file
    store_release: str = ""   # protected: the HCG Store release approved to deliver exactly this file, if recorded


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

    # Metadata of packages that are not imported yet (a listing prepared before its release) is checked too,
    # so a prepared listing cannot carry an invalid access, channel or device-test record.
    imported = {p.info.package for p in pkgs}
    if cfg.packages_meta_dir.exists():
        for d in sorted(cfg.packages_meta_dir.iterdir()):
            if d.is_dir() and (d / "package.toml").exists() and d.name not in imported:
                mr = Report(f"packages/{d.name}")
                meta = load_package_meta(cfg, d.name)
                check_package_meta(meta, mr)
                if (cfg.access_require_explicit and meta.access == "public" and d.name.startswith(HCG_NAMESPACE)
                        and not meta.access_exception):
                    mr.error("access-public-exception", f"{d.name}: a public HCG package needs [access] exception")
                report.extend(mr)
    lock = lock if lock is not None else load_lock(cfg)
    # Channels: stable (/repo/) or candidate (/repo/candidate/, testing). The candidate source is public and has no
    # payment provider, so it may only carry public packages; protected entries stay stable-only (and unindexed).
    for e in lock["packages"]:
        ch = entry_channel(e)
        if ch not in LOCK_CHANNELS:
            report.error("lock-channel", f"{e['filename']}: channel must be one of {LOCK_CHANNELS}, got {ch!r}")
        elif ch == "candidate" and e.get("status", "published") == "protected":
            report.error("candidate-protected", f"{e['filename']}: protected packages cannot be in the public candidate channel")
    # Per-package findings
    for p in pkgs:
        report.extend(p.report)
        access = p.meta.access_for(p.info.version)
        if access != "public":
            report.error("protected-in-public-pool",
                         f"{p.rel}: {p.info.package} {p.info.version} has access level '{access}'; protected packages must not be "
                         "stored in this public repository (run `bin/hcgrepo protect`)")
        if cfg.access_require_explicit and not p.meta.access_explicit:
            report.error("access-undeclared",
                         f"{p.info.package}: declare [access] level = \"public\" | \"connect\" | \"paid\" in packages/{p.info.package}/package.toml")
        # HCG's own packages need HCG Connect sign-in unless an explicit, recorded exception makes one free.
        if (cfg.access_require_explicit and access == "public" and p.info.package.startswith(HCG_NAMESPACE)
                and not p.meta.access_exception):
            report.error("access-public-exception",
                         f"{p.info.package}: HCG packages require sign-in; a public one needs [access] exception = \"<who approved "
                         "it, when, and why>\" in its package.toml")
        elif (cfg.access_require_explicit and access == "public" and p.info.package.startswith(HCG_NAMESPACE)
                and p.info.version not in p.meta.access_exception_versions):
            report.error("access-exception-version",
                         f"{p.info.package} {p.info.version}: the free-release exception covers only "
                         f"{p.meta.access_exception_versions}; this version needs its own approval before it can be public")

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
        if e.get("status") == "protected":
            if e["filename"] in pool_rels or (cfg.root / e["filename"]).exists():
                report.error("protected-in-public-pool", f"{e['filename']} is protected but its .deb is in the public pool")
            prov = load_provenance(cfg, e["filename"])
            if not prov.get("control"):
                report.error("protected-control-missing", f"{e['filename']}: protected entry needs the control data in its provenance")
            meta = load_package_meta(cfg, e["package"])
            if meta.access_for(e["version"]) == "public":
                report.error("protected-but-public", f"{e['package']} {e['version']}: lock entry is protected but "
                             "package.toml makes this version public")
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


def load_provenance(cfg: RepoConfig, rel: str) -> dict:
    import json
    f = cfg.root / (rel + ".provenance.json")
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


PROTECTED_PREFIX = "protected/"


def protected_packages(cfg: RepoConfig) -> list[PoolPackage]:
    """Protected (connect/paid) packages, reconstructed from their lock entry and recorded control data.
    Their .deb is not in this repository; only metadata is."""
    from .debinspect import DebInfo
    out = []
    for e in load_lock(cfg)["packages"]:
        if e.get("status") != "protected":
            continue
        prov = load_provenance(cfg, e["filename"])
        try:
            control = debian.parse_control(prov.get("control", ""))
        except debian.ControlError:
            continue
        info = DebInfo(path=cfg.root / e["filename"], size=e["size"], md5=prov.get("md5", ""), sha1=prov.get("sha1", ""),
                       sha256=e["sha256"], sha512=prov.get("sha512", ""), members=[], control=control,
                       control_text=prov["control"])
        link = prov.get("hcg_store") or (prov if prov.get("source") == "hcg-store" else {})
        out.append(PoolPackage(info, load_package_meta(cfg, e["package"]), e["filename"], Report(e["package"]),
                               protected=True, store_release=str(link.get("store_release") or "")))
    return out


def protected_stanza(cfg: RepoConfig, p: PoolPackage, repo_url: str) -> list[tuple[str, str]]:
    """Index entry for a connect/paid package, used only while a payment endpoint is configured.
    `Tag: cydia::commercial` makes Sileo fetch the file through the provider's authorize_download;
    the Filename is a path that is never deployed, so the file cannot be fetched publicly."""
    override = dict(presentation_fields(cfg, p.meta, repo_url))
    fields = [(n, v) for n, v in p.info.control if n not in override and n not in REPO_FIELDS and n.lower() != "tag"]
    tags = [t.strip() for t in (p.info.get("Tag") or "").split(",") if t.strip()]
    if "cydia::commercial" not in tags:
        tags.append("cydia::commercial")
    fields.append(("Tag", ", ".join(tags)))
    fields += list(override.items())
    fields += [("Filename", f"{PROTECTED_PREFIX}{p.info.canonical_filename()}"), ("Size", str(p.info.size))]
    fields += [(n, v) for n, v in (("MD5sum", p.info.md5), ("SHA1", p.info.sha1), ("SHA256", p.info.sha256),
                                   ("SHA512", p.info.sha512)) if v]
    return fields


def sort_key(p: PoolPackage):
    import functools
    return (p.info.package, functools.cmp_to_key(debian.compare_versions)(p.info.version), p.info.architecture)


def render_packages(cfg: RepoConfig, pkgs: list[PoolPackage], repo_url: str) -> str:
    stanzas = [debian.format_stanza(protected_stanza(cfg, p, repo_url) if p.protected else package_stanza(cfg, p, repo_url))
               for p in sorted(pkgs, key=sort_key)]
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
    """Entries already recorded on the base branch are immutable: their content never changes and
    they are never deleted. Status may move between 'published' and 'protected', and to
    'withdrawn' (which is final)."""
    head = {e["filename"]: e for e in head_lock["packages"]}
    for b in base_lock["packages"]:
        h = head.get(b["filename"])
        if h is None:
            report.error("lock-history-removed", f"{b['filename']} was removed from repo-lock.json (withdraw it instead)")
            continue
        for k in ("package", "version", "architecture", "sha256", "size"):
            if h.get(k) != b.get(k):
                report.error("lock-history-changed", f"{b['filename']}: recorded {k} changed from {b.get(k)!r} to {h.get(k)!r}")
        if h.get("status", "published") not in ("published", "protected", "withdrawn"):
            report.error("lock-history-status", f"{b['filename']}: unknown status {h.get('status')!r}")
        if entry_channel(b) == "stable" and entry_channel(h) == "candidate":
            report.error("lock-history-channel", f"{b['filename']} was stable and cannot move back to candidate; withdraw it instead")
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

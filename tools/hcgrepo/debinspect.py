"""Inspect and validate a single .deb.

Structural checks are done in Python (ar container, Git LFS pointers, HTML error pages). The
control and data archives are then read through `dpkg-deb`, which is the authority on whether
dpkg on the device can unpack the package (it handles gzip/xz/zstd/bzip2 and verifies the
compressed streams end to end).
"""

from __future__ import annotations

import hashlib
import plistlib
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from . import debian, macho
from .config import PackageMeta, Report, RepoConfig

LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
MAINTAINER_SCRIPTS = ("preinst", "postinst", "prerm", "postrm", "config")
REQUIRED_FIELDS = ("Package", "Version", "Architecture", "Maintainer", "Description")
RELATION_FIELDS = ("Depends", "Pre-Depends", "Recommends", "Suggests", "Enhances",
                   "Conflicts", "Breaks", "Replaces", "Provides")
ROOTLESS_PREFIX = "var/jb"

# Absolute library locations that belong to a rootful layout. A rootless binary that links one of
# these will fail to load on a rootless jailbreak.
ROOTFUL_LIBRARY_PREFIXES = (
    "/Library/MobileSubstrate/", "/Library/Frameworks/CydiaSubstrate.framework/",
    "/usr/lib/libsubstrate", "/usr/lib/libhooker", "/usr/lib/libsubstitute", "/usr/lib/TweakInject",
    "/usr/local/lib/", "/Library/PreferenceBundles/", "/Library/PreferenceLoader/",
)

# Maintainer-script patterns that change global security settings or the device's trust setup.
FORBIDDEN_SCRIPT_PATTERNS = [
    (re.compile(r"/etc/sudoers"), "edits sudoers"),
    (re.compile(r"\b(chpasswd|passwd)\b"), "changes account passwords"),
    (re.compile(r"\bnvram\b"), "changes NVRAM"),
    (re.compile(r"\bchmod\s+(-\w+\s+)*([ugoa]*\+[rwx]*s|[0-7]?[4-7][0-7]{3}\b)"), "sets setuid/setgid bits"),
    (re.compile(r"sshd_config"), "changes the SSH server configuration"),
    (re.compile(r"sources\.list|/etc/apt/sources"), "adds or changes APT sources"),
    (re.compile(r"trusted\.gpg"), "changes APT trusted keys"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?[a-zA-Z]*\s+(/|/var/mobile|/private/var/mobile|/var/jb)/?(\s|$|;)"),
     "recursively deletes a system or user root"),
]


class InspectError(Exception):
    """The file is not a usable Debian package at all."""


@dataclass
class FileEntry:
    path: str           # relative, no leading './'
    kind: str           # file | dir | symlink | hardlink | other
    mode: int
    uid: int
    gid: int
    size: int
    linkname: str = ""


@dataclass
class Binary:
    path: str
    info: macho.MachO | None
    error: str | None = None


@dataclass
class AppBundle:
    path: str
    plist: dict | None
    plist_error: str | None = None

    @property
    def bundle_id(self) -> str | None:
        return (self.plist or {}).get("CFBundleIdentifier")

    @property
    def executable(self) -> str | None:
        return (self.plist or {}).get("CFBundleExecutable")

    @property
    def min_os(self) -> str | None:
        v = (self.plist or {}).get("MinimumOSVersion")
        return str(v) if v is not None else None


@dataclass
class DebInfo:
    path: Path
    size: int
    md5: str
    sha1: str
    sha256: str
    sha512: str
    members: list[tuple[str, int]]
    control: list[tuple[str, str]] = field(default_factory=list)
    control_text: str = ""
    files: list[FileEntry] = field(default_factory=list)
    scripts: dict[str, tuple[int, bytes]] = field(default_factory=dict)
    conffiles: list[str] = field(default_factory=list)
    binaries: list[Binary] = field(default_factory=list)
    apps: list[AppBundle] = field(default_factory=list)

    def get(self, name: str) -> str | None:
        return debian.field(self.control, name)

    @property
    def package(self) -> str:
        return self.get("Package") or ""

    @property
    def version(self) -> str:
        return self.get("Version") or ""

    @property
    def architecture(self) -> str:
        return self.get("Architecture") or ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.package, self.version, self.architecture)

    def canonical_filename(self) -> str:
        return f"{self.package}_{self.version}_{self.architecture}.deb"

    def summary(self) -> dict:
        return {
            "package": self.package, "version": self.version, "architecture": self.architecture,
            "size": self.size, "sha256": self.sha256,
            "depends": self.get("Depends") or "",
            "apps": [{"path": a.path, "bundle_id": a.bundle_id, "executable": a.executable,
                      "minimum_os": a.min_os} for a in self.apps],
            "binaries": [{"path": b.path, "arches": b.info.arches if b.info else [],
                          "signed": all(s.signed for s in b.info.slices) if b.info else False,
                          "entitlements": _merged_entitlements(b), "libraries": _dylibs(b)}
                         for b in self.binaries],
            "maintainer_scripts": sorted(self.scripts),
        }


def _dylibs(b: Binary) -> list[str]:
    if not b.info:
        return []
    seen: list[str] = []
    for s in b.info.slices:
        for d in s.dylibs:
            if d not in seen:
                seen.append(d)
    return seen


def _merged_entitlements(b: Binary) -> dict | None:
    if not b.info:
        return None
    ents = [s.entitlements for s in b.info.slices if s.entitlements is not None]
    return ents[0] if ents else None


# ---------------------------------------------------------------------------- reading


def require_dpkg_deb() -> str:
    exe = shutil.which("dpkg-deb")
    if not exe:
        raise InspectError("dpkg-deb not found. Install dpkg (Debian/Ubuntu: apt-get install dpkg; macOS: brew install dpkg).")
    return exe


def hash_file(path: Path) -> dict[str, str]:
    hs = {n: hashlib.new(n) for n in ("md5", "sha1", "sha256", "sha512")}
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            for h in hs.values():
                h.update(chunk)
    return {n: h.hexdigest() for n, h in hs.items()}


def read_ar_members(path: Path) -> list[tuple[str, int]]:
    data_len = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(512)
        if head.startswith(LFS_POINTER_PREFIX):
            raise InspectError("this is a Git LFS pointer file, not a package (fetch the LFS object or commit the real .deb)")
        stripped = head.lstrip()
        if stripped[:1] == b"<" or stripped.lower().startswith(b"<!doctype"):
            raise InspectError("this is an HTML/XML document (a download error page?), not a package")
        if stripped[:1] in (b"{", b"["):
            raise InspectError("this is a JSON document (an API error?), not a package")
        if not head.startswith(b"!<arch>\n"):
            raise InspectError("not an ar archive (bad magic); not a Debian package")
        f.seek(8)
        members = []
        pos = 8
        while pos < data_len:
            hdr = f.read(60)
            if len(hdr) == 0:
                break
            if len(hdr) < 60 or hdr[58:60] != b"`\n":
                raise InspectError(f"corrupt ar member header at offset {pos} (truncated or damaged)")
            name = hdr[:16].decode("ascii", "replace").strip().rstrip("/")
            try:
                size = int(hdr[48:58].decode("ascii").strip())
            except ValueError as exc:
                raise InspectError(f"corrupt ar member size at offset {pos}") from exc
            if pos + 60 + size > data_len:
                raise InspectError(f"ar member {name!r} extends past end of file (truncated download?)")
            members.append((name, size))
            pos += 60 + size + (size % 2)
            f.seek(pos)
    names = [m[0] for m in members]
    if len(names) < 3 or names[0] != "debian-binary":
        raise InspectError(f"unexpected ar members {names}; a .deb starts with debian-binary")
    if not re.fullmatch(r"control\.tar(\.(gz|xz|zst|bz2|lzma))?", names[1]):
        raise InspectError(f"second member must be control.tar*, got {names[1]!r}")
    if not re.fullmatch(r"data\.tar(\.(gz|xz|zst|bz2|lzma))?", names[2]):
        raise InspectError(f"third member must be data.tar*, got {names[2]!r}")
    extra = [n for n in names[3:] if not n.startswith("_")]
    if extra:
        raise InspectError(f"unexpected extra ar members {extra}")
    with open(path, "rb") as f:
        f.seek(68)
        if f.read(members[0][1]) != b"2.0\n":
            raise InspectError("debian-binary is not '2.0'")
    return members


def _iter_tar(cmd: list[str]):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
            for member in tf:
                yield tf, member
    except tarfile.TarError as exc:
        proc.kill()
        proc.wait()
        raise InspectError(f"{cmd[1]} produced an unreadable tar stream: {exc}") from exc
    finally:
        rest = proc.stdout.read() if proc.poll() is None else b""
        del rest
        err = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
        code = proc.wait()
        proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
        if code != 0:
            raise InspectError(f"dpkg-deb {cmd[1]} failed (exit {code}): {err or 'no message'}")


def _norm(name: str) -> str:
    n = name
    while n.startswith("./"):
        n = n[2:]
    return n.rstrip("/") if n not in ("", ".") else ""


def inspect_deb(path: Path) -> DebInfo:
    path = Path(path)
    if not path.is_file():
        raise InspectError(f"{path}: not a file")
    if path.stat().st_size == 0:
        raise InspectError("empty file")
    members = read_ar_members(path)
    dpkg = require_dpkg_deb()
    hs = hash_file(path)
    info = DebInfo(path=path, size=path.stat().st_size, md5=hs["md5"], sha1=hs["sha1"],
                   sha256=hs["sha256"], sha512=hs["sha512"], members=members)

    # Control archive
    for tf, m in _iter_tar([dpkg, "--ctrl-tarfile", str(path)]):
        name = _norm(m.name)
        if not m.isfile():
            continue
        data = tf.extractfile(m).read()  # type: ignore[union-attr]
        if name == "control":
            info.control_text = data.decode("utf-8")
        elif name in MAINTAINER_SCRIPTS:
            info.scripts[name] = (m.mode, data)
        elif name == "conffiles":
            info.conffiles = [l.strip() for l in data.decode("utf-8", "replace").splitlines() if l.strip()]
    if not info.control_text:
        raise InspectError("control archive has no control file")
    try:
        info.control = debian.parse_control(info.control_text)
    except debian.ControlError as exc:
        raise InspectError(f"control file: {exc}") from exc

    # Data archive
    plists: dict[str, bytes] = {}
    for tf, m in _iter_tar([dpkg, "--fsys-tarfile", str(path)]):
        name = _norm(m.name)
        kind = ("dir" if m.isdir() else "symlink" if m.issym() else "hardlink" if m.islnk()
                else "file" if m.isfile() else "other")
        info.files.append(FileEntry(name, kind, m.mode, m.uid, m.gid, m.size, m.linkname))
        if kind != "file":
            continue
        fobj = tf.extractfile(m)
        assert fobj is not None
        head = fobj.read(4)
        if re.search(r"[^/]+\.app/Info\.plist$", name):
            plists[name] = head + fobj.read()
        elif macho.is_macho(head):
            data = head + fobj.read()
            try:
                info.binaries.append(Binary(name, macho.parse(data)))
            except macho.MachOError as exc:
                info.binaries.append(Binary(name, None, str(exc)))

    for f in info.files:
        if f.kind == "dir" and re.search(r"[^/]+\.app$", f.path) and not re.search(r"\.app/.+\.app$", f.path):
            pl = plists.get(f.path + "/Info.plist")
            if pl is None:
                info.apps.append(AppBundle(f.path, None, "missing Info.plist"))
                continue
            try:
                info.apps.append(AppBundle(f.path, plistlib.loads(pl)))
            except Exception as exc:  # noqa: BLE001
                info.apps.append(AppBundle(f.path, None, f"unreadable Info.plist: {exc}"))
    return info


# ---------------------------------------------------------------------------- validation


def validate_deb(info: DebInfo, cfg: RepoConfig, meta: PackageMeta) -> Report:
    r = Report(f"{info.package or info.path.name} {info.version} {info.architecture}".strip())
    _check_control(info, cfg, r)
    _check_payload(info, meta, r)
    _check_binaries(info, meta, r)
    _check_apps(info, cfg, r)
    _check_scripts(info, r)
    return r


def _check_control(info: DebInfo, cfg: RepoConfig, r: Report) -> None:
    for name in REQUIRED_FIELDS:
        if not (info.get(name) or "").strip():
            r.error("control-missing-field", f"control file lacks {name}")
    pkg = info.package
    if pkg and not debian.PACKAGE_NAME_RE.match(pkg):
        r.error("control-bad-package", f"invalid package name {pkg!r} (lowercase letters, digits, '.', '+', '-')")
    if info.version:
        try:
            v = debian.parse_version(info.version)
            if v.epoch:
                r.error("control-epoch", "epochs are not supported: they cannot be represented in immutable filenames")
        except debian.ControlError as exc:
            r.error("control-bad-version", str(exc))
    arch = info.architecture
    if arch and arch != "all" and arch not in cfg.architectures:
        r.error("control-unsupported-arch",
                f"architecture {arch!r} is not accepted by this repository ({', '.join(cfg.architectures)}, all)")
    for name in RELATION_FIELDS:
        try:
            debian.parse_relations(info.get(name))
        except debian.ControlError as exc:
            r.error("control-bad-relation", f"{name}: {exc}")
    if not info.get("Name"):
        r.warn("control-no-name", "no Name field; Sileo will show the package identifier")
    if not (info.get("Section") or "").strip():
        r.warn("control-no-section", "no Section field")
    for repo_field in ("Filename", "Size", "MD5sum", "SHA1", "SHA256", "SHA512"):
        if info.get(repo_field) is not None:
            r.error("control-repo-field", f"control file must not contain the repository field {repo_field}")


def _check_payload(info: DebInfo, meta: PackageMeta, r: Report) -> None:
    arch = info.architecture
    not_root_owned = 0
    for f in info.files:
        p = f.path
        parts = p.split("/") if p else []
        if f.path.startswith("/") or ".." in parts:
            r.error("path-unsafe", f"unsafe path {p!r}")
            continue
        if f.kind == "other":
            r.error("path-special-file", f"device node or FIFO {p!r}")
        if f.kind in ("file", "dir") and f.mode & 0o6000:
            if p not in meta.allow_setuid:
                r.error("path-setuid", f"{p} has setuid/setgid bits ({oct(f.mode)}); not allowed without an explicit review entry")
            else:
                r.warn("path-setuid-reviewed", f"{p} is setuid/setgid ({oct(f.mode)}) and listed in review.allow_setuid")
        if f.kind in ("file", "dir") and f.mode & 0o002 and not (f.kind == "dir" and f.mode & 0o1000):
            r.error("path-world-writable", f"{p} is world-writable ({oct(f.mode)})")
        if f.uid != 0 or f.gid != 0:
            not_root_owned += 1
        in_jb = p == ROOTLESS_PREFIX or p.startswith(ROOTLESS_PREFIX + "/")
        if arch == "iphoneos-arm64":
            if p in ("", "var") or in_jb:
                continue
            r.error("rootless-path-outside-jbroot",
                    f"rootless (iphoneos-arm64) package installs {p!r} outside /var/jb")
        elif arch in ("iphoneos-arm", "iphoneos-arm64e") and in_jb:
            r.error("rootful-path-in-jbroot", f"{arch} package must not install into /var/jb: {p!r}")
        elif arch == "all" and in_jb:
            r.error("all-arch-jbroot-path",
                    f"Architecture: all package installs {p!r} under /var/jb; rootless packages must be iphoneos-arm64")
    if not_root_owned:
        r.warn("path-not-root-owned", f"{not_root_owned} entries are not owned by root:wheel (build with dpkg-deb --root-owner-group)")
    if not any(f.kind != "dir" for f in info.files):
        r.warn("payload-empty", "package installs no files")


def _check_binaries(info: DebInfo, meta: PackageMeta, r: Report) -> None:
    arch = info.architecture
    allowed = {"iphoneos-arm64": {"arm64", "arm64e"}, "iphoneos-arm64e": {"arm64", "arm64e"},
               "iphoneos-arm": {"arm64", "arm64e", "armv7", "armv7s"}}.get(arch, set())
    reviewed = set(meta.entitlements)
    for b in info.binaries:
        if b.info is None:
            r.error("macho-corrupt", f"{b.path}: {b.error}")
            continue
        if arch == "all":
            r.error("macho-in-all-arch", f"{b.path}: native code in an Architecture: all package")
        elif not set(b.info.arches) & allowed:
            r.error("macho-wrong-arch", f"{b.path}: architectures {b.info.arches} do not match {arch}")
        for s in b.info.slices:
            if not s.signed:
                r.error("macho-unsigned", f"{b.path} ({s.arch}): no code signature; iOS will refuse to run it (sign with ldid)")
            if s.encrypted:
                r.error("macho-encrypted", f"{b.path} ({s.arch}): FairPlay-encrypted binary cannot be redistributed or re-signed")
        libs = _dylibs(b)
        if arch == "iphoneos-arm64":
            for d in libs:
                if d.startswith(ROOTFUL_LIBRARY_PREFIXES):
                    r.error("rootless-links-rootful-path", f"{b.path} links {d}, a rootful path (use @rpath or the /var/jb location)")
        elif arch in ("iphoneos-arm", "iphoneos-arm64e"):
            for d in libs:
                if d.startswith("/var/jb/"):
                    r.error("rootful-links-jbroot", f"{b.path} links {d}, which only exists on rootless jailbreaks")
        ents = _merged_entitlements(b)
        expected = meta.entitlements.get(b.path)
        if ents and expected is None:
            r.error("entitlements-unreviewed",
                    f"{b.path} carries entitlements {sorted(ents)} that are not recorded in packages/{info.package}/package.toml [review.entitlements]")
        elif expected is not None and (ents or {}) != expected:
            r.error("entitlements-mismatch",
                    f"{b.path}: entitlements {ents or {}} differ from the reviewed set {expected}")
        reviewed.discard(b.path)
    for p in sorted(reviewed):
        r.warn("entitlements-review-stale", f"reviewed entitlements for {p} but no such binary in this package")


def _check_apps(info: DebInfo, cfg: RepoConfig, r: Report) -> None:
    arch = info.architecture
    lo, _hi = debian.firmware_bounds(_safe_get_relations(info))
    binaries = {b.path: b for b in info.binaries}
    files = {f.path: f for f in info.files}
    for app in info.apps:
        expected_parent = "var/jb/Applications" if arch == "iphoneos-arm64" else "Applications"
        if app.path.rsplit("/", 1)[0] != expected_parent:
            r.warn("app-location", f"{app.path}: apps for {arch} normally live in /{expected_parent}")
        if app.plist is None:
            r.error("app-bad-plist", f"{app.path}: {app.plist_error}")
            continue
        if not app.bundle_id:
            r.error("app-no-bundle-id", f"{app.path}: Info.plist lacks CFBundleIdentifier")
        elif app.bundle_id in cfg.reserved_bundle_ids:
            r.error("app-reserved-bundle-id",
                    f"{app.path}: bundle id {app.bundle_id} belongs to the sandboxed App Store/TestFlight edition")
        exe = app.executable
        if not exe:
            r.error("app-no-executable", f"{app.path}: Info.plist lacks CFBundleExecutable")
        else:
            ep = f"{app.path}/{exe}"
            fe = files.get(ep)
            if fe is None or fe.kind != "file":
                r.error("app-executable-missing", f"{ep} is not in the package")
            else:
                if not fe.mode & 0o111:
                    r.error("app-executable-mode", f"{ep} is not executable ({oct(fe.mode)})")
                b = binaries.get(ep)
                if b is None:
                    r.error("app-executable-not-macho", f"{ep} is not a Mach-O binary")
                elif b.info and not any(s.filetype == "executable" for s in b.info.slices):
                    r.error("app-executable-type", f"{ep} is not a Mach-O executable")
        if app.min_os:
            if lo is None:
                r.error("firmware-missing",
                        f"{app.path} requires iOS {app.min_os} but Depends has no 'firmware (>= {app.min_os})'; Sileo would offer it to older devices")
            elif debian.compare_versions(_vfix(lo), _vfix(app.min_os)) < 0:
                r.error("firmware-too-low",
                        f"Depends allows firmware >= {lo} but {app.path} requires iOS {app.min_os}")
    if info.apps:
        post = info.scripts.get("postinst", (0, b""))[1].decode("utf-8", "replace")
        if "uicache" not in post:
            r.warn("app-no-uicache", "package installs an app but postinst does not run uicache; the icon may not appear")
        else:
            depends_names = {a.name for g in debian.parse_relations(_safe_get_relations(info)) for a in g}
            if "uikittools" not in depends_names:
                r.warn("app-uicache-undeclared",
                       "postinst uses uicache but Depends does not include uikittools (the provider of uicache)")


def _vfix(v: str) -> str:
    return v if v[:1].isdigit() else "0"


def _safe_get_relations(info: DebInfo) -> str | None:
    d = info.get("Depends")
    try:
        debian.parse_relations(d)
        return d
    except debian.ControlError:
        return None


def _check_scripts(info: DebInfo, r: Report) -> None:
    arch = info.architecture
    for name, (mode, data) in sorted(info.scripts.items()):
        if not mode & 0o111:
            r.error("script-not-executable", f"{name} is not executable ({oct(mode)}); dpkg will fail")
        if macho.is_macho(data[:4]):
            r.warn("script-binary", f"{name} is a compiled binary; it cannot be reviewed as text")
            continue
        if not data.startswith(b"#!"):
            r.error("script-no-shebang", f"{name} has no #! interpreter line")
        text = data.decode("utf-8", "replace")
        for rx, why in FORBIDDEN_SCRIPT_PATTERNS:
            if rx.search(text):
                r.error("script-forbidden", f"{name} {why}; packages must not change global security settings")
        if arch in ("iphoneos-arm", "iphoneos-arm64e") and "/var/jb" in text:
            r.warn("script-jbroot-in-rootful", f"{name} references /var/jb in a {arch} package")

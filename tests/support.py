"""Test helpers: throw-away repository roots and real .deb fixtures built with dpkg-deb."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from hcgrepo import macho  # noqa: E402
from hcgrepo.config import load_config  # noqa: E402

NS = "com.hakmicapitalgroup."


class RepoTestCase(unittest.TestCase):
    """Each test gets a private copy of config/ and branding/ with an empty pool and lock."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hcgrepo-test-")
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        for d in ("config", "branding"):
            shutil.copytree(REPO / d, self.root / d)
        (self.root / "packages").mkdir()
        (self.root / "pool").mkdir()
        (self.root / "repo-lock.json").write_text('{"packages": [], "schema": 1}\n')
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.cfg = load_config(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def reload(self):
        self.cfg = load_config(self.root)
        return self.cfg

    def write_meta(self, package: str, toml: str, changelog: str | None = None, icon: bool = False):
        d = self.root / "packages" / package
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.toml").write_text(toml)
        if changelog:
            (d / "CHANGELOG.md").write_text(changelog)
        if icon:
            shutil.copyfile(REPO / "branding" / "CydiaIcon@2x.png", d / "icon.png")


def make_deb(out: Path, control: dict[str, str], files: dict[str, tuple[bytes, int]] | None = None,
             scripts: dict[str, tuple[str, int]] | None = None, dirs: list[tuple[str, int]] | None = None,
             root_owner: bool = True, compression: str = "xz", setup=None, nocheck: bool = False) -> Path:
    """Build a real .deb. `files` maps 'var/jb/usr/bin/x' -> (content, mode)."""
    stage = Path(tempfile.mkdtemp(prefix="stage-", dir=out.parent))
    try:
        (stage / "DEBIAN").mkdir()
        (stage / "DEBIAN" / "control").write_text("".join(f"{k}: {v}\n" for k, v in control.items()))
        for name, (text, mode) in (scripts or {}).items():
            p = stage / "DEBIAN" / name
            p.write_text(text)
            os.chmod(p, mode)
        for path, mode in dirs or []:
            p = stage / path
            p.mkdir(parents=True, exist_ok=True)
            os.chmod(p, mode)
        for path, (content, mode) in (files or {}).items():
            p = stage / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            os.chmod(p, mode)
        if setup:
            setup(stage)
        cmd = ["dpkg-deb", f"-Z{compression}", "-b", str(stage), str(out)]
        if root_owner:
            cmd.insert(1, "--root-owner-group")
        if nocheck:  # emulate packagers that skip dpkg-deb's own sanity checks
            cmd.insert(1, "--nocheck")
        subprocess.run(cmd, check=True, capture_output=True)
        return out
    finally:
        shutil.rmtree(stage)


def control(package: str, version: str = "1.0.0", arch: str = "iphoneos-arm64", **extra) -> dict[str, str]:
    c = {"Package": package, "Name": package.rsplit(".", 1)[-1].title(), "Version": version,
         "Architecture": arch, "Maintainer": "Hakmi Capital Group", "Author": "Hakmi Capital Group",
         "Section": "Utilities", "Description": f"Test package {package}\n Longer description line."}
    c.update(extra)
    return {k: v for k, v in c.items() if v is not None}


def tool_deb(work: Path, package: str = NS + "testtool", version: str = "1.0.0", arch: str = "iphoneos-arm64",
             prefix: str | None = None, **extra) -> Path:
    """A command-line tool package: a shell script in <prefix>/usr/bin."""
    prefix = ("var/jb" if arch == "iphoneos-arm64" else "") if prefix is None else prefix
    rel = f"{prefix}/usr/bin/hcg-test".lstrip("/")
    out = work / f"{package}_{version}_{arch}.deb"
    return make_deb(out, control(package, version, arch, **extra),
                    files={rel: (f"#!/bin/sh\necho {package} {version}\n".encode(), 0o755)})


def app_deb(work: Path, package: str = NS + "testapp", version: str = "1.0.0", bundle_id: str | None = None,
            entitlements: dict | None = None, min_os: str = "15.0", depends: str | None = "firmware (>= 15.0), uikittools",
            arch: str = "iphoneos-arm64", macho_arch: str = "arm64", signed: bool = True, exec_mode: int = 0o755,
            postinst: str | None = None, dylibs: list[str] | None = None) -> Path:
    prefix = "var/jb/" if arch == "iphoneos-arm64" else ""
    app = f"{prefix}Applications/TestApp.app"
    plist = plistlib.dumps({"CFBundleIdentifier": bundle_id or package, "CFBundleExecutable": "TestApp",
                            "MinimumOSVersion": min_os, "CFBundleShortVersionString": version})
    binary = macho.build_test_macho(macho_arch, entitlements=entitlements, signed=signed, dylibs=dylibs)
    post = postinst if postinst is not None else (
        f"#!/bin/sh\nif command -v uicache >/dev/null 2>&1; then uicache -p /{app} || true; fi\nexit 0\n")
    out = work / f"{package}_{version}_{arch}.deb"
    return make_deb(out, control(package, version, arch, Depends=depends, Section="Terminal_Support"),
                    files={f"{app}/Info.plist": (plist, 0o644), f"{app}/TestApp": (binary, exec_mode)},
                    scripts={"postinst": (post, 0o755)} if post else None)

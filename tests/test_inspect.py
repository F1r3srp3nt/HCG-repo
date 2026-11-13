"""Package-level validation: broken archives, layout mistakes, permissions, Mach-O and scripts."""

import plistlib
import unittest

from support import NS, RepoTestCase, app_deb, control, make_deb, tool_deb
from hcgrepo.config import load_package_meta
from hcgrepo.debinspect import InspectError, inspect_deb, validate_deb

ENTS = {"com.apple.private.security.no-sandbox": True, "platform-application": True}
APP_EXE = "var/jb/Applications/TestApp.app/TestApp"


class InspectTests(RepoTestCase):
    def validate(self, path):
        info = inspect_deb(path)
        return info, validate_deb(info, self.cfg, load_package_meta(self.cfg, info.package))

    def assertRejected(self, path, code):
        _info, r = self.validate(path)
        self.assertIn(code, {c for c, _ in r.errors}, r.render())

    # --- archives that are not usable packages
    def test_valid_rootless_tool(self):
        info, r = self.validate(tool_deb(self.work))
        self.assertTrue(r.ok, r.render())
        self.assertEqual(info.key, (NS + "testtool", "1.0.0", "iphoneos-arm64"))

    def test_truncated_archive(self):
        p = tool_deb(self.work)
        data = p.read_bytes()
        p.write_bytes(data[: len(data) - 40])
        with self.assertRaises(InspectError):
            inspect_deb(p)

    def test_corrupt_compressed_payload(self):
        p = tool_deb(self.work)
        data = bytearray(p.read_bytes())
        data[-30] ^= 0xFF  # flip a byte inside data.tar.xz; ar framing stays intact
        p.write_bytes(bytes(data))
        with self.assertRaises(InspectError):
            inspect_deb(p)

    def test_git_lfs_pointer(self):
        p = self.work / "x.deb"
        p.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 123456\n")
        with self.assertRaisesRegex(InspectError, "LFS"):
            inspect_deb(p)

    def test_html_error_page(self):
        p = self.work / "x.deb"
        p.write_text("<!DOCTYPE html><html><body>404</body></html>")
        with self.assertRaisesRegex(InspectError, "HTML"):
            inspect_deb(p)

    def test_empty_and_random_files(self):
        p = self.work / "x.deb"
        p.write_bytes(b"")
        with self.assertRaises(InspectError):
            inspect_deb(p)
        p.write_bytes(b"PK\x03\x04 not a deb")
        with self.assertRaises(InspectError):
            inspect_deb(p)

    # --- layout
    def test_rootless_file_outside_jbroot(self):
        self.assertRejected(tool_deb(self.work, prefix=""), "rootless-path-outside-jbroot")

    def test_rootful_file_inside_jbroot(self):
        self.assertRejected(tool_deb(self.work, arch="iphoneos-arm", prefix="var/jb"), "rootful-path-in-jbroot")

    def test_valid_rootful_tool(self):
        _i, r = self.validate(tool_deb(self.work, arch="iphoneos-arm"))
        self.assertTrue(r.ok, r.render())

    def test_all_arch_with_jbroot_paths(self):
        self.assertRejected(tool_deb(self.work, arch="all", prefix="var/jb"), "all-arch-jbroot-path")

    def test_roothide_arch_not_accepted(self):
        self.assertRejected(tool_deb(self.work, arch="iphoneos-arm64e", prefix=""), "control-unsupported-arch")

    # --- permissions
    def test_setuid_rejected(self):
        p = make_deb(self.work / "s.deb", control(NS + "suid"),
                     files={"var/jb/usr/bin/x": (b"#!/bin/sh\n", 0o4755)})
        self.assertRejected(p, "path-setuid")

    def test_world_writable_rejected(self):
        p = make_deb(self.work / "w.deb", control(NS + "ww"), files={"var/jb/etc/x.conf": (b"x", 0o666)})
        self.assertRejected(p, "path-world-writable")

    def test_not_root_owned_is_a_warning(self):
        import os

        def chown(stage):
            try:
                os.chown(stage / "var/jb/usr/bin/x", 501, 501)
            except PermissionError:
                pass

        p = make_deb(self.work / "o.deb", control(NS + "own"), files={"var/jb/usr/bin/x": (b"#!/bin/sh\n", 0o755)},
                     root_owner=False, setup=chown)
        info, r = self.validate(p)
        if all(f.uid == 0 for f in info.files):
            self.skipTest("cannot produce non-root ownership here")
        self.assertIn("path-not-root-owned", {c for c, _ in r.warnings})

    # --- control
    def test_repo_fields_in_control_rejected(self):
        self.assertRejected(tool_deb(self.work, Filename="debs/x.deb"), "control-repo-field")

    def test_bad_dependency_syntax(self):
        p = make_deb(self.work / "d.deb", control(NS + "dep", Depends="firmware (=> 15.0)"),
                     files={"var/jb/usr/bin/x": (b"#!/bin/sh\n", 0o755)}, nocheck=True)
        self.assertRejected(p, "control-bad-relation")

    def test_epoch_rejected(self):
        self.assertRejected(tool_deb(self.work, version="1:2.0"), "control-epoch")

    # --- apps and Mach-O
    def test_app_with_reviewed_entitlements(self):
        self.write_meta(NS + "testapp", f'[review.entitlements."{APP_EXE}"]\n'
                        '"com.apple.private.security.no-sandbox" = true\n"platform-application" = true\n')
        info, r = self.validate(app_deb(self.work, entitlements=ENTS))
        self.assertTrue(r.ok, r.render())
        self.assertEqual(info.apps[0].bundle_id, NS + "testapp")
        self.assertEqual(info.binaries[0].info.slices[0].entitlements, ENTS)

    def test_unreviewed_entitlements(self):
        self.assertRejected(app_deb(self.work, entitlements=ENTS), "entitlements-unreviewed")

    def test_entitlements_differ_from_review(self):
        self.write_meta(NS + "testapp", f'[review.entitlements."{APP_EXE}"]\n"platform-application" = true\n')
        self.assertRejected(app_deb(self.work, entitlements=ENTS), "entitlements-mismatch")

    def test_unsigned_binary(self):
        self.assertRejected(app_deb(self.work, signed=False), "macho-unsigned")

    def test_wrong_architecture_binary(self):
        self.assertRejected(app_deb(self.work, macho_arch="x86_64"), "macho-wrong-arch")

    def test_non_executable_app_binary(self):
        self.assertRejected(app_deb(self.work, exec_mode=0o644), "app-executable-mode")

    def test_reserved_bundle_id(self):
        self.assertRejected(app_deb(self.work, bundle_id="com.hakmicapitalgroup.hcgterminal"), "app-reserved-bundle-id")

    def test_firmware_dependency_required_for_app(self):
        self.assertRejected(app_deb(self.work, depends="uikittools"), "firmware-missing")
        self.assertRejected(app_deb(self.work, depends="firmware (>= 14.0), uikittools", min_os="15.0"), "firmware-too-low")

    def test_uicache_without_uikittools_warns(self):
        _i, r = self.validate(app_deb(self.work, depends="firmware (>= 15.0)"))
        self.assertTrue(r.ok, r.render())
        self.assertIn("app-uicache-undeclared", {c for c, _ in r.warnings})

    def test_rootless_binary_linking_rootful_substrate(self):
        self.assertRejected(app_deb(self.work, dylibs=["/usr/lib/libSystem.B.dylib", "/Library/Frameworks/CydiaSubstrate.framework/CydiaSubstrate"]),
                            "rootless-links-rootful-path")

    def test_missing_info_plist(self):
        p = make_deb(self.work / "a.deb", control(NS + "noplist", Depends="firmware (>= 15.0)"),
                     files={"var/jb/Applications/X.app/X": (b"#!/bin/sh\n", 0o755)})
        self.assertRejected(p, "app-bad-plist")

    # --- maintainer scripts
    def test_script_not_executable(self):
        p = make_deb(self.work / "s.deb", control(NS + "scr"), files={"var/jb/usr/bin/x": (b"#!/bin/sh\n", 0o755)},
                     scripts={"postinst": ("#!/bin/sh\nexit 0\n", 0o644)}, nocheck=True)
        self.assertRejected(p, "script-not-executable")

    def test_script_changing_security_settings(self):
        for body in ("echo 'mobile ALL=(ALL) NOPASSWD: ALL' >> /var/jb/etc/sudoers", "chmod u+s /var/jb/usr/bin/x",
                     "echo 'deb https://x/ ./' > /var/jb/etc/apt/sources.list.d/x.list"):
            with self.subTest(body=body):
                p = make_deb(self.work / "s.deb", control(NS + "scr"), files={"var/jb/usr/bin/x": (b"#!/bin/sh\n", 0o755)},
                             scripts={"postinst": (f"#!/bin/sh\n{body}\nexit 0\n", 0o755)})
                self.assertRejected(p, "script-forbidden")


if __name__ == "__main__":
    unittest.main()

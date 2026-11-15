"""Import requests, identity pinning, binary inspection details, keyring packages and site rules."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import NS, RepoTestCase, app_deb, control, make_deb, tool_deb
from hcgrepo import macho
from hcgrepo.config import Report, load_lock, load_package_meta
from hcgrepo.debinspect import FileEntry, inspect_deb, validate_deb
from hcgrepo.importer import GitHub, ImportError_, import_deb, load_request, pending_requests, run_request
from hcgrepo.site import build, url_breaks

PROD = "https://repo.hakmicapitalgroup.com/"
SHA = "a" * 40


class RequestTests(RepoTestCase):
    def write_req(self, name, body):
        d = self.root / "imports"
        d.mkdir(exist_ok=True)
        (d / name).write_text(body)
        return d / name

    def test_unpinned_requests_are_refused(self):
        bad = {
            "latest.toml": f'[request]\npackage="{NS}x"\nversion="1"\narchitecture="iphoneos-arm64"\n'
                           f'[source]\nkind="github-release"\nrepository="o/r"\ntag="latest"\nasset="x.deb"\ncommit="{SHA}"\n',
            "short.toml": f'[request]\npackage="{NS}x"\nversion="1"\narchitecture="iphoneos-arm64"\n'
                          '[source]\nkind="github-artifact"\nrepository="o/r"\nrun_id=1\nartifact="a"\ncommit="abc123"\n',
            "recipe.toml": f'[request]\npackage="{NS}x"\nversion="1"\narchitecture="iphoneos-arm64"\n'
                           f'[source]\nkind="build"\nrepository="o/r"\ncommit="{SHA}"\nrecipe="rm -rf"\nlayout="rootless"\nentitlements="a.plist"\n',
            "escape.toml": f'[request]\npackage="{NS}x"\nversion="1"\narchitecture="iphoneos-arm64"\n'
                           f'[source]\nkind="build"\nrepository="o/r"\ncommit="{SHA}"\nrecipe="hcg-terminal-unleashed"\nlayout="rootless"\nentitlements="../x.plist"\n',
        }
        for name, body in bad.items():
            with self.subTest(name=name), self.assertRaises(ImportError_):
                load_request(self.write_req(name, body))

    def test_build_request_imports_with_provenance_and_identity(self):
        req = self.write_req("t.toml", f'[request]\npackage="{NS}testtool"\nversion="1.0.0"\narchitecture="iphoneos-arm64"\n'
                                       f'[source]\nkind="build"\nrepository="o/r"\ncommit="{SHA}"\nrecipe="hcg-terminal-unleashed"\n'
                                       'layout="rootless"\nentitlements="e.plist"\n')
        r = load_request(req)
        self.assertEqual([p["id"] for p in pending_requests(self.cfg)], ["t"])
        status, rep = run_request(self.cfg, r, GitHub("http://127.0.0.1:9", token=""), self.work, tool_deb(self.work),
                                  {"builder_run_url": "https://example.invalid/run/1"})
        self.assertEqual(status, "imported", rep.render())
        prov = json.loads(next(self.root.glob("pool/*/*.provenance.json")).read_text())
        self.assertEqual((prov["source"], prov["commit"], prov["request"]), ("source-build", SHA, "t"))
        self.assertEqual(pending_requests(self.cfg), [])

    def test_identity_pins(self):
        p = tool_deb(self.work)
        for expect in ({"version": "2.0"}, {"package": NS + "other"}, {"architecture": "iphoneos-arm"}, {"sha256": "0" * 64}):
            with self.subTest(expect=expect):
                status, rep = import_deb(self.cfg, p, {"source": "test"}, expect=expect)
                self.assertEqual(status, "rejected")
                self.assertIn("unexpected-identity", rep.codes())
        self.assertEqual(load_lock(self.cfg)["packages"], [])


class InspectionDetailTests(RepoTestCase):
    def validate(self, path):
        info = inspect_deb(path)
        return info, validate_deb(info, self.cfg, load_package_meta(self.cfg, info.package))

    def test_binary_minimum_ios(self):
        binary = macho.build_test_macho("arm64", min_os="15.0")
        p = make_deb(self.work / "t.deb", control(NS + "tweak"), files={"var/jb/Library/x.dylib": (binary, 0o755)})
        info, r = self.validate(p)
        self.assertEqual(info.binary_min_os, "15.0")
        self.assertIn("firmware-undeclared", {c for c, _ in r.warnings})
        p = make_deb(self.work / "t2.deb", control(NS + "tweak", Depends="firmware (>= 14.0)"),
                     files={"var/jb/Library/x.dylib": (binary, 0o755)})
        self.assertIn("firmware-below-binary", {c for c, _ in self.validate(p)[1].warnings})

    def test_mixed_signature_in_app(self):
        team_lib = macho.build_test_macho("arm64", team="59GAB85EFG", filetype=6)

        def add_lib(stage):
            lib = stage / "var/jb/Applications/TestApp.app/Frameworks/libX.dylib"
            lib.parent.mkdir(parents=True)
            lib.write_bytes(team_lib)
            os.chmod(lib, 0o755)

        p = app_deb(self.work)
        # rebuild the same app with an embedded certificate-signed dylib
        info = inspect_deb(p)
        self.assertEqual(info.binaries[0].info.slices[0].team, "")
        d = self.work / "m"
        d.mkdir()
        subprocess.run(["dpkg-deb", "-R", str(p), str(d)], check=True)
        add_lib(d)
        out = self.work / "mixed.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "-b", str(d), str(out)], check=True, capture_output=True)
        _, r = self.validate(out)
        self.assertIn("app-mixed-signatures", {c for c, _ in r.warnings})

    def test_ownership_judged_by_name_like_dpkg(self):
        self.assertTrue(FileEntry("a", "file", 0o644, 501, 20, 1, "", "root", "wheel").root_owned)
        self.assertFalse(FileEntry("a", "file", 0o644, 501, 20, 1, "", "mobile", "mobile").root_owned)
        self.assertFalse(FileEntry("a", "file", 0o644, 501, 20, 1).root_owned)
        self.assertTrue(FileEntry("a", "file", 0o644, 0, 0, 1).root_owned)


class SiteTests(RepoTestCase):
    def test_install_actions_only_for_indexed_packages(self):
        (self.root / "config" / "products.toml").write_text(
            f'[[products]]\nname = "Indexed One"\npackage_ids = ["{NS}testtool"]\nstatus = "x"\nsummary = "s"\n'
            '[[products]]\nname = "Future Thing"\npackage_ids = []\nstatus = "In development"\nsummary = "s"\n')
        self.reload()
        self.write_meta(NS + "testtool", '[display]\nsummary = "A tool."\nlimitations = "- Only a test."\n'
                        'removal = "1. Remove it."\nscreenshots = [{file = "shot.png", caption = "Main screen"}]\n',
                        changelog="## 1.0.0\n\n- First.\n")
        shutil.copyfile(self.root / "branding" / "CydiaIcon.png", self.root / "packages" / (NS + "testtool") / "shot.png")
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work), {"source": "local-file", "repository": "o/r",
                                                                     "commit": SHA})[0], "imported")
        res = build(self.cfg, PROD, self.tmp / "site")
        home = (res.out_dir / "index.html").read_text()
        self.assertIn(f'sileo://package/{NS}testtool', home)
        self.assertIn("Future Thing", home)
        self.assertNotIn("Indexed One", home)               # replaced by the real card
        dev = home.split('id="in-development"')[1].split("</section>")[0]
        self.assertNotIn("sileo://package/", dev)
        self.assertNotIn("Install", dev.replace("installable", "").replace("install action", ""))
        page = (res.out_dir / "repo" / "depictions" / (NS + "testtool") / "index.html").read_text()
        for heading in ("Compatibility", "Screenshots", "Installation", "Limitations", "Changelog", "Removal", "Downloads and checksums"):
            self.assertIn(f">{heading}</h2>", page)
        self.assertIn(f"https://github.com/o/r/commit/{SHA}", page)
        self.assertIn('alt="Main screen"', page)
        dep = json.loads((res.out_dir / "repo" / "depictions" / (NS + "testtool") / "sileo.json").read_text())
        shots = [v for v in dep["tabs"][0]["views"] if v["class"] == "DepictionScreenshotsView"]
        self.assertEqual(shots[0]["screenshots"][0]["accessibilityText"], "Main screen")
        for f in ("assets/brand/favicon.svg", "assets/brand/hcg-repo-banner-2000.webp", "apple-touch-icon.png"):
            self.assertTrue((res.out_dir / f).is_file(), f)

    def test_url_breaks_keep_text(self):
        html = url_breaks("https://repo.hakmicapitalgroup.com/repo/")
        self.assertEqual(html.replace("<wbr>", ""), "https://repo.hakmicapitalgroup.com/repo/")
        self.assertIn("://<wbr>", html)


@unittest.skipUnless(shutil.which("gpg"), "gpg not installed")
class KeyringTests(RepoTestCase):
    def test_keyring_package(self):
        from hcgrepo.keyring import build_keyring_deb
        from hcgrepo.sign import SignError
        with self.assertRaises(SignError):
            build_keyring_deb(self.cfg, "rootless", "1.0", self.work)
        home = tempfile.mkdtemp(prefix="gk", dir="/tmp")
        try:
            env = {**os.environ, "GNUPGHOME": home}
            subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-gen-key", "K <k@example.invalid>", "rsa2048",
                            "sign", "never"], check=True, capture_output=True, env=env)
            fpr = next(l.split(":")[9] for l in subprocess.run(["gpg", "--with-colons", "--list-keys"], capture_output=True,
                       text=True, env=env).stdout.splitlines() if l.startswith("fpr:"))
            (self.root / "keys").mkdir()
            subprocess.run(["gpg", "--armor", "--output", str(self.root / "keys" / "hcg-repo-archive-key.asc"),
                            "--export", fpr], check=True, env=env)
        finally:
            subprocess.run(["gpgconf", "--kill", "all"], capture_output=True, env={**os.environ, "GNUPGHOME": home})
            shutil.rmtree(home, ignore_errors=True)
        cfgp = self.root / "config" / "repo.toml"
        cfgp.write_text(cfgp.read_text().replace('fingerprint = ""', f'fingerprint = "{fpr}"'))
        self.reload()
        for layout, path in (("rootless", "var/jb/etc/apt/trusted.gpg.d/hcg-repo.gpg"), ("rootful", "etc/apt/trusted.gpg.d/hcg-repo.gpg")):
            deb = build_keyring_deb(self.cfg, layout, "1.0", self.work)
            info = inspect_deb(deb)
            self.assertIn(path, [f.path for f in info.files])
            self.assertEqual(info.scripts, {})
            status, rep = import_deb(self.cfg, deb, {"source": "local-file"})
            self.assertEqual(status, "imported", rep.render())


if __name__ == "__main__":
    unittest.main()

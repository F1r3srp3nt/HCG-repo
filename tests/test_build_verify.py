"""End-to-end: build the snapshot, serve it over HTTP under the right prefix, verify like a client.
Includes regressions for path-prefix mistakes and inconsistent or tampered indexes."""

import gzip
import json
import shutil
from pathlib import Path

from support import NS, RepoTestCase, app_deb, tool_deb
from hcgrepo import debian
from hcgrepo.config import normalize_base_url
from hcgrepo.importer import import_deb
from hcgrepo.repo import compress_indexes, build_info_date, render_release
from hcgrepo.server import BackgroundServer
from hcgrepo.site import BuildError, build
from hcgrepo.verify import verify

PAGES = "https://f1r3srp3nt.github.io/HCG-repo/"
PROD = "https://repo.hakmicapitalgroup.com/"
EXE = "var/jb/Applications/TestApp.app/TestApp"


class BuildVerifyTests(RepoTestCase):
    def populate(self):
        self.write_meta(NS + "testapp",
                        f'[display]\nicon = "icon.png"\n[review.entitlements."{EXE}"]\n"platform-application" = true\n',
                        changelog="# Changelog\n\n## 1.0.0\n\n- First test build.\n", icon=True)
        for p in (app_deb(self.work, entitlements={"platform-application": True}), tool_deb(self.work),
                  tool_deb(self.work, arch="iphoneos-arm")):
            status, r = import_deb(self.cfg, p, {"source": "test"})
            self.assertEqual(status, "imported", r.render())

    def build(self, base, name="site"):
        return build(self.cfg, base, self.tmp / name)

    def serve_verify(self, site: Path, prefix: str, public: str, **kw):
        with BackgroundServer(site, prefix) as srv:
            return verify(public, srv.url, cfg=self.cfg, **kw)

    def rewrite_index(self, repo_dir: Path, mutate):
        """Edit Packages and regenerate compressed indexes + Release consistently, so only the
        intended mistake remains."""
        text = (repo_dir / "Packages").read_text()
        text = mutate(text)
        files = compress_indexes(text)
        for n, d in files.items():
            (repo_dir / n).write_bytes(d)
        (repo_dir / "Release").write_text(render_release(self.cfg, files, build_info_date()))

    # --- happy paths
    def test_empty_repository_is_valid(self):
        res = self.build(PAGES)
        self.assertEqual((res.out_dir / "repo" / "Packages").read_bytes(), b"")
        v = self.serve_verify(res.out_dir, "/HCG-repo/", PAGES)
        self.assertTrue(v.report.ok, v.report.render())
        html = (res.out_dir / "index.html").read_text()
        self.assertIn("No packages are published yet", html)
        self.assertNotIn("sileo://package/", html)
        self.assertIn("HCG Terminal Unleashed", html)  # planned, not installable

    def test_populated_repository_under_both_bases(self):
        self.populate()
        for base, prefix in ((PAGES, "/HCG-repo/"), (PROD, "/")):
            with self.subTest(base=base):
                res = self.build(base, name="site-" + prefix.strip("/"))
                packages = (res.out_dir / "repo" / "Packages").read_text()
                self.assertIn(f"SileoDepiction: {base}repo/depictions/{NS}testapp/sileo.json", packages)
                self.assertIn(f"Filename: debs/{NS}testapp_1.0.0_iphoneos-arm64.deb", packages)
                v = self.serve_verify(res.out_dir, prefix, base)
                self.assertTrue(v.report.ok, v.report.render())
                self.assertEqual(v.packages, 3)
                html = (res.out_dir / "index.html").read_text()
                self.assertIn(f'href="sileo://source/{base}repo/"', html)
                self.assertIn(f"sileo://package/{NS}testapp", html)
                dep = json.loads((res.out_dir / "repo" / "depictions" / (NS + "testapp") / "sileo.json").read_text())
                self.assertEqual(dep["class"], "DepictionTabView")
                self.assertIn("Experimental", json.dumps(dep))

    def test_stanza_fields_come_from_control(self):
        self.populate()
        res = self.build(PAGES)
        stanzas = debian.parse_stanzas((res.out_dir / "repo" / "Packages").read_text())
        app = next(s for s in stanzas if debian.field(s, "Package") == NS + "testapp")
        self.assertEqual(debian.field(app, "Depends"), "firmware (>= 15.0), uikittools")
        self.assertEqual(debian.field(app, "Architecture"), "iphoneos-arm64")
        self.assertIsNotNone(debian.field(app, "SHA256"))

    def test_planned_product_replaced_when_indexed(self):
        (self.root / "config" / "products.toml").write_text(
            '[[products]]\nname = "Test App Planned"\npackage_ids = ["%stestapp"]\nstatus = "Soon"\nsummary = "x"\n' % NS)
        self.reload()
        html = (self.build(PAGES).out_dir / "index.html").read_text()
        self.assertIn("Test App Planned", html)
        self.populate()
        html = (self.build(PAGES, "site2").out_dir / "index.html").read_text()
        self.assertNotIn("Test App Planned", html)

    def test_base_url_normalisation(self):
        self.assertEqual(normalize_base_url("https://F1R3SRP3NT.github.io/HCG-repo"), "https://f1r3srp3nt.github.io/HCG-repo/")
        for bad in ("f1r3srp3nt.github.io/HCG-repo/", "https://x/a//b/", "https://x/?q=1", "ftp://x/"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_base_url(bad)

    # --- path-prefix regressions
    def test_built_for_custom_domain_but_served_under_project_prefix(self):
        self.populate()
        res = self.build(PROD)
        v = self.serve_verify(res.out_dir, "/HCG-repo/", PAGES)
        codes = v.report.codes()
        self.assertIn("presentation-url-base", codes)
        self.assertIn("site-sileo-link", codes)

    def test_project_prefix_missing_on_server(self):
        res = self.build(PAGES)
        with BackgroundServer(res.out_dir, "/") as srv:
            # Client uses the documented URL (with /HCG-repo/) against a server that lacks it.
            v = verify(PAGES, srv.url.rstrip("/") + "/HCG-repo/", cfg=self.cfg)
        self.assertIn("release-missing", v.report.codes())

    def test_filename_with_double_prefix(self):
        self.populate()
        res = self.build(PAGES)
        self.rewrite_index(res.out_dir / "repo", lambda t: t.replace("Filename: debs/", "Filename: repo/debs/"))
        v = self.serve_verify(res.out_dir, "/HCG-repo/", PAGES)
        self.assertIn("filename-double-prefix", v.report.codes())
        self.assertIn("deb-download", v.report.codes())

    def test_filename_absolute_path(self):
        self.populate()
        res = self.build(PAGES)
        self.rewrite_index(res.out_dir / "repo", lambda t: t.replace("Filename: debs/", "Filename: /debs/"))
        v = self.serve_verify(res.out_dir, "/HCG-repo/", PAGES)
        self.assertIn("filename-unsafe", v.report.codes())

    # --- index consistency and checksums
    def test_tampered_package_detected(self):
        self.populate()
        res = self.build(PAGES)
        deb = next((res.out_dir / "repo" / "debs").glob("*testtool*arm64.deb"))
        other = self.work / "other"
        other.mkdir()
        shutil.copyfile(tool_deb(other, Section="Changed"), deb)
        v = self.serve_verify(res.out_dir, "/HCG-repo/", PAGES)
        self.assertIn("deb-checksum", v.report.codes())

    def test_compressed_index_inconsistent(self):
        self.populate()
        res = self.build(PAGES)
        repo = res.out_dir / "repo"
        files = compress_indexes((repo / "Packages").read_text())
        files["Packages.gz"] = gzip.compress(b"Package: stale\n", mtime=0)
        for n, d in files.items():
            (repo / n).write_bytes(d)
        (repo / "Release").write_text(render_release(self.cfg, files, build_info_date()))
        v = self.serve_verify(res.out_dir, "/HCG-repo/", PAGES)
        self.assertIn("index-inconsistent", v.report.codes())

    def test_index_changed_after_release(self):
        self.populate()
        res = self.build(PAGES)
        p = res.out_dir / "repo" / "Packages.xz"
        p.write_bytes(p.read_bytes()[:-4] + b"\0\0\0\0")
        v = self.serve_verify(res.out_dir, "/HCG-repo/", PAGES)
        self.assertIn("index-checksum", v.report.codes())

    def test_stale_unlisted_index(self):
        res = self.build(PAGES)
        (res.out_dir / "repo" / "Packages.zst").write_bytes(b"(\xb5/\xfd stale")
        v = self.serve_verify(res.out_dir, "/HCG-repo/", PAGES)
        self.assertIn("index-unlisted", v.report.codes())

    def test_release_missing_architecture(self):
        res = self.build(PAGES)
        rel = res.out_dir / "repo" / "Release"
        rel.write_text(rel.read_text().replace("Architectures: iphoneos-arm64 iphoneos-arm", "Architectures: iphoneos-arm"))
        v = self.serve_verify(res.out_dir, "/HCG-repo/", PAGES)
        self.assertIn("release-arch", v.report.codes())

    # --- build safety
    def test_build_refuses_broken_pool_and_keeps_previous_snapshot(self):
        res = self.build(PAGES)
        marker = res.out_dir / "repo" / "Release"
        before = marker.read_bytes()
        bad = self.root / "pool" / "iphoneos-arm64"
        bad.mkdir(parents=True)
        (bad / "broken.deb").write_bytes(b"!<arch>\ntruncated")
        with self.assertRaises(BuildError):
            self.build(PAGES)
        self.assertEqual(marker.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.name.startswith("site.building")), [])


if __name__ == "__main__":
    import unittest
    unittest.main()

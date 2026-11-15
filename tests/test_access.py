"""Access levels: connect/paid packages are never stored or served publicly; Sileo payment-provider
mode indexes them as commercial without exposing the file; identity, artwork and HTML checks."""

import json
import shutil

from support import NS, RepoTestCase, tool_deb
from hcgrepo.config import Report, load_lock
from hcgrepo.importer import ImportError_, import_deb, protect
from hcgrepo.repo import check_lock_history, check_repository, load_pool
from hcgrepo.server import BackgroundServer
from hcgrepo.site import BuildError, build
from hcgrepo.verify import verify

PROD = "https://repo.hakmicapitalgroup.com/"
PKG = NS + "testtool"
FILE = f"{PKG}_1.0.0_iphoneos-arm64.deb"


class AccessTests(RepoTestCase):
    def set_access(self, level, package=PKG):
        self.write_meta(package, f'[display]\nsummary = "x"\n[access]\nlevel = "{level}"\n')

    def set_cfg(self, old, new):
        p = self.root / "config" / "repo.toml"
        p.write_text(p.read_text().replace(old, new))
        self.reload()

    def validate(self):
        r = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, r), r)
        return r

    def serve_verify(self, site, base=PROD):
        with BackgroundServer(site, "/") as srv:
            return verify(base, srv.url, cfg=self.cfg)

    def test_strict_mode_requires_declaration(self):
        self.set_cfg("require_explicit = false", "require_explicit = true")
        status, r = import_deb(self.cfg, tool_deb(self.work), {"source": "t"})
        self.assertEqual(status, "rejected")
        self.assertIn("access-undeclared", r.codes())
        self.set_access("public")
        status, r = import_deb(self.cfg, tool_deb(self.work), {"source": "t"})
        self.assertEqual(status, "imported", r.render())
        self.assertTrue(self.validate().ok, self.validate().render())

    def test_protected_import_never_stores_the_file(self):
        self.set_access("paid")
        status, r = import_deb(self.cfg, tool_deb(self.work), {"source": "t"})
        self.assertEqual(status, "protected", r.render())
        self.assertFalse(list(self.root.glob("pool/**/*.deb")))
        e = load_lock(self.cfg)["packages"][0]
        self.assertEqual(e["status"], "protected")
        prov = json.loads((self.root / (e["filename"] + ".provenance.json")).read_text())
        self.assertIn("Package: " + PKG, prov["control"])
        self.assertTrue(self.validate().ok)

    def test_build_without_endpoint_excludes_and_does_not_expose(self):
        (self.root / "config" / "products.toml").write_text(
            f'[[products]]\nkey = "tool"\nname = "Tool"\nstore_slug = "tool"\npackage_ids = ["{PKG}"]\naccess = "paid"\n'
            'status = "x"\nsummary = "s"\n')
        self.set_access("paid")
        import_deb(self.cfg, tool_deb(self.work), {"source": "t"})
        res = build(self.cfg, PROD, self.tmp / "site")
        self.assertEqual((res.out_dir / "repo" / "Packages").read_text(), "")
        self.assertFalse([f for f in res.out_dir.rglob("*") if f.name == FILE])
        home = (res.out_dir / "index.html").read_text()
        self.assertNotIn(f"sileo://package/{PKG}", home)
        self.assertIn('id="sign-in-required"', home)
        self.assertIn("Paid · HCG Store", home)
        self.assertFalse((res.out_dir / "repo" / "payment_endpoint").exists())
        v = self.serve_verify(res.out_dir)
        self.assertTrue(v.report.ok, v.report.render())

    def test_payment_endpoint_mode_indexes_commercial_without_file(self):
        self.set_access("connect")
        import_deb(self.cfg, tool_deb(self.work), {"source": "t"})
        self.set_cfg('payment_endpoint = ""', 'payment_endpoint = "https://connect.example.invalid/sileo"')
        res = build(self.cfg, PROD, self.tmp / "site")
        packages = (res.out_dir / "repo" / "Packages").read_text()
        self.assertIn(f"Package: {PKG}", packages)
        self.assertIn("Tag: cydia::commercial", packages)
        self.assertIn(f"Filename: protected/{FILE}", packages)
        self.assertEqual((res.out_dir / "repo" / "payment_endpoint").read_text(), "https://connect.example.invalid/sileo/\n")
        self.assertFalse([f for f in res.out_dir.rglob("*") if f.name == FILE])
        v = self.serve_verify(res.out_dir)
        self.assertTrue(v.report.ok, v.report.render())
        # Regression: if the file ever lands at its public path, verification must fail.
        (res.out_dir / "repo" / "protected").mkdir()
        shutil.copyfile(tool_deb(self.work), res.out_dir / "repo" / "protected" / FILE)
        self.assertIn("protected-exposed", self.serve_verify(res.out_dir).report.codes())

    def test_insecure_payment_endpoint_refused(self):
        with self.assertRaises(Exception):
            self.set_cfg('payment_endpoint = ""', 'payment_endpoint = "http://connect.example.invalid/"')

    def test_protect_moves_existing_public_package_out(self):
        import_deb(self.cfg, tool_deb(self.work), {"source": "t"})
        self.set_access("connect")
        self.assertIn("protected-in-public-pool", self.validate().codes())
        with self.assertRaises(ImportError_):
            protect(self.cfg, PKG, "1.0.0", "iphoneos-arm64", self.root / "inside")
        base_lock = load_lock(self.cfg)
        dest = protect(self.cfg, PKG, "1.0.0", "iphoneos-arm64", self.tmp / "outside")
        self.assertTrue(dest.is_file())
        self.assertFalse(list(self.root.glob("pool/**/*.deb")))
        self.assertTrue(self.validate().ok, self.validate().render())
        r = Report("x")
        check_lock_history(base_lock, load_lock(self.cfg), r)
        self.assertTrue(r.ok, r.render())

    def test_withdrawn_cannot_become_protected(self):
        import_deb(self.cfg, tool_deb(self.work), {"source": "t"})
        base = load_lock(self.cfg)
        base["packages"][0]["status"] = "withdrawn"
        head = json.loads(json.dumps(base))
        head["packages"][0]["status"] = "protected"
        r = Report("x")
        check_lock_history(base, head, r)
        self.assertIn("lock-history-republished", r.codes())


class IdentityAndArtworkTests(RepoTestCase):
    def built(self):
        return build(self.cfg, PROD, self.tmp / "site").out_dir

    def check(self, site):
        with BackgroundServer(site, "/") as srv:
            return verify(PROD, srv.url, cfg=self.cfg).report

    def test_identity_icons_and_featured_banner(self):
        site = self.built()
        rel = (site / "repo" / "Release").read_text()
        self.assertIn("Origin: HCG Repo\n", rel)
        self.assertIn("Label: HCG Repo\n", rel)
        feat = json.loads((site / "repo" / "sileo-featured.json").read_text())
        self.assertEqual(feat["itemSize"], "{330, 110}")      # 3:1, same as the banner, so aspect-fill crops nothing
        self.assertFalse(feat["banners"][0]["displayText"])
        self.assertTrue(self.check(site).ok)

    def test_wrong_identity_detected(self):
        site = self.built()
        p = site / "repo" / "Release"
        p.write_text(p.read_text().replace("Origin: HCG Repo", "Origin: Untitled"))
        self.assertIn("release-identity", self.check(site).codes())

    def test_html_instead_of_release(self):
        site = self.built()
        (site / "repo" / "Release").write_text("<!DOCTYPE html><html><body>Sign in</body></html>")
        self.assertIn("release-html", self.check(site).codes())

    def test_missing_icon_and_bad_featured(self):
        site = self.built()
        (site / "repo" / "CydiaIcon@3x.png").unlink()
        f = site / "repo" / "sileo-featured.json"
        d = json.loads(f.read_text())
        d["banners"][0]["url"] = "https://elsewhere.example/banner.jpg"
        f.write_text(json.dumps(d))
        codes = self.check(site).codes()
        self.assertIn("repo-icon", codes)
        self.assertIn("featured-url-base", codes)


if __name__ == "__main__":
    import unittest
    unittest.main()

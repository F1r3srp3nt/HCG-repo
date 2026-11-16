"""HCG Vortex: a free experimental beta, public by an explicit exception.

The listing is prepared before any release: it must be valid, visible as "not yet released", and offer
nothing to install. Once a .deb is imported, the index and depictions must show exactly that .deb's
identity and checksums, a Beta badge, the device-test record as it is (never "working" without one),
and its known issues. Other products keep their access levels. The fixture .deb here only stands in for
the real artifact inside the test; it is never added to the repository."""

import json
import re
import shutil

from support import REPO, RepoTestCase, tool_deb
from hcgrepo import debian
from hcgrepo.config import load_package_meta
from hcgrepo.debinspect import inspect_deb
from hcgrepo.importer import import_deb
from hcgrepo.server import BackgroundServer
from hcgrepo.site import build
from hcgrepo.verify import verify

PROD = "https://repo.hakmicapitalgroup.com/"
PKG = "com.hakmicapitalgroup.vortex"
WORKING = re.compile(r"\b(works|working|device-tested|verified working)\b", re.I)


class VortexTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(REPO / "packages" / PKG, self.root / "packages" / PKG)
        p = self.root / "config" / "repo.toml"
        p.write_text(p.read_text().replace("require_explicit = false", "require_explicit = true"))
        self.reload()

    def site(self, name="site"):
        return build(self.cfg, PROD, self.tmp / name).out_dir

    def test_prepared_listing_before_any_release(self):
        site = self.site()
        self.assertEqual((site / "repo" / "Packages").read_text(), "")
        page = (site / "products" / "hcg-vortex" / "index.html").read_text()
        self.assertIn("Experimental beta, not yet released", page)
        self.assertIn("Downloads are not available yet.", page)
        self.assertNotIn("sileo://package/", page)
        self.assertNotIn("store.hakmicapitalgroup.com", page)      # not in HCG Store: no Store actions
        self.assertIn('href="products/hcg-vortex/"', (site / "index.html").read_text())
        meta = load_package_meta(self.cfg, PKG)
        self.assertEqual((meta.channel, meta.access), ("beta", "public"))
        self.assertTrue(meta.access_exception and meta.known_issues)

    def test_exception_is_required_and_scoped(self):
        toml = self.root / "packages" / PKG / "package.toml"
        toml.write_text(re.sub(r"(?m)^exception = .*\n", "", toml.read_text()))
        from hcgrepo.config import Report
        from hcgrepo.repo import check_repository
        r = Report("repository")
        check_repository(self.reload(), [], r)
        self.assertIn("access-public-exception", r.codes())
        # Nothing else became public: the other products keep their access levels.
        levels = {p.key: p.access for p in self.cfg.products}
        self.assertEqual((levels["hcg-nexus"], levels["hcg-terminal-unleashed"], levels["hcg-vortex"]), ("paid", "connect", "public"))
        self.assertEqual(load_package_meta(self.cfg, "com.hakmicapitalgroup.hcgnexus").access, "public")  # no metadata in this test root
        self.assertEqual({p.key for p in self.cfg.products if not p.in_store}, {"hcg-vortex"})

    def test_imported_beta_listing_matches_the_deb_and_claims_nothing(self):
        deb = tool_deb(self.work, package=PKG, version="0.1.0~beta1", Depends="firmware (>= 15.0)")
        info = inspect_deb(deb)
        status, rep = import_deb(self.cfg, deb, {"source": "test-fixture"},
                                 expect={"package": PKG, "version": "0.1.0~beta1", "architecture": "iphoneos-arm64", "sha256": info.sha256})
        self.assertEqual(status, "imported", rep.render())
        site = self.site()
        st = debian.parse_stanzas((site / "repo" / "Packages").read_text())[0]
        for k in ("Package", "Version", "Architecture", "Depends"):
            self.assertEqual(debian.field(st, k), info.get(k), k)
        self.assertEqual((debian.field(st, "SHA256"), int(debian.field(st, "Size"))), (info.sha256, info.size))
        self.assertEqual((site / "repo" / debian.field(st, "Filename")).read_bytes(), deb.read_bytes())

        web = (site / "repo" / "depictions" / PKG / "index.html").read_text()
        self.assertIn('class="badge badge-beta">Beta<', web)
        self.assertIn("Not yet tested on any device.", web)
        self.assertIn("Known issues", web)
        self.assertNotRegex(re.sub(r"<[^>]+>", " ", web.split("Known issues")[0]), WORKING)
        dep = json.loads((site / "repo" / "depictions" / PKG / "sileo.json").read_text())
        text = json.dumps(dep)
        self.assertIn("Beta, experimental, not yet tested on any device", text)
        self.assertIn("Known issues", text)
        card = (site / "index.html").read_text()
        self.assertIn("Free · no sign-in", card)
        self.assertIn(f"sileo://package/{PKG}", card)
        with BackgroundServer(site, "/") as srv:
            v = verify(PROD, srv.url, cfg=self.cfg)
        self.assertTrue(v.report.ok, v.report.render())

    def test_candidate_source_lets_sileo_refresh_be_tested_before_publication(self):
        import dataclasses
        from hcgrepo.diagnostics import CANDIDATE, build_variant, variant_cfg
        deb = tool_deb(self.work, package=PKG, version="0.1.0~beta1")
        self.assertEqual(import_deb(self.cfg, deb, {"source": "test-fixture"})[0], "imported")
        site = self.site()
        d = build_variant(self.cfg, PROD, site, CANDIDATE)
        st = debian.parse_stanzas((d / "Packages").read_text())[0]
        self.assertEqual(debian.field(st, "SHA256"), inspect_deb(deb).sha256)
        self.assertTrue(debian.field(st, "SileoDepiction").startswith(f"{PROD}diag/d8-candidate/depictions/{PKG}/"))
        self.assertEqual((d / debian.field(st, "Filename")).read_bytes(), deb.read_bytes())
        with BackgroundServer(site, "/") as srv:
            v = verify(PROD, srv.url, cfg=variant_cfg(self.cfg, CANDIDATE, PROD), site_checks=False)
        self.assertTrue(v.report.ok, v.report.render())
        self.assertEqual(v.packages, 1)

    def test_device_results_are_shown_exactly(self):
        deb = tool_deb(self.work, package=PKG, version="0.1.0~beta1")
        self.assertEqual(import_deb(self.cfg, deb, {"source": "test-fixture"})[0], "imported")
        toml = self.root / "packages" / PKG / "package.toml"
        toml.write_text(toml.read_text() + '''
[versions."0.1.0~beta1"]
status = "experimental"
[[versions."0.1.0~beta1".device_tests]]
device = "iPhone 13 Pro (A15)"
ios = "16.5"
jailbreak = "Dopamine 2.x (rootless)"
date = "2026-11-20"
result = "fail"
evidence = "docs/device-tests/2026-11-20-vortex.md"
''')
        from hcgrepo.config import Report, check_package_meta
        r = Report("meta")
        check_package_meta(load_package_meta(self.reload(), PKG), r)
        self.assertIn("meta-known-issues-stale", r.codes())   # a failed test must be described
        toml.write_text(toml.read_text().replace("- None recorded yet.", "- Crashes SpringBoard on iPhone 13 Pro, iOS 16.5 (Dopamine)."))
        self.reload()
        site = self.site("site2")
        web = (site / "repo" / "depictions" / PKG / "index.html").read_text()
        self.assertIn("iPhone 13 Pro (A15)", web)
        self.assertIn("Does not work", web)
        self.assertNotIn("Not yet tested on any device.", web)
        self.assertIn("none passed", web)
        self.assertIn("Crashes SpringBoard", web)
        self.assertIn('badge-warn">Experimental<', web)     # a failed test never makes it device-tested
        dep = json.dumps(json.loads((site / "repo" / "depictions" / PKG / "sileo.json").read_text()))
        self.assertIn("iOS 16.5, Dopamine 2.x (rootless): Does not work", dep)


if __name__ == "__main__":
    import unittest
    unittest.main()

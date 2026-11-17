"""HCG Repo Candidate: the testing channel at <source>/candidate/. It is an independent source built by every
ordinary build, it never leaks into the stable index, it carries only public packages, and its web depictions
load their stylesheet from where they actually are (the Zebra recording showed unstyled pages at a deeper path)."""

import json
import shutil

from support import NS, RepoTestCase, tool_deb
from hcgrepo import debian
from hcgrepo.config import Report, load_lock
from hcgrepo.importer import ImportError_, import_deb, set_channel
from hcgrepo.repo import check_lock_history
from hcgrepo.server import BackgroundServer
from hcgrepo.site import BuildError, build
from hcgrepo.verify import verify

PROD = "https://repo.hakmicapitalgroup.com/"
PKG = NS + "testtool"


class CandidateTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.write_meta(PKG, '[display]\nsummary = "x"\nknown_issues = "- none"\n[release]\nchannel = "beta"\n'
                             '[access]\nlevel = "public"\nexception = "Free test beta (owner, 17 Nov 2026)."\n'
                             'exception_versions = ["1.0.0~beta1"]\n')
        self.reload()
        self.deb = tool_deb(self.work, version="1.0.0~beta1")
        self.assertEqual(import_deb(self.cfg, self.deb, {"source": "t"})[0], "imported")
        set_channel(self.cfg, PKG, "1.0.0~beta1", "iphoneos-arm64", "candidate")
        self.reload()

    def test_candidate_is_independent_and_never_in_the_stable_index(self):
        site = build(self.cfg, PROD, self.tmp / "site").out_dir
        stable, cand = site / "repo", site / "repo" / "candidate"
        self.assertEqual((stable / "Packages").read_text(), "")
        self.assertEqual([f.name for f in (stable / "debs").iterdir()], [])
        st = debian.parse_stanzas((cand / "Packages").read_text())
        self.assertEqual([(debian.field(s, "Package"), debian.field(s, "Version")) for s in st], [(PKG, "1.0.0~beta1")])
        self.assertEqual((cand / debian.field(st[0], "Filename")).read_bytes(), self.deb.read_bytes())
        self.assertTrue(debian.field(st[0], "SileoDepiction").startswith(f"{PROD}repo/candidate/depictions/{PKG}/"))
        rel = (cand / "Release").read_text()
        self.assertIn("Origin: HCG Repo Candidate\n", rel)
        self.assertIn("Label: HCG Repo Candidate\n", rel)
        for icon in ("CydiaIcon.png", "CydiaIcon@2x.png", "CydiaIcon@3x.png"):     # the official source icon
            self.assertEqual((cand / icon).read_bytes(), (self.root / "branding" / icon).read_bytes())
        featured = json.loads((cand / "sileo-featured.json").read_text())
        self.assertEqual(featured["banners"][0]["url"], f"{PROD}repo/candidate/assets/hcg-repo-banner.jpg")
        self.assertTrue((cand / "assets" / "hcg-repo-banner.jpg").is_file())
        page = (cand / "index.html").read_text()
        self.assertIn(f"sileo://source/{PROD}repo/candidate/", page)
        self.assertIn("diag/d8-candidate", page)                       # migration instructions
        self.assertIn("0.1.0~beta1", page)                              # version ordering explained
        self.assertIn('href="../../assets/site.css"', page)
        home = (site / "index.html").read_text()
        self.assertIn('id="candidate"', home)
        self.assertNotIn("d8", home)
        with BackgroundServer(site, "/") as srv:
            self.assertTrue(verify(PROD, srv.url, cfg=self.cfg).report.ok)
            v = verify(PROD, srv.url, cfg=self.cfg.candidate(), site_checks=False)
            self.assertTrue(v.report.ok, v.report.render())
            self.assertEqual(v.packages, 1)

    def test_candidate_web_depiction_loads_its_stylesheet(self):
        site = build(self.cfg, PROD, self.tmp / "site").out_dir
        dep = (site / "repo" / "candidate" / "depictions" / PKG / "index.html").read_text()
        self.assertIn('<link rel="stylesheet" href="../../../../assets/site.css">', dep)
        (site / "assets" / "site.css").unlink()      # regression: a stylesheet that does not load is an error
        with BackgroundServer(site, "/") as srv:
            codes = verify(PROD, srv.url, cfg=self.cfg.candidate(), site_checks=False).report.codes()
        self.assertIn("depiction-stylesheet", codes)

    def test_protected_packages_can_never_be_candidates(self):
        other = NS + "other"
        self.write_meta(other, '[display]\nsummary = "x"\n[access]\nlevel = "paid"\n')
        self.reload()
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work, package=other), {"source": "t"})[0], "protected")
        with self.assertRaises(ImportError_):
            set_channel(self.cfg, other, "1.0.0", "iphoneos-arm64", "candidate")

    def test_stable_builds_cannot_move_back_to_candidate(self):
        lock = load_lock(self.cfg)
        stable = json.loads(json.dumps(lock))
        for e in stable["packages"]:
            e.pop("channel", None)
        r = Report("history")
        check_lock_history(stable, lock, r)
        self.assertIn("lock-history-channel", r.codes())
        r = Report("history")
        check_lock_history(lock, stable, r)                 # candidate -> stable (promotion) is allowed
        self.assertTrue(r.ok, r.render())

    def test_same_build_in_both_channels_is_refused(self):
        shutil.copy(self.root / "repo-lock.json", self.tmp / "lock.bak")
        lock = load_lock(self.cfg)
        dup = dict(lock["packages"][0]); dup.pop("channel")
        lock["packages"].append(dup)
        (self.root / "repo-lock.json").write_text(json.dumps(lock))
        with self.assertRaises(BuildError):
            build(self.reload(), PROD, self.tmp / "site")


if __name__ == "__main__":
    import unittest
    unittest.main()

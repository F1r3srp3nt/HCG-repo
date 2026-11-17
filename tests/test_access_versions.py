"""Per-version access: a paid package (like HCG Nexus) keeps its releases paid while one owner-approved candidate
build is public. The exception covers exactly the listed versions; withdrawing the approval, or contradicting a
protected record, is a validation error."""

from support import NS, RepoTestCase, tool_deb
from hcgrepo import debian
from hcgrepo.config import Report, load_lock, load_package_meta
from hcgrepo.importer import ImportError_, import_deb, protect, set_channel
from hcgrepo.repo import check_repository, load_pool
from hcgrepo.site import build

PROD = "https://repo.hakmicapitalgroup.com/"
PKG = NS + "testtool"
PAID_WITH_BETA = ('[display]\nsummary = "x"\nknown_issues = "- not yet device-tested"\n[release]\nchannel = "beta"\n'
                  '[access]\nlevel = "paid"\nexception = "Free candidate beta (owner, 17 Nov 2026)."\n'
                  'exception_versions = ["1.0.1~beta1"]\n')


class PerVersionAccessTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.write_meta(PKG, PAID_WITH_BETA)
        self.reload()

    def validate(self):
        r = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, r), r)
        return r

    def test_access_for(self):
        meta = load_package_meta(self.cfg, PKG)
        self.assertEqual(meta.access, "paid")
        self.assertEqual(meta.access_for("1.0.1~beta1"), "public")
        self.assertEqual(meta.access_for("1.0.0"), "paid")
        self.assertEqual(meta.access_for("1.0.1"), "paid")         # the release after the beta stays paid
        self.assertEqual(meta.access_for("1.0.1~beta2"), "paid")   # a later beta needs its own approval

    def test_paid_release_protected_and_approved_beta_public_in_candidate(self):
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work, version="1.0.0"), {"source": "t"})[0], "protected")
        beta = tool_deb(self.work, version="1.0.1~beta1")
        status, r = import_deb(self.cfg, beta, {"source": "t"}, channel="candidate")
        self.assertEqual(status, "imported", r.render())
        self.reload()
        self.assertTrue(self.validate().ok, self.validate().render())
        stored = sorted(p.name for p in self.root.glob("pool/**/*.deb"))
        self.assertEqual(stored, [f"{PKG}_1.0.1~beta1_iphoneos-arm64.deb"])   # the paid release is never stored
        statuses = {(e["version"], e.get("status"), e.get("channel")) for e in load_lock(self.cfg)["packages"]}
        self.assertEqual(statuses, {("1.0.0", "protected", None), ("1.0.1~beta1", "published", "candidate")})
        site = build(self.cfg, PROD, self.tmp / "site").out_dir
        self.assertEqual((site / "repo" / "Packages").read_text(), "")       # stable: paid release not indexed
        cand = debian.parse_stanzas((site / "repo" / "candidate" / "Packages").read_text())
        self.assertEqual([(debian.field(s, "Package"), debian.field(s, "Version")) for s in cand], [(PKG, "1.0.1~beta1")])
        self.assertFalse([f for f in site.rglob("*") if f.name == f"{PKG}_1.0.0_iphoneos-arm64.deb"])

    def test_unapproved_version_stays_protected(self):
        status, _ = import_deb(self.cfg, tool_deb(self.work, version="1.0.1~beta2"), {"source": "t"})
        self.assertEqual(status, "protected")
        self.assertFalse(list(self.root.glob("pool/**/*.deb")))

    def test_withdrawing_the_approval_is_an_error(self):
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work, version="1.0.1~beta1"), {"source": "t"}, channel="candidate")[0], "imported")
        self.write_meta(PKG, PAID_WITH_BETA.replace('"1.0.1~beta1"', '"1.0.1~beta9"'))
        self.reload()
        self.assertIn("protected-in-public-pool", self.validate().codes())

    def test_protected_record_cannot_be_made_public(self):
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work, version="1.0.0"), {"source": "t"})[0], "protected")
        self.write_meta(PKG, PAID_WITH_BETA.replace('["1.0.1~beta1"]', '["1.0.1~beta1", "1.0.0"]'))
        self.reload()
        self.assertIn("protected-but-public", self.validate().codes())

    def test_protect_refuses_an_approved_public_version(self):
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work, version="1.0.1~beta1"), {"source": "t"}, channel="candidate")[0], "imported")
        with self.assertRaises(ImportError_):
            protect(self.cfg, PKG, "1.0.1~beta1", "iphoneos-arm64", self.tmp / "out")

    def test_free_demo_cannot_be_imported_into_stable(self):
        status, r = import_deb(self.cfg, tool_deb(self.work, version="1.0.1~beta1"), {"source": "t"})
        self.assertEqual(status, "rejected")
        self.assertIn("free-demo-stable", r.codes())
        self.assertFalse(list(self.root.glob("pool/**/*.deb")))

    def test_free_demo_cannot_be_promoted_to_stable(self):
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work, version="1.0.1~beta1"), {"source": "t"}, channel="candidate")[0], "imported")
        with self.assertRaises(ImportError_):
            set_channel(self.cfg, PKG, "1.0.1~beta1", "iphoneos-arm64", "stable")
        lock = load_lock(self.cfg)                   # a hand-edited lock is caught by validation
        for e in lock["packages"]:
            e.pop("channel", None)
        (self.root / "repo-lock.json").write_text(__import__("json").dumps(lock))
        self.reload()
        self.assertIn("free-demo-stable", self.validate().codes())

    def test_exception_still_needs_versions(self):
        self.write_meta(PKG, PAID_WITH_BETA.replace('exception_versions = ["1.0.1~beta1"]\n', ''))
        self.reload()
        self.assertIn("meta-exception-scope", self.validate().codes())

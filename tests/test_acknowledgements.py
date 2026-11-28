"""[[validation.acknowledged]]: reviewed acknowledgements of warnings for exact published versions."""

import unittest

from support import NS, REPO, RepoTestCase
from hcgrepo.config import Report, acknowledge_warnings, check_package_meta, load_config, load_package_meta
from hcgrepo.repo import check_repository, load_pool

PKG = NS + "testtool"
ACK = ('[[validation.acknowledged]]\ncode = "firmware-undeclared"\nversions = ["1.0.0"]\nreason = "published, immutable"\n'
       'reviewed_by = "test"\ndate = "2026-11-28"\n')


class Acknowledgements(RepoTestCase):
    def report(self):
        r = Report(PKG)
        r.warn("firmware-undeclared", "binaries require iOS 15.0 or later")
        r.warn("legacy-lzma-member", "data.tar.lzma")
        r.error("firmware-undeclared", "an error is never acknowledged")
        return r

    def test_only_the_exact_version_code_and_warnings_are_acknowledged(self):
        self.write_meta(PKG, '[display]\nsummary = "x"\n' + ACK)
        meta = load_package_meta(self.reload(), PKG)
        other = self.report()
        acknowledge_warnings(meta, "1.0.1", other)
        self.assertEqual([lvl for lvl, *_ in other.items], ["warning", "warning", "error"])
        r = self.report()
        acknowledge_warnings(meta, "1.0.0", r)
        self.assertEqual([(lvl, c) for lvl, c, _ in r.items],
                         [("acknowledged", "firmware-undeclared"), ("warning", "legacy-lzma-member"), ("error", "firmware-undeclared")])
        self.assertIn("published, immutable", r.items[0][2])

    def test_incomplete_acknowledgement_is_an_error(self):
        self.write_meta(PKG, '[display]\nsummary = "x"\n[[validation.acknowledged]]\ncode = "x"\nversions = ["1.0.0"]\n')
        r = Report(PKG)
        check_package_meta(load_package_meta(self.reload(), PKG), r)
        self.assertIn("meta-bad-acknowledgement", r.codes())

    def test_production_repository_has_no_open_warnings(self):
        cfg = load_config(REPO)
        r = Report("repository")
        check_repository(cfg, load_pool(cfg, r), r)
        self.assertEqual([(c, m) for lvl, c, m in r.items if lvl != "acknowledged"], [])
        self.assertEqual(sum(1 for lvl, *_ in r.items if lvl == "acknowledged"), 8)


if __name__ == "__main__":
    unittest.main()

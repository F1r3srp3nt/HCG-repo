"""Import, immutability, duplicates, dependencies and the lock."""

import json
import shutil

from support import NS, RepoTestCase, tool_deb
from hcgrepo.config import Report, load_lock
from hcgrepo.importer import import_deb, withdraw
from hcgrepo.repo import check_lock_history, check_repository, load_pool

PROV = {"source": "local-file"}


class RepoTests(RepoTestCase):
    def validate(self):
        r = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, r), r)
        return r

    def test_import_records_pool_lock_and_provenance(self):
        status, r = import_deb(self.cfg, tool_deb(self.work), PROV)
        self.assertEqual(status, "imported", r.render())
        rel = f"pool/iphoneos-arm64/{NS}testtool_1.0.0_iphoneos-arm64.deb"
        self.assertTrue((self.root / rel).is_file())
        prov = json.loads((self.root / (rel + ".provenance.json")).read_text())
        self.assertEqual(prov["source"], "local-file")
        self.assertEqual(prov["sha256"], load_lock(self.cfg)["packages"][0]["sha256"])
        self.assertTrue(self.validate().ok)

    def test_reimport_identical_is_noop(self):
        p = tool_deb(self.work)
        import_deb(self.cfg, p, PROV)
        status, _ = import_deb(self.cfg, p, PROV)
        self.assertEqual(status, "already-present")
        self.assertEqual(len(load_lock(self.cfg)["packages"]), 1)

    def test_restoring_missing_file_does_not_duplicate_lock(self):
        p = tool_deb(self.work)
        import_deb(self.cfg, p, PROV)
        rel = load_lock(self.cfg)["packages"][0]["filename"]
        (self.root / rel).unlink()
        self.assertIn("lock-file-missing", self.validate().codes())
        status, r = import_deb(self.cfg, p, PROV)
        self.assertEqual(status, "imported", r.render())
        self.assertEqual(len(load_lock(self.cfg)["packages"]), 1)
        self.assertTrue(self.validate().ok)

    def test_published_version_is_immutable(self):
        import_deb(self.cfg, tool_deb(self.work), PROV)
        other = self.work / "other"
        other.mkdir()
        changed = tool_deb(other, Section="Changed")  # same package/version/arch, different bytes
        status, r = import_deb(self.cfg, changed, PROV)
        self.assertEqual(status, "rejected")
        self.assertIn("immutable-version", r.codes())

    def test_tampered_pool_file_detected(self):
        import_deb(self.cfg, tool_deb(self.work), PROV)
        rel = load_lock(self.cfg)["packages"][0]["filename"]
        other = self.work / "other"
        other.mkdir()
        shutil.copyfile(tool_deb(other, Section="Changed"), self.root / rel)
        self.assertIn("lock-content-changed", self.validate().codes())

    def test_conflicting_duplicate_in_pool(self):
        import_deb(self.cfg, tool_deb(self.work), PROV)
        other = self.work / "other"
        other.mkdir()
        dup = self.root / "pool" / "iphoneos-arm64" / "copy.deb"
        shutil.copyfile(tool_deb(other, Section="Changed"), dup)
        codes = self.validate().codes()
        self.assertIn("duplicate-package", codes)
        self.assertIn("pool-noncanonical-name", codes)

    def test_rootless_and_rootful_builds_coexist(self):
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work), PROV)[0], "imported")
        status, r = import_deb(self.cfg, tool_deb(self.work, arch="iphoneos-arm"), PROV)
        self.assertEqual(status, "imported", r.render())
        self.assertTrue(self.validate().ok)

    def test_lfs_pointer_in_pool(self):
        d = self.root / "pool" / "iphoneos-arm64"
        d.mkdir(parents=True)
        (d / "x.deb").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n")
        r = self.validate()
        self.assertIn("deb-unreadable", r.codes())
        self.assertIn("LFS", r.render())

    def test_missing_internal_dependency(self):
        status, r = import_deb(self.cfg, tool_deb(self.work, Depends=NS + "libmissing (>= 1.0)"), PROV)
        self.assertEqual(status, "rejected")
        self.assertIn("dependency-missing", r.codes())

    def test_internal_dependency_satisfied_with_version(self):
        lib = tool_deb(self.work, package=NS + "libcore", version="1.2")
        self.assertEqual(import_deb(self.cfg, lib, PROV)[0], "imported")
        ok = tool_deb(self.work, Depends=NS + "libcore (>= 1.0)")
        self.assertEqual(import_deb(self.cfg, ok, PROV)[0], "imported")
        too_new = tool_deb(self.work, package=NS + "other", Depends=NS + "libcore (>= 2.0)")
        status, r = import_deb(self.cfg, too_new, PROV)
        self.assertEqual(status, "rejected")
        self.assertIn("dependency-missing", r.codes())

    def test_dependency_must_match_architecture(self):
        lib = tool_deb(self.work, package=NS + "libcore", arch="iphoneos-arm")
        import_deb(self.cfg, lib, PROV)
        status, r = import_deb(self.cfg, tool_deb(self.work, Depends=NS + "libcore"), PROV)
        self.assertEqual(status, "rejected", "a rootless package must not be satisfied by a rootful library")

    def test_unknown_external_dependency(self):
        status, r = import_deb(self.cfg, tool_deb(self.work, Depends="some-unlisted-thing"), PROV)
        self.assertEqual(status, "rejected")
        self.assertIn("dependency-missing", r.codes())
        status, r = import_deb(self.cfg, tool_deb(self.work, Depends="firmware (>= 15.0), bash | zsh"), PROV)
        self.assertEqual(status, "imported", r.render())

    def test_device_tested_label_needs_evidence(self):
        self.write_meta(NS + "testtool", '[versions."1.0.0"]\nstatus = "device-tested"\n')
        status, r = import_deb(self.cfg, tool_deb(self.work), PROV)
        self.assertEqual(status, "rejected")
        self.assertIn("meta-device-tested-without-evidence", r.codes())

    def test_withdraw_keeps_lock_and_blocks_reuse(self):
        import_deb(self.cfg, tool_deb(self.work), PROV)
        withdraw(self.cfg, NS + "testtool", "1.0.0", "iphoneos-arm64", "test")
        self.assertTrue(self.validate().ok)
        other = self.work / "other"
        other.mkdir()
        status, r = import_deb(self.cfg, tool_deb(other, Section="Changed"), PROV)
        self.assertEqual(status, "rejected")

    def test_lock_history(self):
        import_deb(self.cfg, tool_deb(self.work), PROV)
        base = load_lock(self.cfg)
        head = json.loads(json.dumps(base))
        head["packages"][0]["sha256"] = "0" * 64
        r = Report("x")
        check_lock_history(base, head, r)
        self.assertIn("lock-history-changed", r.codes())
        r = Report("x")
        check_lock_history(base, {"schema": 1, "packages": []}, r)
        self.assertIn("lock-history-removed", r.codes())
        withdrawn = json.loads(json.dumps(base))
        withdrawn["packages"][0]["status"] = "withdrawn"
        r = Report("x")
        check_lock_history(base, withdrawn, r)
        self.assertTrue(r.ok)
        r = Report("x")
        check_lock_history(withdrawn, base, r)
        self.assertIn("lock-history-republished", r.codes())


if __name__ == "__main__":
    import unittest
    unittest.main()

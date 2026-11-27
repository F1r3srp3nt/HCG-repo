"""HCG Store releases -> HCG Repo. Only an artifact a person approved for PUBLIC delivery to HCG Repo,
mapped to the same product, edition and package, with the same version, architecture, size and
SHA-256, and distributed under the same access rule, may be recorded. Recording never publishes."""

import copy
import json

from support import NS, RepoTestCase, set_payment_provider, tool_deb
from hcgrepo import cli
from hcgrepo.config import load_lock
from hcgrepo.debinspect import inspect_deb
from hcgrepo.importer import import_deb
from hcgrepo.store import check_release

PKG = NS + "testtool"


def record(info, **over):
    """The JSON HCG Store returns for GET /api/v1/publishing/releases/<id> (releaseView/artifactView)."""
    art = {
        "kind": "jailbreak-package", "declaredKind": None, "kindCheckedByStore": True, "readiness": {},
        "id": "art-0123456789abcdef", "fileName": f"{info.package}_{info.version}_{info.architecture}.deb",
        "platform": "ios", "format": "deb", "architectures": [info.architecture], "minimumOs": None,
        "sizeBytes": info.size, "declaredSha256": info.sha256, "sha256": info.sha256, "status": "verified",
        "receivedBytes": info.size,
        "validation": {"sha256Matches": True, "receivedSha256": info.sha256, "problems": [], "signatureStructure": "not-checked",
                       "content": {"kind": "jailbreak-package", "problems": [],
                                   "debian": {"package": info.package, "version": info.version,
                                              "architecture": info.architecture, "controlReadable": True}}},
        "signingReported": {"status": "not-checked"},
        "package": {"id": info.package, "version": info.version, "architecture": info.architecture},
        "createdAt": 1, "completedAt": 2,
    }
    rec = {
        "id": "rel-0123456789abcdef", "productId": "test-fixtures", "productTitle": "Test fixtures", "productSource": "catalogue",
        "editionId": None, "version": "1.0.0", "build": "7", "channel": "stable", "releaseNotes": [],
        "compatibility": {"minimumOs": {}, "architectures": [info.architecture], "notes": []},
        "distribution": "free", "requestedEligibility": "public", "destinations": ["hcg-store", "hcg-repo"],
        "status": "approved", "approvedEligibility": "public", "createdBy": "sub-a", "createdAt": 1, "submittedAt": 2,
        "approvedBy": "sub-b", "approvedAt": 3, "withdrawnReason": None,
        "reviewUrl": "https://store.example/publishing/releases/rel-0123456789abcdef", "artifacts": [art],
    }
    for k, v in over.items():
        if k.startswith("art_"):
            art[k[4:]] = v
        else:
            rec[k] = v
    return rec


class StoreReleaseTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.write_meta(PKG, '[display]\nsummary = "x"\n[access]\nlevel = "connect"\n')
        self.reload()
        self.deb = tool_deb(self.work)
        self.info = inspect_deb(self.deb)

    def codes(self, rec, info="same"):
        return check_release(self.cfg, rec, "art-0123456789abcdef", self.info if info == "same" else info).codes()

    def run_cli(self, rec, *extra):
        p = self.tmp / "release.json"
        p.write_text(json.dumps(rec))
        a = cli.build_parser().parse_args(["import-store-release", "--release", str(p), "--artifact",
                                           "art-0123456789abcdef", "--deb", str(self.deb), *extra])
        return a.fn(a, self.reload())

    def test_approved_public_release_is_recorded_as_protected_only(self):
        self.assertEqual(self.codes(record(self.info)), set())
        self.assertEqual(self.run_cli(record(self.info)), 0)
        entry = load_lock(self.reload())["packages"][0]
        self.assertEqual((entry["package"], entry["status"], entry["sha256"]), (PKG, "protected", self.info.sha256))
        self.assertFalse(list((self.root / "pool").rglob("*.deb")))  # the file is never stored here
        prov = json.loads(next((self.root / "pool").rglob("*.provenance.json")).read_text())
        self.assertEqual((prov["source"], prov["store_release"], prov["store_artifact_sha256"]),
                         ("hcg-store", "rel-0123456789abcdef", self.info.sha256))

    def test_unapproved_or_non_public_releases_are_refused(self):
        cases = {
            "store-release-not-approved": [record(self.info, status=s, approvedEligibility=None) for s in ("draft", "submitted")],
            "store-release-withdrawn": [record(self.info, status="withdrawn", withdrawnReason="regression")],
            "store-release-not-public": [record(self.info, approvedEligibility=e) for e in ("internal-testing", "private")],
            "store-destination": [record(self.info, destinations=["hcg-store"])],
            "store-draft-product": [record(self.info, productSource="draft")],
        }
        for code, recs in cases.items():
            for rec in recs:
                self.assertIn(code, self.codes(rec), rec["status"])
                self.assertNotEqual(self.run_cli(rec), 0)
        self.assertEqual(load_lock(self.reload())["packages"], [])

    def test_rejected_or_wrong_files_are_refused(self):
        self.assertIn("store-artifact-not-verified", self.codes(record(self.info, art_status="rejected")))
        rec = record(self.info)
        rec["artifacts"][0]["validation"]["problems"] = ["unsafe path"]
        self.assertIn("store-artifact-problems", self.codes(rec))
        other = inspect_deb(tool_deb(self.work, version="1.0.0-2"))
        codes = self.codes(record(self.info), other)
        self.assertTrue({"store-file-identity", "store-file-sha256"} <= codes, codes)
        self.assertIn("store-artifact-missing", check_release(self.cfg, record(self.info), "art-ffffffffffffffff").codes())

    def test_mapping_version_and_access_must_agree(self):
        self.assertIn("store-product-mismatch", self.codes(record(self.info, productId="hcg-nexus")))
        self.assertIn("store-edition-mismatch", self.codes(record(self.info, editionId="unleashed")))
        self.assertIn("store-version", self.codes(record(self.info, version="1.1.0")))
        self.assertEqual(self.codes(record(self.info, version="1.0.0")), set())
        self.assertIn("store-access-mismatch", self.codes(record(self.info, distribution="paid")))
        self.write_meta(PKG, '[display]\nsummary = "x"\n[access]\nlevel = "paid"\n')
        self.reload()
        self.assertIn("store-access-mismatch", self.codes(record(self.info, distribution="free")))
        self.assertEqual(self.codes(record(self.info, distribution="entitlement")), set())
        self.write_meta(PKG, '[display]\nsummary = "x"\n[access]\nlevel = "public"\n')
        self.reload()
        self.assertIn("store-access-public", self.codes(record(self.info)))

    def test_existing_protected_entry_keeps_its_build_provenance(self):
        status, _ = import_deb(self.cfg, self.deb, {"source": "source-build", "commit": "a" * 40})
        self.assertEqual(status, "protected")
        self.assertEqual(self.run_cli(record(self.info)), 0)
        prov = json.loads(next((self.root / "pool").rglob("*.provenance.json")).read_text())
        self.assertEqual(prov["source"], "source-build")
        self.assertEqual(prov["hcg_store"]["store_release"], "rel-0123456789abcdef")
        self.assertEqual(len(load_lock(self.reload())["packages"]), 1)

    def test_only_store_approved_protected_packages_are_indexed(self):
        from hcgrepo.site import build
        import_deb(self.cfg, self.deb, {"source": "source-build"})   # protected, but no Store release yet
        set_payment_provider(self.root, "https://store.example/repo-api/v1/sileo/")
        site = build(self.reload(), "https://repo.hakmicapitalgroup.com/", self.tmp / "a").out_dir
        self.assertNotIn(f"Package: {PKG}", (site / "repo" / "Packages").read_text())
        self.assertEqual(self.run_cli(record(self.info)), 0)
        site = build(self.reload(), "https://repo.hakmicapitalgroup.com/", self.tmp / "b").out_dir
        packages = (site / "repo" / "Packages").read_text()
        self.assertIn(f"Package: {PKG}", packages)
        self.assertIn(f"SHA256: {self.info.sha256}", packages)
        self.assertFalse(list(site.rglob("*.deb")))

    def test_dry_run_changes_nothing(self):
        self.assertEqual(self.run_cli(copy.deepcopy(record(self.info)), "--dry-run"), 0)
        self.assertEqual(load_lock(self.reload())["packages"], [])


if __name__ == "__main__":
    import unittest
    unittest.main()

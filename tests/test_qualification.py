"""The HCG Repo qualification source (docs/QUALIFICATION_SILEO.md)."""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import REPO
from hcgrepo import cli
from hcgrepo.config import load_config
from hcgrepo.debinspect import inspect_deb
from hcgrepo.site import build

Q = REPO / "qualification"
FREE = "com.hakmicapitalgroup.qualification.free"
PAID = "com.hakmicapitalgroup.qualification.paid"
BASE = "https://repo.hakmicapitalgroup.com/qualification/54906bcc56/"


def built_packages(out: Path) -> dict[str, Path]:
    subprocess.run([sys.executable, str(REPO / "tools/qualification/build_packages.py"), str(out)], check=True,
                   capture_output=True)
    return {p.name.split("_")[0]: p for p in out.glob("*.deb")}


class QualificationSource(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_packages_are_reproducible_and_match_the_records(self):
        a, b = built_packages(self.tmp / "a"), built_packages(self.tmp / "b")
        sha = {k: hashlib.sha256(v.read_bytes()).hexdigest() for k, v in a.items()}
        self.assertEqual(sha, {k: hashlib.sha256(v.read_bytes()).hexdigest() for k, v in b.items()})
        lock = {e["package"]: e for e in json.loads((Q / "repo-lock.json").read_text())["packages"]}
        self.assertEqual(lock[FREE]["sha256"], sha[FREE])
        self.assertEqual((lock[FREE].get("status", "published"), lock[PAID]["status"]), ("published", "protected"))
        self.assertEqual(lock[PAID]["sha256"], sha[PAID])
        self.assertTrue((Q / lock[FREE]["filename"]).is_file())
        self.assertFalse((Q / lock[PAID]["filename"]).exists())          # paid bytes are never stored here

    def test_configuration_is_separate_from_the_public_sources(self):
        q, pub = load_config(Q), load_config(REPO)
        self.assertEqual((q.origin, q.suite), ("HCG Repo Qualification", "qualification"))
        self.assertEqual(q.payment_endpoint, "https://store.hakmicapitalgroup.com/api/sileo/v1/")
        self.assertEqual(q.store_eligibility, ("internal-testing",))
        self.assertEqual(q.qualification_path, "qualification/54906bcc56/")
        self.assertEqual(q.base_url("qualification"), BASE)
        self.assertEqual((pub.payment_endpoint, pub.store_eligibility), ("", ("public",)))
        self.assertFalse(any(p.startswith("com.hakmicapitalgroup.qualification") for e in
                             json.loads((REPO / "repo-lock.json").read_text())["packages"] for p in [e["package"]]))

    def test_assembly_adds_only_the_unlisted_source(self):
        site = self.tmp / "site"
        (site / "repo").mkdir(parents=True)
        (site / "repo" / "Packages").write_text("public\n")
        r = subprocess.run([sys.executable, str(REPO / "tools/qualification/assemble.py"), "--site", str(site),
                            "--allow-inactive"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((site / "repo" / "Packages").read_text(), "public\n")
        qrepo = site / "qualification/54906bcc56/repo"
        self.assertEqual((qrepo / "payment_endpoint").read_bytes(), b"https://store.hakmicapitalgroup.com/api/sileo/v1/")
        packages = (qrepo / "Packages").read_text()
        self.assertIn(f"Package: {FREE}", packages)
        self.assertNotIn(PAID, packages)                                  # until HCG Store's release is recorded
        self.assertIn("Origin: HCG Repo Qualification", (qrepo / "Release").read_text())
        self.assertIn('content="noindex, nofollow"', (site / "qualification/54906bcc56/index.html").read_text())
        again = subprocess.run([sys.executable, str(REPO / "tools/qualification/assemble.py"), "--site", str(site),
                                "--allow-inactive"], capture_output=True, text=True)
        self.assertNotEqual(again.returncode, 0)                          # never overwrites

    def test_inactive_source_is_not_assembled_without_dry_run(self):
        if load_config(Q).qualification_active:
            self.skipTest("qualification is active")
        site = self.tmp / "s"
        site.mkdir()
        r = subprocess.run([sys.executable, str(REPO / "tools/qualification/assemble.py"), "--site", str(site)],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not approved", r.stdout + r.stderr)

    def test_internal_testing_store_release_lists_the_paid_package(self):
        root = self.tmp / "q"
        shutil.copytree(Q, root, symlinks=False)
        debs = built_packages(self.tmp / "debs")
        info = inspect_deb(debs[PAID])
        art = {"id": "art-00000000000000aa", "kind": "jailbreak-package", "platform": "ios", "format": "deb",
               "architectures": [info.architecture], "sizeBytes": info.size, "sha256": info.sha256, "status": "verified",
               "validation": {"sha256Matches": True, "problems": [], "content": {"debian": {
                   "package": info.package, "version": info.version, "architecture": info.architecture}}},
               "package": {"id": info.package, "version": info.version, "architecture": info.architecture}}
        rec = {"id": "rel-00000000000000aa", "productId": "hcg-repo-qualification", "productSource": "catalogue",
               "editionId": "jailbreak", "version": "1.0.0", "build": "1", "channel": "stable", "distribution": "paid",
               "destinations": ["hcg-repo"], "status": "approved", "approvedEligibility": "internal-testing",
               "approvedAt": 3, "artifacts": [art]}
        rp = self.tmp / "rec.json"

        def record(eligibility, cfg_root):
            rp.write_text(json.dumps(dict(rec, approvedEligibility=eligibility)))
            a = cli.build_parser().parse_args(["import-store-release", "--release", str(rp), "--artifact", art["id"],
                                               "--deb", str(debs[PAID])])
            return a.fn(a, load_config(cfg_root))

        self.assertNotEqual(record("private", root), 0)
        self.assertNotEqual(record("public", root), 0)                    # the qualification source takes internal-testing only
        self.assertEqual(record("internal-testing", root), 0)
        site = build(load_config(root), BASE, self.tmp / "site").out_dir
        stanzas = {s.split("\n", 1)[0]: s for s in (site / "repo" / "Packages").read_text().split("\n\n") if s}
        paid = stanzas[f"Package: {PAID}"]
        self.assertIn("Tag: cydia::commercial", paid)
        self.assertIn(f"Filename: protected/{PAID}_1.0.0_iphoneos-arm64.deb", paid)
        self.assertIn(f"SHA256: {info.sha256}", paid)
        self.assertFalse(list(site.rglob(f"{PAID}_1.0.0_iphoneos-arm64.deb")))


if __name__ == "__main__":
    unittest.main()

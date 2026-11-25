"""Founder-signed release approvals (hcg.release-approval v1) with throw-away keys only."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import NS, RepoTestCase, tool_deb
from hcgrepo import approvals as ap
from hcgrepo.config import Report
from hcgrepo.importer import import_deb
from hcgrepo.repo import check_repository, load_pool

PKG = NS + "testtool"
COMMIT = "c" * 40
HAVE = shutil.which("gpg") and shutil.which("gpgv")


def gen(home, name):
    env = {**os.environ, "GNUPGHOME": home}
    subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-gen-key", f"{name} <{name.split()[-1].lower()}@example.invalid>",
                    "rsa2048", "sign", "never"], check=True, capture_output=True, env=env)
    out = subprocess.run(["gpg", "--batch", "--with-colons", "--list-keys", name], check=True, capture_output=True,
                         text=True, env=env).stdout
    return next(l.split(":")[9] for l in out.splitlines() if l.startswith("fpr:"))


@unittest.skipUnless(HAVE, "gpg/gpgv not installed")
class ApprovalTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.home = tempfile.mkdtemp(prefix="ga", dir="/tmp")
        os.chmod(self.home, 0o700)
        self.env = {**os.environ, "GNUPGHOME": self.home}
        self.approver = gen(self.home, "HCG Founder Approver")
        self.signer = gen(self.home, "HCG Repo Signer")
        self.other = gen(self.home, "Somebody Else")
        (self.root / "keys" / "approvers").mkdir(parents=True)
        for fpr, name in ((self.approver, "approvers/founder.asc"), (self.signer, "hcg-repo-archive-key.asc")):
            subprocess.run(["gpg", "--batch", "--armor", "--output", str(self.root / "keys" / name), "--export", fpr],
                           check=True, capture_output=True, env=self.env)
        self.set_config(approver_keys=["keys/approvers/founder.asc"])
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work),
                                    {"source": "t", "repository": "F1r3srp3nt/Example", "commit": COMMIT})[0], "imported")
        self.sha = next(iter(json.loads((self.root / "repo-lock.json").read_text())["packages"]))["sha256"]

    def tearDown(self):
        subprocess.run(["gpgconf", "--kill", "all"], capture_output=True, env=self.env)
        shutil.rmtree(self.home, ignore_errors=True)
        super().tearDown()

    def set_config(self, approver_keys, required=False, fingerprint=""):
        p = self.root / "config" / "repo.toml"
        text = p.read_text().split("\n[approvals]")[0]
        import re
        text = re.sub(r'fingerprint = "[0-9A-F]*"', f'fingerprint = "{fingerprint}"', text)
        text += ("\n[approvals]\napprover_keys = " + json.dumps(approver_keys) +
                 f"\nrequired_for_import = {'true' if required else 'false'}\n")
        p.write_text(text)
        self.reload()

    def sign(self, path: Path, fpr: str) -> None:
        subprocess.run(["gpg", "--batch", "--yes", "--local-user", fpr, "--armor", "--detach-sign", str(path)],
                       check=True, capture_output=True, env=self.env)

    def approve(self, fpr=None, channel="stable") -> Path:
        stmt = ap.draft_artifact(self.cfg, PKG, "1.0.0", "iphoneos-arm64", channel, fpr or self.approver)
        path = ap.write_draft(self.cfg, stmt)
        self.sign(path, fpr or self.approver)
        return path

    def test_artifact_approval_binds_every_field(self):
        path = self.approve()
        stmt = json.loads(path.read_text())
        self.assertEqual((stmt["package"], stmt["version"], stmt["sha256"], stmt["channel"], stmt["access"]),
                         (PKG, "1.0.0", self.sha, "stable", "public"))
        self.assertEqual(stmt["source"], {"repository": "F1r3srp3nt/Example", "commit": COMMIT})
        self.assertTrue(stmt["approved_at"].endswith("Z"))
        r = ap.check_artifact(self.cfg, PKG, "1.0.0", "iphoneos-arm64", "stable", self.sha, COMMIT, "F1r3srp3nt/Example")
        self.assertTrue(r.ok, r.render())
        # Each bound fact is checked: another checksum, channel or commit is not covered.
        self.assertIn("approval-mismatch", ap.check_artifact(self.cfg, PKG, "1.0.0", "iphoneos-arm64", "stable",
                                                             "0" * 64).codes())
        self.assertIn("approval-missing", ap.check_artifact(self.cfg, PKG, "1.0.0", "iphoneos-arm64", "candidate",
                                                            self.sha).codes())
        self.assertIn("approval-mismatch", ap.check_artifact(self.cfg, PKG, "1.0.0", "iphoneos-arm64", "stable",
                                                             self.sha, "d" * 40).codes())

    def test_tampering_and_formatting_are_refused(self):
        path = self.approve()
        stmt = json.loads(path.read_text())
        path.write_bytes(ap.canonical(dict(stmt, sha256="0" * 64)))
        self.assertIn("approval-bad-signature", ap.verify_statement(self.cfg, path)[1].codes())
        pretty = json.dumps(stmt, indent=2).encode()
        path.write_bytes(pretty)
        self.sign(path, self.approver)
        self.assertIn("approval-not-canonical", ap.verify_statement(self.cfg, path)[1].codes())

    def test_only_the_founder_approval_key_counts(self):
        path = self.approve(fpr=self.other)
        self.assertIn("approval-bad-signature", ap.verify_statement(self.cfg, path)[1].codes())

    def test_the_repository_signing_key_is_never_an_approver(self):
        self.set_config(approver_keys=["keys/approvers/founder.asc", "keys/hcg-repo-archive-key.asc"],
                        fingerprint=self.signer)
        path = self.approve(fpr=self.signer)
        codes = ap.verify_statement(self.cfg, path)[1].codes()
        self.assertIn("approval-key-is-signing-key", codes)

    def test_approvals_are_never_overwritten(self):
        self.approve()
        with self.assertRaises(ap.ApprovalError):
            ap.write_draft(self.cfg, ap.draft_artifact(self.cfg, PKG, "1.0.0", "iphoneos-arm64", "stable", self.approver))

    def test_publication_approval_is_separate_and_bound_to_a_commit(self):
        art = self.approve()
        self.assertIn("approval-missing", ap.check_publication(self.cfg, "e" * 40, "production").codes())
        stmt = ap.draft_publication(self.cfg, "e" * 40, "production", self.approver)
        path = ap.write_draft(self.cfg, stmt)
        self.sign(path, self.approver)
        self.assertTrue(ap.check_publication(self.cfg, "e" * 40, "production").ok)
        self.assertIn("approval-missing", ap.check_publication(self.cfg, "f" * 40, "production").codes())
        self.assertIn("approval-missing", ap.check_publication(self.cfg, "e" * 40, "pages").codes())
        self.assertNotEqual(art.parent.name, path.parent.name)       # artifact and publication approvals are separate

    def test_enforcement_is_opt_in(self):
        r = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, r), r)
        self.assertNotIn("approval-missing", r.codes())                # off by default
        self.set_config(approver_keys=["keys/approvers/founder.asc"], required=True)
        r = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, r), r)
        self.assertIn("approval-missing", r.codes())                   # on: the unapproved entry fails validation
        self.approve()
        r = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, r), r)
        self.assertTrue(r.ok, r.render())


if __name__ == "__main__":
    unittest.main()

"""Repository metadata signing with a throw-away key: sign, verify with gpgv, detect tampering."""

import os
import shutil
import subprocess
import tempfile
import unittest

from support import RepoTestCase, tool_deb
from hcgrepo.importer import import_deb
from hcgrepo.server import BackgroundServer
from hcgrepo.site import BuildError, build
from hcgrepo.sign import verify_signatures
from hcgrepo.verify import verify

PAGES = "https://f1r3srp3nt.github.io/HCG-repo/"


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "gpg/gpgv not installed")
class SigningTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.gnupg = tempfile.mkdtemp(prefix="gnupg-", dir=self.tmp)
        os.chmod(self.gnupg, 0o700)
        self._old = os.environ.get("GNUPGHOME")
        os.environ["GNUPGHOME"] = self.gnupg
        subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-gen-key", "HCG Repo Test <test@example.invalid>",
                        "rsa2048", "sign", "never"], check=True, capture_output=True)
        out = subprocess.run(["gpg", "--batch", "--with-colons", "--list-keys"], check=True, capture_output=True, text=True).stdout
        self.fpr = next(l.split(":")[9] for l in out.splitlines() if l.startswith("fpr:"))
        (self.root / "keys").mkdir()
        subprocess.run(["gpg", "--batch", "--armor", "--output", str(self.root / "keys" / "hcg-repo-archive-key.asc"),
                        "--export", self.fpr], check=True)
        cfg = (self.root / "config" / "repo.toml").read_text()
        (self.root / "config" / "repo.toml").write_text(cfg.replace('fingerprint = ""', f'fingerprint = "{self.fpr}"'))
        self.reload()
        import_deb(self.cfg, tool_deb(self.work), {"source": "test"})

    def tearDown(self):
        if self._old is None:
            os.environ.pop("GNUPGHOME", None)
        else:
            os.environ["GNUPGHOME"] = self._old
        subprocess.run(["gpgconf", "--kill", "all"], capture_output=True, env={**os.environ, "GNUPGHOME": self.gnupg})
        super().tearDown()

    def test_sign_and_verify(self):
        res = build(self.cfg, PAGES, self.tmp / "site", sign_key=self.fpr)
        self.assertTrue(res.signed and res.signature_verified, res.signature_detail)
        repo = res.out_dir / "repo"
        for n in ("Release.gpg", "InRelease", "hcg-repo-archive-key.asc"):
            self.assertTrue((repo / n).exists(), n)
        pub = self.root / "keys" / "hcg-repo-archive-key.asc"
        with BackgroundServer(res.out_dir, "/HCG-repo/") as srv:
            v = verify(PAGES, srv.url, cfg=self.cfg, pubkey=pub, require_signature=True)
        self.assertTrue(v.report.ok and v.signature_verified, v.report.render())

        rel = repo / "Release"
        rel.write_text(rel.read_text().replace("Origin: HCG Repo", "Origin: Tampered"))
        ok, detail = verify_signatures(repo, pub)
        self.assertFalse(ok, detail)

    def test_wrong_key_is_refused(self):
        subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-gen-key", "Other <o@example.invalid>",
                        "rsa2048", "sign", "never"], check=True, capture_output=True)
        out = subprocess.run(["gpg", "--batch", "--with-colons", "--list-keys", "Other"], check=True, capture_output=True, text=True).stdout
        other = next(l.split(":")[9] for l in out.splitlines() if l.startswith("fpr:"))
        with self.assertRaises(BuildError):
            build(self.cfg, PAGES, self.tmp / "site", sign_key=other)

    def test_required_signature_without_key(self):
        with self.assertRaises(BuildError):
            build(self.cfg, PAGES, self.tmp / "site", require_signature=True)

    def test_unsigned_build_reports_unsigned(self):
        res = build(self.cfg, PAGES, self.tmp / "site")
        self.assertFalse(res.signed)
        self.assertFalse((res.out_dir / "repo" / "Release.gpg").exists())
        self.assertIn("Not signed yet", (res.out_dir / "index.html").read_text())


if __name__ == "__main__":
    unittest.main()

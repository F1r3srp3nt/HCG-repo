"""Repository signing seen by real clients, with throw-away keys only (docs/SIGNING.md, Migration).

APT (`apt-get update`, isolated state, no system keys) is run against locally served builds:
  - unsigned: APT refuses by default (Sileo and Zebra accept unsigned sources, see docs/SIGNING.md);
  - signed with the trusted key: accepted, for Stable and Candidate;
  - signed, key not trusted: refused (NO_PUBKEY);
  - tampered Packages or Release after signing: refused;
  - key rotation: a client holding only the old key refuses the new signature; the transition keyring (both keys)
    accepts it.
gpgv (the check Sileo's `apt-key verify` performs on Release + Release.gpg) is covered by verify_signatures.
The migration keeps every package byte, checksum and URL: unsigned and signed builds index identical Packages.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import RepoTestCase, tool_deb
from hcgrepo import guard
from hcgrepo.config import Report
from hcgrepo.importer import import_deb, set_channel
from hcgrepo.server import BackgroundServer
from hcgrepo.sign import check_key, key_status, verify_signatures
from hcgrepo.site import build

PAGES = "https://f1r3srp3nt.github.io/HCG-repo/"
HAVE = all(shutil.which(x) for x in ("gpg", "gpgv", "apt-get"))


def gen_key(home: str, name: str, expire: str = "never") -> str:
    env = {**os.environ, "GNUPGHOME": home}
    subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-gen-key", f"{name} <{name.lower()}@example.invalid>",
                    "rsa2048", "sign", expire], check=True, capture_output=True, env=env)
    out = subprocess.run(["gpg", "--batch", "--with-colons", "--list-keys", name], check=True, capture_output=True,
                         text=True, env=env).stdout
    return next(l.split(":")[9] for l in out.splitlines() if l.startswith("fpr:"))


def export(home: str, fpr: str, dest: Path, armor: bool = True) -> Path:
    args = ["gpg", "--batch", "--yes", "--output", str(dest)] + (["--armor"] if armor else []) + ["--export", fpr]
    subprocess.run(args, check=True, capture_output=True, env={**os.environ, "GNUPGHOME": home})
    return dest


@unittest.skipUnless(HAVE, "gpg, gpgv and apt-get are needed")
class _ClientBase(RepoTestCase):
    """Fixtures only: two throw-away keys, a Stable and a Candidate package, helpers to build and to run APT."""

    def setUp(self):
        super().setUp()
        self.home = tempfile.mkdtemp(prefix="gk", dir="/tmp")   # short path: gpg-agent socket length limit
        os.chmod(self.home, 0o700)
        self._old = os.environ.get("GNUPGHOME")
        os.environ["GNUPGHOME"] = self.home
        self.fa = gen_key(self.home, "HCG Repo Test A")
        self.fb = gen_key(self.home, "HCG Repo Test B")
        (self.root / "keys").mkdir()
        self.pub_a = export(self.home, self.fa, self.tmp / "a.asc")
        import_deb(self.cfg, tool_deb(self.work), {"source": "test"})
        (self.work / "c").mkdir()
        cdeb = tool_deb(self.work / "c", version="2.0.0~beta1")
        self.write_meta("com.hakmicapitalgroup.testtool", '[display]\nsummary = "x"\nknown_issues = "- n"\n'
                        '[release]\nchannel = "beta"\n')
        self.reload()
        import_deb(self.cfg, cdeb, {"source": "test"})
        set_channel(self.cfg, "com.hakmicapitalgroup.testtool", "2.0.0~beta1", "iphoneos-arm64", "candidate")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("GNUPGHOME", None)
        else:
            os.environ["GNUPGHOME"] = self._old
        subprocess.run(["gpgconf", "--kill", "all"], capture_output=True, env={**os.environ, "GNUPGHOME": self.home})
        shutil.rmtree(self.home, ignore_errors=True)
        super().tearDown()

    def configure(self, fpr: str, pub: Path):
        p = self.root / "config" / "repo.toml"
        import re
        p.write_text(re.sub(r'fingerprint = "[0-9A-F]*"', f'fingerprint = "{fpr}"', p.read_text()))
        shutil.copyfile(pub, self.root / "keys" / "hcg-repo-archive-key.asc")
        return self.reload()

    def site(self, name: str, key: str | None):
        return build(self.cfg, PAGES, self.tmp / name, sign_key=key).out_dir

    def apt(self, site: Path, trusted: list[Path], sub: str = "repo/", trustedparts: Path | None = None) -> tuple[int, str]:
        d = Path(tempfile.mkdtemp(prefix="apt-", dir=self.tmp))
        for x in ("state/lists/partial", "cache/archives/partial", "etc/sources.list.d", "etc/preferences.d",
                  "etc/apt.conf.d", "etc/trusted.gpg.d"):
            (d / x).mkdir(parents=True)
        for i, k in enumerate(trusted):
            subprocess.run(["gpg", "--batch", "--yes", "--output", str(d / "etc/trusted.gpg.d" / f"k{i}.gpg"),
                            "--dearmor", str(k)], check=True, capture_output=True)
        parts = trustedparts or (d / "etc/trusted.gpg.d")
        with BackgroundServer(site, "/HCG-repo/") as srv:
            (d / "etc/sources.list").write_text(f"deb {srv.url}{sub} ./\n")
            p = subprocess.run(["apt-get", "-o", f"Dir::Etc={d}/etc", "-o", f"Dir::State={d}/state",
                                "-o", f"Dir::Cache={d}/cache", "-o", f"Dir::Etc::trusted={d}/etc/trusted.gpg",
                                "-o", f"Dir::Etc::trustedparts={parts}",
                                "-o", "APT::Architecture=iphoneos-arm64", "-o", "APT::Architectures::=iphoneos-arm64",
                                "-o", "Debug::NoLocking=1", "-o", "APT::Sandbox::User=" + os.environ.get("USER", "root"),
                                "-o", "Acquire::http::Proxy=false", "-o", "Acquire::Retries=0", "update"],
                               capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr

    def assertAccepted(self, rc_out):
        rc, out = rc_out
        self.assertEqual(rc, 0, out)
        self.assertNotRegex(out, r"^(E|W): ", out)

    def assertRefused(self, rc_out, why: str):
        rc, out = rc_out
        self.assertIn(why, out)
        self.assertRegex(out, r"(?m)^(E|W): ")

class SigningClientTests(_ClientBase):
    def test_unsigned_today(self):
        site = self.site("unsigned", None)
        self.assertRefused(self.apt(site, [self.pub_a]), "is not signed")

    def test_signed_and_trusted_both_channels(self):
        self.configure(self.fa, self.pub_a)
        site = self.site("signed", self.fa)
        self.assertAccepted(self.apt(site, [self.pub_a]))
        self.assertAccepted(self.apt(site, [self.pub_a], sub="repo/candidate/"))
        for sub in ("repo", "repo/candidate"):
            ok, detail = verify_signatures(site / sub, self.pub_a, self.fa)
            self.assertTrue(ok, detail)

    def test_signed_but_key_not_trusted(self):
        self.configure(self.fa, self.pub_a)
        site = self.site("signed", self.fa)
        self.assertRefused(self.apt(site, []), "NO_PUBKEY")

    def test_tampered_metadata_is_refused(self):
        self.configure(self.fa, self.pub_a)
        site = self.site("signed", self.fa)
        pk = site / "repo" / "Packages"
        text = pk.read_text()
        tampered = text.replace("Maintainer: Hakmi", "Maintainer: Hakmx", 1)   # same size: only the hash can tell
        self.assertNotEqual(text, tampered)
        pk.write_text(tampered)
        for f in ("Packages.gz", "Packages.xz", "Packages.bz2", "Packages.zst"):
            (site / "repo" / f).unlink(missing_ok=True)
        self.assertRefused(self.apt(site, [self.pub_a]), "Hash Sum mismatch")
        site2 = self.site("signed2", self.fa)
        rel = site2 / "repo" / "InRelease"
        rel.write_text(rel.read_text().replace("Origin: ", "Origin: X", 1))
        (site2 / "repo" / "Release.gpg").unlink()
        self.assertRefused(self.apt(site2, [self.pub_a]), "BADSIG")

    def test_signature_by_another_key_is_rejected_by_hcg_checks(self):
        self.configure(self.fa, self.pub_a)
        both = self.tmp / "both.asc"
        both.write_text(self.pub_a.read_text() + export(self.home, self.fb, self.tmp / "b.asc").read_text())
        site = self.site("signed", self.fa)
        ok, detail = verify_signatures(site / "repo", both, self.fb)
        self.assertFalse(ok)
        self.assertIn("not the expected key", detail)

    def test_rotation_needs_the_transition_keyring(self):
        pub_b = export(self.home, self.fb, self.tmp / "b.asc")
        self.configure(self.fb, pub_b)
        site = self.site("rotated", self.fb)
        self.assertRefused(self.apt(site, [self.pub_a]), "NO_PUBKEY")         # clients with only the old key
        self.assertAccepted(self.apt(site, [self.pub_a, pub_b]))               # transition keyring: both keys

    def test_migration_changes_no_package(self):
        unsigned = self.site("unsigned", None)
        self.configure(self.fa, self.pub_a)
        signed = self.site("signed", self.fa)
        for sub in ("repo", "repo/candidate"):
            self.assertEqual((unsigned / sub / "Packages").read_bytes(), (signed / sub / "Packages").read_bytes())
            for deb in (unsigned / sub / "debs").glob("*.deb"):
                self.assertEqual(deb.read_bytes(), (signed / sub / "debs" / deb.name).read_bytes())
            for name in ("Release.gpg", "InRelease"):
                self.assertFalse((unsigned / sub / name).exists())
                self.assertTrue((signed / sub / name).exists())
        r = guard.check_site(self.cfg, signed)
        self.assertTrue(r.ok, r.render())

    def test_guard_blocks_unexpected_changes_and_downgrades(self):
        self.configure(self.fa, self.pub_a)
        live = self.site("live", self.fa)
        new = self.site("new", self.fa)
        with BackgroundServer(live, "/HCG-repo/") as srv:
            self.assertTrue(guard.compare_live(self.cfg, new, PAGES, srv.url).ok)
            # a published version silently getting other bytes
            pk = new / "repo" / "Packages"
            orig = pk.read_text()
            import re
            pk.write_text(re.sub(r"SHA256: [0-9a-f]{64}", "SHA256: " + "0" * 64, orig, count=1))
            self.assertIn("unexpected-change", guard.compare_live(self.cfg, new, PAGES, srv.url).codes())
            self.assertIn("checksum-mismatch", guard.check_site(self.cfg, new).codes())
            pk.write_text(orig)
            # dropping the signature once the live source is signed
            for sub in ("repo", "repo/candidate"):
                (new / sub / "Release.gpg").unlink()
            self.assertIn("signing-downgrade", guard.compare_live(self.cfg, new, PAGES, srv.url).codes())
        # a different signing key without acknowledging the rotation
        pub_b = export(self.home, self.fb, self.tmp / "b.asc")
        self.configure(self.fb, pub_b)
        rotated = self.site("rotated", self.fb)
        with BackgroundServer(live, "/HCG-repo/") as srv:
            self.assertIn("signing-key-change", guard.compare_live(self.cfg, rotated, PAGES, srv.url).codes())
            self.assertTrue(guard.compare_live(self.cfg, rotated, PAGES, srv.url, allow_key_change=True).ok)

    def test_deployed_bytes_are_compared(self):
        self.configure(self.fa, self.pub_a)
        site = self.site("signed", self.fa)
        man = guard.manifest(self.cfg, site)
        self.assertIn("repo/candidate/InRelease", man["files"])
        with BackgroundServer(site, "/HCG-repo/") as srv:
            self.assertTrue(guard.check_deployed(self.cfg, man, PAGES, srv.url).ok)
            (site / "repo" / "Release.gpg").write_bytes(b"x")
            self.assertIn("deployed-mismatch", guard.check_deployed(self.cfg, man, PAGES, srv.url).codes())

    def test_key_expiry_is_monitored(self):
        fc = gen_key(self.home, "HCG Repo Test C", expire="10d")
        pub_c = export(self.home, fc, self.tmp / "c.asc")
        st = key_status(pub_c)
        self.assertLessEqual(st["days_until_signing_expiry"], 10)
        self.assertIn("key-expiring", {c for lvl, c, m in check_key(pub_c, fc) if lvl == "error"})
        fd = gen_key(self.home, "HCG Repo Test D", expire="45d")
        pub_d = export(self.home, fd, self.tmp / "d.asc")
        self.assertEqual([(l, c) for l, c, _ in check_key(pub_d, fd)], [("warning", "key-expiring")])
        self.assertIn("key-fingerprint", {c for _, c, _ in check_key(pub_d, fc)})


def resign(repo: Path, fpr: str, date, valid_until=None) -> None:
    """Rewrite Release's Date/Valid-Until and sign it again (Release.gpg + InRelease) with a test key."""
    import re
    from email.utils import format_datetime
    rel = repo / "Release"
    txt = re.sub(r"^Date: .*$", "Date: " + format_datetime(date, usegmt=True), rel.read_text(), flags=re.M)
    txt = re.sub(r"^Valid-Until: .*\n", "", txt, flags=re.M)
    if valid_until:
        txt = txt.replace("\nArchitectures:", "\nValid-Until: " + format_datetime(valid_until, usegmt=True)
                          + "\nArchitectures:", 1)
    rel.write_text(txt)
    for a in (["--armor", "--detach-sign", "--output", str(repo / "Release.gpg")],
              ["--clearsign", "--output", str(repo / "InRelease")]):
        subprocess.run(["gpg", "--batch", "--yes", "--local-user", fpr, "--digest-algo", "SHA512"] + a + [str(rel)],
                       check=True, capture_output=True)


@unittest.skipUnless(HAVE, "gpg, gpgv and apt-get are needed")
class ReplayProtectionTests(_ClientBase):
    """Valid-Until and freshness with real APT (docs/SIGNING.md §6). Observed with apt 2.8: an expired Valid-Until and
    a Date in the future are refused; an older Release that is still within its validity is accepted (APT does not
    detect a replay inside the window), so Valid-Until only bounds the replay window."""

    def setUp(self):
        super().setUp()
        from datetime import datetime, timezone
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.configure(self.fa, self.pub_a)

    def signed(self, name, date, vu=None):
        site = self.site(name, self.fa)
        resign(site / "repo", self.fa, date, vu)
        return site

    def test_build_writes_valid_until_only_when_signed(self):
        from datetime import timedelta
        from hcgrepo.site import BuildError
        p = self.root / "config" / "repo.toml"
        p.write_text(p.read_text().replace("[signing]\n", "[signing]\nvalid_days = 7\n", 1))
        self.reload()
        site = self.site("vu", self.fa)
        rel = (site / "repo" / "Release").read_text()
        self.assertIn("Valid-Until: ", rel)
        self.assertIn("Valid-Until: ", (site / "repo" / "candidate" / "Release").read_text())
        self.assertAccepted(self.apt(site, [self.pub_a]))
        from email.utils import parsedate_to_datetime
        import re
        date = parsedate_to_datetime(re.search(r"^Date: (.*)$", rel, re.M).group(1))
        self.assertLessEqual(date, self.now + timedelta(seconds=5) - timedelta(minutes=50))   # backdated for slow clocks
        # Without a configured fingerprint and without a key, valid_days alone must not produce unsigned output.
        import re as _re
        p.write_text(_re.sub(r'fingerprint = "[0-9A-F]*"', 'fingerprint = ""', p.read_text()))
        self.reload()
        with self.assertRaisesRegex(BuildError, "valid_days needs a signed build"):
            build(self.cfg, PAGES, self.tmp / "unsigned-vu")

    def test_expired_metadata_is_refused(self):
        from datetime import timedelta
        site = self.signed("expired", self.now - timedelta(days=10), self.now - timedelta(days=1))
        self.assertRefused(self.apt(site, [self.pub_a]), "is expired")

    def test_clock_differences(self):
        from datetime import timedelta
        self.assertAccepted(self.apt(self.signed("skew-10s", self.now + timedelta(seconds=10)), [self.pub_a]))
        self.assertRefused(self.apt(self.signed("skew-5min", self.now + timedelta(minutes=5)), [self.pub_a]),
                           "not valid yet")
        self.assertAccepted(self.apt(self.signed("backdated", self.now - timedelta(hours=1),
                                                 self.now + timedelta(days=7)), [self.pub_a]))

    def test_replay_within_the_window_is_not_detected_by_apt(self):
        from datetime import timedelta
        newer = self.signed("newer", self.now - timedelta(hours=1), self.now + timedelta(days=7))
        older = self.signed("older", self.now - timedelta(days=3), self.now + timedelta(days=4))
        d = None
        results = []
        for site in (newer, older):
            rc, out = self.apt_state(site, d)
            d = d or self._last_state
            results.append(rc)
        self.assertEqual(results, [0, 0])      # documented limitation: Valid-Until bounds, but does not stop, replay

    def apt_state(self, site, state_dir):
        """apt-get update reusing one client state across calls (to model a device that saw a newer Release)."""
        d = state_dir or Path(tempfile.mkdtemp(prefix="apt-", dir=self.tmp))
        if state_dir is None:
            for x in ("state/lists/partial", "cache/archives/partial", "etc/sources.list.d", "etc/preferences.d",
                      "etc/apt.conf.d", "etc/trusted.gpg.d"):
                (d / x).mkdir(parents=True)
            subprocess.run(["gpg", "--batch", "--yes", "--output", str(d / "etc/trusted.gpg.d/k.gpg"), "--dearmor",
                            str(self.pub_a)], check=True, capture_output=True)
        self._last_state = d
        with BackgroundServer(site, "/HCG-repo/") as srv:
            (d / "etc/sources.list").write_text(f"deb {srv.url}repo/ ./\n")
            p = subprocess.run(["apt-get", "-o", f"Dir::Etc={d}/etc", "-o", f"Dir::State={d}/state", "-o",
                                f"Dir::Cache={d}/cache", "-o", f"Dir::Etc::trusted={d}/etc/trusted.gpg", "-o",
                                f"Dir::Etc::trustedparts={d}/etc/trusted.gpg.d", "-o", "APT::Architecture=iphoneos-arm64",
                                "-o", "APT::Architectures::=iphoneos-arm64", "-o", "Debug::NoLocking=1",
                                "-o", "APT::Sandbox::User=" + os.environ.get("USER", "root"),
                                "-o", "Acquire::http::Proxy=false", "update"], capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr


@unittest.skipUnless(HAVE and shutil.which("dpkg"), "gpg, gpgv, apt-get and dpkg are needed")
class KeyringBootstrapTests(_ClientBase):
    """HCG Repo Keyring (test keys): installed with real dpkg into an isolated root, APT then trusts exactly what the
    package installed. Covers install, verification, a key update (rotation transition), removal and refusal."""

    def dpkg(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["dpkg", "--root=/", f"--instdir={root}/fs", f"--admindir={root}/admin",
                               f"--log={root}/dpkg.log", "--force-architecture", "--force-not-root",
                               "--force-unsafe-io", *args], capture_output=True, text=True)

    def device(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="dev-", dir=self.tmp))
        for d in ("fs", "admin/info", "admin/updates"):
            (root / d).mkdir(parents=True)
        (root / "admin/status").write_text("")
        return root

    def test_keyring_lifecycle_with_apt(self):
        from hcgrepo.keyring import build_keyring_deb
        from hcgrepo.debinspect import inspect_deb
        from hcgrepo.deepcheck import deep_validate
        self.configure(self.fa, self.pub_a)
        site = self.site("signed", self.fa)
        out = self.tmp / "kr"
        deb1 = build_keyring_deb(self.cfg, "rootless", "1.0", out, test_key=self.pub_a)
        info = inspect_deb(deb1)
        self.assertEqual(info.scripts, {})                                     # no maintainer scripts
        self.assertEqual(info.conffiles, [])                                   # updates replace the key, removal deletes it
        self.assertEqual(info.version, "1.0~test")
        rep, ev = deep_validate(info)
        self.assertTrue(rep.ok and ev["lifecycle"]["passed"], rep.render())
        dev = self.device()
        parts = dev / "fs/var/jb/etc/apt/trusted.gpg.d"
        self.assertRefused(self.apt(site, [], trustedparts=parts if parts.exists() else self.tmp / "none"), "NO_PUBKEY")
        r = self.dpkg(dev, "-i", str(deb1))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertAccepted(self.apt(site, [], trustedparts=parts))            # bootstrap: the keyring alone suffices
        fp = (dev / "fs/var/jb/usr/share/hcg-repo-keyring/FINGERPRINTS").read_text()
        self.assertIn(self.fa, fp)                                             # inspectable on the device
        # Rotation: a transition keyring (A + B) keeps verifying while the repository switches to B.
        pub_b = export(self.home, self.fb, self.tmp / "b.asc")
        deb2 = build_keyring_deb(self.cfg, "rootless", "2.0", out, test_key=self.pub_a, extra_keys=[pub_b])
        self.assertEqual(self.dpkg(dev, "-i", str(deb2)).returncode, 0)
        self.configure(self.fb, pub_b)
        site_b = self.site("signed-b", self.fb)
        self.assertAccepted(self.apt(site_b, [], trustedparts=parts))
        self.assertIn(self.fb, (dev / "fs/var/jb/usr/share/hcg-repo-keyring/FINGERPRINTS").read_text())
        # Removal deletes the key: verification is no longer possible.
        self.assertEqual(self.dpkg(dev, "-r", "com.hakmicapitalgroup.repo-keyring").returncode, 0)
        self.assertFalse((parts / "hcg-repo.gpg").exists())
        self.assertRefused(self.apt(site_b, [], trustedparts=parts), "NO_PUBKEY")

    def test_test_keyring_is_never_importable(self):
        from hcgrepo.keyring import build_keyring_deb
        from hcgrepo.importer import import_deb as imp
        deb = build_keyring_deb(self.cfg, "rootful", "1.0", self.tmp / "kr", test_key=self.pub_a)
        status, rep = imp(self.cfg, deb, {"source": "test"})
        self.assertEqual(status, "rejected")
        self.assertIn("test-keyring", rep.codes())

    def test_expired_or_revoked_keys_are_not_packaged(self):
        from hcgrepo.keyring import build_keyring_deb
        from hcgrepo.sign import SignError
        env = {**os.environ, "GNUPGHOME": self.home}
        subprocess.run(["gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase", "",
                        "--quick-set-expire", self.fb, "seconds=1"], check=True, capture_output=True, env=env)
        import time
        time.sleep(2)
        pub_b = export(self.home, self.fb, self.tmp / "b-expired.asc")
        with self.assertRaises(SignError):
            build_keyring_deb(self.cfg, "rootless", "1.0", self.tmp / "kr", test_key=pub_b)


@unittest.skipUnless(HAVE, "gpg, gpgv and apt-get are needed")
class TrustCenterTests(_ClientBase):
    """hcg.repo.artifact-verification: one downloaded file against the live index, never overstating trust."""

    def deb(self):
        return next((self.root / "pool").rglob("*1.0.0_iphoneos-arm64.deb"))

    def test_signed_index_verifies_the_file(self):
        from hcgrepo.trust import trust_document, verify_artifact
        self.configure(self.fa, self.pub_a)
        site = self.site("signed", self.fa)
        with BackgroundServer(site, "/HCG-repo/") as srv:
            res = verify_artifact(self.cfg, self.deb(), PAGES, srv.url, "stable")
            self.assertEqual((res["verdict"], res["metadata_authenticated"]), ("verified", True), res["steps"])
            other = verify_artifact(self.cfg, self.deb(), PAGES, srv.url, "candidate")
            self.assertEqual(other["verdict"], "failed")                  # not in that channel's index
            pk = site / "repo" / "Packages"
            pk.write_text(pk.read_text().replace("Maintainer: Hakmi", "Maintainer: Hakmx", 1))
            self.assertEqual(verify_artifact(self.cfg, self.deb(), PAGES, srv.url, "stable")["verdict"], "failed")
        self.assertIn("does not show", res["scope_note"])
        doc = trust_document(self.cfg, PAGES)
        self.assertEqual((doc["signing"]["status"], doc["signing"]["trusted_fingerprints"]), ("signed", [self.fa]))

    def test_unsigned_index_is_never_called_authenticated(self):
        from hcgrepo.trust import trust_document, verify_artifact
        site = self.site("unsigned", None)
        with BackgroundServer(site, "/HCG-repo/") as srv:
            res = verify_artifact(self.cfg, self.deb(), PAGES, srv.url, "stable")
        self.assertEqual((res["verdict"], res["metadata_authenticated"]), ("matches-unauthenticated-index", False))
        doc = trust_document(self.cfg, PAGES)
        self.assertEqual((doc["signing"]["status"], doc["signing"]["metadata_authenticated"]), ("unsigned", False))
        self.assertEqual(doc["signing"]["trusted_fingerprints"], [])

    def test_modified_file_fails(self):
        from hcgrepo.trust import verify_artifact
        self.configure(self.fa, self.pub_a)
        site = self.site("signed", self.fa)
        (self.work / "x").mkdir()
        bad = tool_deb(self.work / "x", version="1.0.0", Description="other bytes\n same identity")
        with BackgroundServer(site, "/HCG-repo/") as srv:
            self.assertEqual(verify_artifact(self.cfg, bad, PAGES, srv.url, "stable")["verdict"], "failed")


@unittest.skipUnless(HAVE, "gpg, gpgv and apt-get are needed")
class FounderProcedureTests(RepoTestCase):
    """tools/signing/founder-keygen.sh with a throw-away key, then the CI path exactly as Publish runs it."""

    def test_procedure_and_ci_subkey(self):
        from support import REPO
        out = Path(tempfile.mkdtemp(prefix="fk", dir="/tmp")) / "keys"
        try:
            p = subprocess.run([str(REPO / "tools/signing/founder-keygen.sh"), str(out), "--test",
                                "--uid", "HCG Repo Test Key <t@example.invalid>", "--subkey-expire", "1y"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("Self-test passed", p.stdout)
            self.assertNotIn("PRIVATE KEY", p.stdout + p.stderr)          # nothing secret printed
            fpr = (out / "public/FINGERPRINT").read_text().strip()
            st = key_status(out / "public/hcg-repo-archive-key.asc")
            self.assertEqual(st["primary_fpr"], fpr)
            self.assertEqual(len(st["usable_signing_keys"]), 1)
            self.assertNotEqual(st["usable_signing_keys"][0], fpr)        # signing is done by the subkey only
            self.assertLessEqual(st["days_until_signing_expiry"], 366)
            self.assertTrue((out / "offline/revocation-certificate.rev").is_file())
            for f in out.rglob("*"):
                if f.is_file():
                    self.assertEqual(f.stat().st_mode & 0o077, 0, f)        # owner-only files
            # CI: import only the subkey secret into a fresh keyring, as the Publish workflow does.
            ci = tempfile.mkdtemp(prefix="ci", dir="/tmp")
            os.chmod(ci, 0o700)
            env = {**os.environ, "GNUPGHOME": ci}
            subprocess.run(["gpg", "--batch", "--quiet", "--import", str(out / "ci/hcg-repo-signing-subkey.asc")],
                           check=True, capture_output=True, env=env)
            k = subprocess.run(["gpg", "--batch", "--with-colons", "-K"], capture_output=True, text=True, env=env).stdout
            self.assertRegex(k, r"(?m)^sec:.*")
            self.assertNotRegex(k, r"(?m)^sec:[^\n]*:[^#:\n]*:[^\n]*:\+:")    # no usable primary secret
            self.assertIn("sec#", subprocess.run(["gpg", "--batch", "-K"], capture_output=True, text=True, env=env).stdout)
            (self.root / "keys").mkdir()
            shutil.copyfile(out / "public/hcg-repo-archive-key.asc", self.root / "keys/hcg-repo-archive-key.asc")
            cfgp = self.root / "config" / "repo.toml"
            cfgp.write_text(cfgp.read_text().replace('fingerprint = ""', f'fingerprint = "{fpr}"'))
            self.reload()
            import_deb(self.cfg, tool_deb(self.work), {"source": "test"})
            old = os.environ.get("GNUPGHOME")
            os.environ["GNUPGHOME"] = ci
            try:
                res = build(self.cfg, PAGES, self.tmp / "site", sign_key=fpr)
            finally:
                if old is None:
                    os.environ.pop("GNUPGHOME", None)
                else:
                    os.environ["GNUPGHOME"] = old
                subprocess.run(["gpgconf", "--kill", "all"], capture_output=True, env=env)
                shutil.rmtree(ci, ignore_errors=True)
            self.assertTrue(res.signature_verified, res.signature_detail)
            ok, detail = verify_signatures(res.out_dir / "repo", self.root / "keys/hcg-repo-archive-key.asc", fpr)
            self.assertTrue(ok, detail)
        finally:
            subprocess.run(["gpgconf", "--kill", "all"], capture_output=True,
                           env={**os.environ, "GNUPGHOME": str(out / "offline/keyring")})
            shutil.rmtree(out.parent, ignore_errors=True)

    def test_refuses_to_write_into_a_git_checkout(self):
        from support import REPO
        p = subprocess.run([str(REPO / "tools/signing/founder-keygen.sh"), str(REPO / "never-here"), "--test"],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((REPO / "never-here").exists())


if __name__ == "__main__":
    unittest.main()

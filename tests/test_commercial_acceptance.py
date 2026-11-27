"""HCG Repo commercial distribution: acceptance tests 1-20 in an isolated TEST environment (docs/COMMERCIAL_TEST.md).

HCG Store's real server code runs in test mode (HTTPS, HCG Connect protocol mock, test entitlement ledger, test
payment provider). HCG Repo builds a real site whose paid packages are Store-held. Sileo and Zebra are exercised as
protocol fixtures that send exactly the requests their source code sends. Nothing here contacts production, takes a
real payment or publishes anything. Physical-device results are separate (docs/DEVICE_QUALIFICATION_COMMERCIAL.md).

Skipped unless a local HCG Store checkout with node_modules exists (HCG_STORE_CHECKOUT).
"""

import hashlib
import json
import re
import unittest
import urllib.parse

from commercial_env import (AVAILABLE, FREE, PRO, PRODUCTS, SECOND, TRIAL, UDID, CommercialEnv, Http, PaymentClient,
                            SileoInstall)
from support import NS, REPO, RepoTestCase, set_payment_provider, tool_deb
from hcgrepo import cli
from hcgrepo.config import load_config
from hcgrepo.debinspect import inspect_deb
from hcgrepo.importer import ImportError_, import_deb, set_channel
from hcgrepo.server import BackgroundServer
from hcgrepo.site import build

PROD = "https://repo.hakmicapitalgroup.com/"
DEMO = NS + "testdemo"


def store_record(info, slug, n):
    rid, aid = f"rel-{n:016x}", f"art-{n:016x}"
    art = {"id": aid, "kind": "jailbreak-package", "platform": "ios", "format": "deb",
           "architectures": [info.architecture], "sizeBytes": info.size, "sha256": info.sha256, "status": "verified",
           "validation": {"sha256Matches": True, "problems": [],
                          "content": {"debian": {"package": info.package, "version": info.version,
                                                 "architecture": info.architecture}}},
           "package": {"id": info.package, "version": info.version, "architecture": info.architecture}}
    return {"id": rid, "productId": slug, "productSource": "catalogue", "editionId": "jailbreak", "version": info.version,
            "build": "1", "channel": "stable", "distribution": "paid", "destinations": ["hcg-repo"], "status": "approved",
            "approvedEligibility": "public", "approvedAt": 3, "artifacts": [art]}, aid


@unittest.skipUnless(AVAILABLE, "needs a local HCG Store checkout with node_modules (HCG_STORE_CHECKOUT) and openssl")
class CommercialAcceptance(RepoTestCase):
    """One TEST environment for the whole class (starting the Store takes a few seconds)."""

    @classmethod
    def setUpClass(cls):
        cls.case = cls("run")
        RepoTestCase.setUp(cls.case)
        c = cls.case
        cls.env = CommercialEnv(c.tmp, c.root, c.work)
        cls.repo_srv = None
        try:
            cls._build_repo()
        except Exception:
            cls.env.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        if cls.repo_srv:
            cls.repo_srv.__exit__(None, None, None)
        RepoTestCase.tearDown(cls.case)

    def run(self, result=None):  # the fixture instance created above is never run as a test
        return super().run(result) if self._testMethodName != "run" else None

    # Every test uses the ONE shared TEST environment built in setUpClass (no per-test repository root).
    def setUp(self):
        self.root, self.tmp, self.work = self.case.root, self.case.tmp, self.case.work

    def tearDown(self):
        pass

    # ------------------------------------------------------------------ HCG Repo TEST site
    @classmethod
    def _build_repo(cls):
        c, env = cls.case, cls.env
        # Start the Store first (its port is the payment endpoint); HCG Repo origin for CORS is known after serving.
        env.start_store()
        prods = ""
        for n, (slug, (key, pkg, central, price)) in enumerate(PRODUCTS.items(), 1):
            prods += f'''
[[products]]
key = "{key}"
name = "{key} (TEST)"
package_ids = ["{pkg}"]
layouts = ["iphoneos-arm64"]
kind = "Jailbreak tweak"
access = "paid"
status = "Available"
summary = "TEST commercial product."
store_slug = "{slug}"
store_edition = "jailbreak"

[products.commercial]
release_channel = "commercial-release"
publisher = "Hakmi Capital Group"
category = "Test"
price = "{price / 100:.2f}"
currency = "EUR"
price_note = "TEST"
availability = "Available"
description = ["TEST product."]
store_page = "live"
listing = "approved"
store_readiness = "ready"
commercial_release = "approved"
approved_version = "1.0.0"
purchases = "enabled"
downloads = "enabled"
installation = "enabled"
''' + "".join(f'\n[[products.commercial.approvals]]\nsetting = "{k}"\nvalue = "{v}"\napproved_by = "TEST owner"\n'
               f'date = "2026-11-27"\nrecord = "TEST approval"\n'
               for k, v in (("listing", "approved"), ("store_readiness", "ready"), ("commercial_release", "approved"),
                            ("approved_version", "1.0.0"), ("purchases", "enabled"), ("downloads", "enabled"),
                            ("installation", "enabled")))
            c.write_meta(pkg, '[display]\nsummary = "TEST"\n[access]\nlevel = "paid"\n')
        with open(c.root / "config" / "products.toml", "a") as f:
            f.write(prods)
        p = c.root / "config" / "repo.toml"
        set_payment_provider(c.root, env.endpoint)
        text = p.read_text()
        assert 'base_url = "https://store.hakmicapitalgroup.com/"' in text
        text = text.replace('base_url = "https://store.hakmicapitalgroup.com/"', f'base_url = "{env.origin}/"')
        p.write_text(text)                                                 # every Store link points at the TEST Store
        c.write_meta(FREE, '[display]\nsummary = "TEST free"\n[access]\nlevel = "public"\nexception = "TEST free"\n'
                           'exception_versions = ["1.0.0"]\n')
        c.write_meta(DEMO, '[display]\nsummary = "TEST demo"\nknown_issues = "- n"\n[release]\nchannel = "beta"\n'
                           '[access]\nlevel = "public"\nexception = "TEST demo"\nexception_versions = ["0.1.0~beta1", "0.1.0~beta2"]\n')
        cfg = c.reload()
        # Paid builds: recorded as protected (never stored), linked to the TEST Store's approved artifact.
        for n, (slug, (key, pkg, central, price)) in enumerate(PRODUCTS.items(), 1):
            deb = env.debs[(pkg, "1.0.0")]
            info = inspect_deb(deb)
            status, rep = import_deb(cfg, deb, {"source": "source-build"})
            assert status == "protected", rep.render()
            rec, aid = store_record(info, slug, n)
            rp = c.tmp / f"rel-{n}.json"
            rp.write_text(json.dumps(rec))
            a = cli.build_parser().parse_args(["import-store-release", "--release", str(rp), "--artifact", aid,
                                               "--deb", str(deb)])
            assert a.fn(a, c.reload()) == 0
        # Free packages: a public stable package, and a candidate demo with a rollback version.
        free = tool_deb(c.work, package=FREE, version="1.0.0")
        assert import_deb(c.reload(), free, {"source": "t"})[0] == "imported"
        cls.demo_shas = []
        for v in ("0.1.0~beta1", "0.1.0~beta2"):
            d = c.work / v
            d.mkdir()
            deb = tool_deb(d, package=DEMO, version=v)
            assert import_deb(c.reload(), deb, {"source": "t"}, channel="candidate")[0] == "imported"
            cls.demo_shas.append(inspect_deb(deb).sha256)
        cls.cfg = c.reload()
        cls.site = build(cls.cfg, PROD, c.tmp / "site").out_dir
        cls.repo_base = env.serve_repo_https(cls.site, env.repo_port)     # HTTPS, as Zebra requires
        cls.repo_url = cls.repo_base + "repo/"
        cls.repo_srv = BackgroundServer(cls.site, "/")                     # plain HTTP copy for verify-artifact
        cls.http_repo_base = cls.repo_srv.__enter__().url
        cls.free_sha = inspect_deb(free).sha256

    # ------------------------------------------------------------------ helpers
    def packages(self, sub=""):
        st, _, raw = self.env.http.request("GET", self.repo_url + sub + "Packages")
        self.assertEqual(st, 200)
        out = {}
        for stanza in raw.decode().split("\n\n"):
            f = dict(l.split(": ", 1) for l in stanza.splitlines() if ": " in l and not l.startswith(" "))
            if f.get("Package"):
                out[(f["Package"], f["Version"])] = f
        return out

    def client(self, flavour="sileo") -> PaymentClient:
        c = PaymentClient(flavour, self.repo_url, self.env.http)
        c.discover()
        return c

    def signed_in(self, sub, flavour="sileo") -> PaymentClient:
        self.env.control("next-user", {"sub": sub, "email": f"{sub}@example.test", "name": sub})
        c = self.client(flavour)
        self.assertTrue(c.authenticate(), "sign-in through the HCG Connect protocol did not complete")
        return c

    def fetch(self, url, headers=None):
        return self.env.http.request("GET", url, headers=headers)

    _bought: dict = {}

    def buyer(self, sub, slug="hcg-test-pro"):
        """A customer who completed a TEST checkout (test payment provider, signed webhook) for `slug`."""
        if (sub, slug) not in self._bought:
            b = self.env.web_sign_in(sub, f"{sub}@example.test")
            self._bought[(sub, slug)] = self.env.web_buy(b, f"{slug}-test")
        return self._bought[(sub, slug)]

    # ------------------------------------------------------------------ 1-20
    def test_01_anonymous_users_discover_public_packages(self):
        stable, cand = self.packages(), self.packages("candidate/")
        self.assertEqual(stable[(FREE, "1.0.0")]["SHA256"], self.free_sha)
        self.assertIn((DEMO, "0.1.0~beta2"), cand)
        paid = stable[(PRO, "1.0.0")]
        self.assertIn("cydia::commercial", paid.get("Tag", ""))           # discoverable, marked commercial
        self.assertTrue(paid["Filename"].startswith("protected/"))
        st, _, raw = self.env.http.request("GET", self.repo_url + "payment_endpoint")
        self.assertEqual((st, raw.decode()), (200, self.env.endpoint))       # exact: no whitespace (Irisin)

    def test_02_anonymous_users_cannot_download_paid_packages(self):
        paid = self.packages()[(PRO, "1.0.0")]
        st, _, _ = self.env.http.request("GET", self.repo_url + paid["Filename"])
        self.assertEqual(st, 404)                                          # never deployed
        c = self.client("sileo")
        st, b = c.authorize_download(PRO, "1.0.0")
        self.assertEqual(st, 401)
        self.assertTrue(b.get("invalidate"))
        z = self.client("zebra")                                           # Zebra sends token "none"
        st, b = z.authorize_download(PRO, "1.0.0")
        self.assertEqual(st, 401)

    def test_03_customer_authenticates_through_hcg_connect_protocol(self):
        c = self.signed_in("cust-03")
        self.assertTrue(len(c.token) >= 20 and len(c.payment_secret) >= 20)
        st, b = c.user_info()
        self.assertEqual((st, b["user"]["name"]), (200, "cust-03"))
        self.assertNotIn(UDID, json.dumps(b))                              # the device ID is not an identity
        log = self.env.log_path.read_text()
        self.assertNotIn(c.token, log)

    def test_04_signed_in_without_entitlement_cannot_download(self):
        c = self.signed_in("cust-04")
        st, info = c.package_info(PRO)
        self.assertEqual((st, info["purchased"], info["price"]), (200, 0, "€4.99"))
        st, b = c.purchase(PRO)                                            # never grants; points to the Store
        self.assertEqual(b["status"], 1)
        self.assertTrue(b["url"].startswith(self.env.origin + "/apps/hcg-test-pro"))
        st, b = c.authorize_download(PRO, "1.0.0")
        self.assertEqual(st, 403)
        st, b = c.package_info(PRO)
        self.assertEqual(b["purchased"], 0)                                # a purchase request granted nothing

    def test_05_sandbox_purchase_creates_test_entitlement(self):
        c = self.signed_in("cust-05")
        self.assertEqual(c.package_info(PRO)[1]["purchased"], 0)
        self.buyer("cust-05")
        st, info = c.package_info(PRO)
        self.assertEqual((info["purchased"], info["price"]), (1, "Owned"))
        self.assertIn(PRO, c.user_info()[1]["items"])

    def test_06_entitled_customer_downloads_the_exact_approved_package(self):
        self.buyer("cust-06")
        c = self.signed_in("cust-06")
        st, b = c.authorize_download(PRO, "1.0.0")
        self.assertEqual(st, 200, b)
        self.assertTrue(b["url"].startswith("https://"))
        st, h, data = self.fetch(b["url"])
        self.assertEqual(st, 200)
        sha = hashlib.sha256(data).hexdigest()
        self.assertEqual(sha, self.packages()[(PRO, "1.0.0")]["SHA256"])   # what HCG Repo's index promised
        self.assertEqual(sha, self.env.sha(self.env.debs[(PRO, "1.0.0")]))

    def test_07_same_entitlement_works_across_package_manager_adapters(self):
        self.buyer("cust-07")
        for flavour in ("sileo", "zebra", "irisin"):
            c = self.signed_in("cust-07", flavour)
            self.assertEqual(c.package_info(PRO)[1]["purchased"], 1, flavour)
            st, b = c.authorize_download(PRO, "1.0.0")
            self.assertEqual(st, 200, (flavour, b))
            st, _, data = self.fetch(b["url"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), self.packages()[(PRO, "1.0.0")]["SHA256"], flavour)

    def test_08_expired_authorization_cannot_download(self):
        self.buyer("cust-08")
        c = self.signed_in("cust-08")
        url = c.authorize_download(PRO, "1.0.0")[1]["url"]
        self.env.control("clock", {"advanceSeconds": 61})                 # TTL is 60 s in TEST
        try:
            st, _, body = self.fetch(url)
            self.assertEqual(st, 403)
            self.assertIn(b"link_expired", body)
        finally:
            self.env.control("clock", {"advanceSeconds": -61})

    def test_09_authorization_for_one_package_cannot_download_another(self):
        self.buyer("cust-09")                                              # owns testpro only
        c = self.signed_in("cust-09")
        st, _ = c.authorize_download(SECOND, "1.0.0")
        self.assertEqual(st, 403)
        url = c.authorize_download(PRO, "1.0.0")[1]["url"]
        _, _, data = self.fetch(url)
        self.assertEqual(hashlib.sha256(data).hexdigest(), self.env.sha(self.env.debs[(PRO, "1.0.0")]))
        token = url.rsplit("/", 1)[-1]
        forged = url.rsplit("/", 1)[0] + "/" + token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
        self.assertEqual(self.fetch(forged)[0], 403)                       # signature binds the exact artifact

    def test_10_refunded_and_revoked_entitlements_stop_new_downloads(self):
        chk = self.buyer("cust-10")
        c = self.signed_in("cust-10")
        url = c.authorize_download(PRO, "1.0.0")[1]["url"]
        st, b = self.env.refund(chk, 499)
        self.assertEqual(st, 200, b)
        self.assertEqual(c.authorize_download(PRO, "1.0.0")[0], 403)      # refund applies to the next request
        self.assertEqual(self.fetch(url)[0], 403)                          # and to an already issued link
        self.buyer("cust-10b")
        c2 = self.signed_in("cust-10b")
        self.assertEqual(c2.authorize_download(PRO, "1.0.0")[0], 200)
        self.env.control("revoke-all", {"sub": "cust-10b"})               # an HCG administrator revokes access
        self.assertEqual(c2.authorize_download(PRO, "1.0.0")[0], 403)

    def test_11_temporary_free_testing_respects_expiration(self):
        # Temporary access as a time-limited grant in the TEST entitlement ledger. (HCG Store's free windows apply to
        # releases published through Ship to Store; they are covered by the Store's own test suite.)
        c = self.signed_in("cust-11")
        self.assertEqual(c.authorize_download(TRIAL, "1.0.0")[0], 403)
        self.env.control("grant", {"sub": "cust-11", "centralProductId": "hcg.test.trial", "edition": "jailbreak",
                                   "seconds": 300, "source": "temporary-free-testing"})
        try:
            self.assertEqual(c.package_info(TRIAL)[1]["purchased"], 1)
            st, b = c.authorize_download(TRIAL, "1.0.0")
            self.assertEqual(st, 200, b)
            url = b["url"]
            self.env.control("clock", {"advanceSeconds": 301})             # the grant has expired
            self.assertEqual(c.authorize_download(TRIAL, "1.0.0")[0], 403)
            self.assertEqual(c.package_info(TRIAL)[1]["purchased"], 0)
        finally:
            self.env.control("clock", {"advanceSeconds": -301})

    def test_12_candidate_and_stable_access_remain_separate(self):
        stable, cand = self.packages(), self.packages("candidate/")
        self.assertNotIn((DEMO, "0.1.0~beta2"), stable)
        for pkg in (PRO, SECOND, TRIAL):
            self.assertNotIn(pkg, {k[0] for k in cand})                   # paid builds never in the public Candidate
        with self.assertRaises(ImportError_):
            set_channel(self.cfg, PRO, "1.0.0", "iphoneos-arm64", "candidate")

    def test_13_superseded_and_disabled_versions_follow_policy(self):
        # HCG Repo indexes only the owner-approved commercial version (approved_version = 1.0.0).
        self.assertEqual([k for k in self.packages() if k[0] == PRO], [(PRO, "1.0.0")])
        self.buyer("cust-13")
        c = self.signed_in("cust-13")
        st, b = c.authorize_download(PRO, "0.8.0")                        # delivery not approved: disabled
        self.assertEqual(st, 404, b)
        # Superseded but still delivery-approved: HCG Store keeps it available to entitled customers for rollback
        # (measured policy), while HCG Repo's index offers only the approved current version.
        st, b = c.authorize_download(PRO, "0.9.0")
        self.assertEqual(st, 200, b)
        _, _, data = self.fetch(b["url"])
        self.assertEqual(hashlib.sha256(data).hexdigest(), self.env.sha(self.env.debs[(PRO, "0.9.0")]))

    def test_14_protected_artifacts_absent_from_public_static_storage(self):
        names = {p.name for p in self.site.rglob("*.deb")}
        shas = {hashlib.sha256(p.read_bytes()).hexdigest() for p in self.site.rglob("*.deb")}
        for pkg in (PRO, SECOND, TRIAL):
            for (p, v), deb in self.env.debs.items():
                if p == pkg:
                    self.assertNotIn(deb.name, names)
                    self.assertNotIn(self.env.sha(deb), shas)
        self.assertFalse(list((self.root / "pool").rglob(f"{PRO}*.deb")))  # not even in the pool
        self.assertNotIn(str(self.site), str(self.env.storage))

    def test_15_existing_free_packages_keep_working(self):
        f = self.packages()[(FREE, "1.0.0")]
        st, _, data = self.env.http.request("GET", self.repo_url + f["Filename"])
        self.assertEqual((st, hashlib.sha256(data).hexdigest()), (200, self.free_sha))
        self.assertNotIn("Tag", f) if "cydia::commercial" not in f.get("Tag", "") else self.fail("free marked paid")

    def test_16_release_passport_and_artifact_verification_still_work(self):
        from hcgrepo.passport import all_passports
        from hcgrepo.trust import verify_artifact
        p = all_passports(self.cfg, FREE, "1.0.0")[0]
        self.assertEqual(p["artifact"]["sha256"], self.free_sha)
        self.assertFalse(p["repository_signing"]["authenticated"])
        deb = next(self.root.glob("pool/**/*testfree_1.0.0*.deb"))
        res = verify_artifact(self.cfg, deb, PROD, self.http_repo_base, "stable")
        self.assertEqual(res["verdict"], "matches-unauthenticated-index")
        pp = all_passports(self.cfg, PRO, "1.0.0")[0]
        self.assertEqual(pp["publication"]["record_status"], "protected")

    def test_17_rollback_versions_remain_intact(self):
        cand = self.packages("candidate/")
        self.assertEqual([cand[(DEMO, v)]["SHA256"] for v in ("0.1.0~beta1", "0.1.0~beta2")], self.demo_shas)

    def test_18_store_or_hcg_outage_grants_nothing(self):
        self.buyer("cust-18")
        c = self.signed_in("cust-18")
        self.env.control("authority", {"down": True})
        try:
            st, b = c.authorize_download(PRO, "1.0.0")
            self.assertEqual(st, 503, b)
            self.assertNotIn("url", b)
        finally:
            self.env.control("authority", {"down": False})
        dead = PaymentClient("sileo", self.repo_url, self.env.http)        # Store unreachable: no URL, no download
        dead.endpoint = "https://127.0.0.1:9/repo-api/v1/sileo/"
        dead.token = c.token
        with self.assertRaises(OSError):
            dead.authorize_download(PRO, "1.0.0")

    def test_19_no_secrets_in_logs_or_package_metadata(self):
        c = self.signed_in("cust-19")
        secrets = [c.token, c.payment_secret, "hcg-repo-test-webhook-secret"]
        log = self.env.log_path.read_text()
        public = "".join(p.read_text(errors="replace") for p in self.site.rglob("*") if p.is_file()
                         and p.suffix in ("", ".html", ".json", ".txt", ".js"))
        for s in secrets:
            self.assertNotIn(s, log)
            self.assertNotIn(s, public)
        self.assertNotRegex(public, r"(?i)authorization: bearer|payment_secret=|token=[A-Za-z0-9_-]{20,}")

    def test_20_no_real_payment_and_no_production_publication(self):
        self.assertEqual(self.cfg.store_base_url, self.env.origin + "/")     # the TEST site never calls production
        st, h = self.env.http.json("GET", self.env.origin + "/api/v1/health")
        self.assertEqual((h["mode"], h["payments"], h["entitlementAuthority"]), ("test", "test-mode", "test-ledger"))
        prod = load_config(REPO)                                           # the real repository configuration
        self.assertEqual((prod.payment_endpoint, prod.signing_fingerprint), ("", ""))   # nothing advertised, no key
        self.assertFalse(prod.payment_provider.advertise)
        self.assertEqual(prod.payment_provider.url, "https://store.hakmicapitalgroup.com/api/sileo/v1/")
        self.assertTrue(self.env.endpoint.startswith("https://127.0.0.1:"))

    # ------------------------------------------------------------------ Profile and range (proposed Store patch)
    def _profile(self, browser=None, origin=None):
        origin = origin or self.repo_base.rstrip("/")
        b = browser or Http(self.env.tmp / "cert.pem")
        st, h, raw = b.request("GET", self.env.origin + "/api/v1/repo/profile", headers={"origin": origin},
                               use_cookies=browser is not None)
        if st == 404:
            self.skipTest("HCG Store checkout lacks the proposed /api/v1/repo/profile endpoint "
                          "(docs/store-integration/0001-repo-profile-and-ranges.patch)")
        return st, h, json.loads(raw)

    def test_21_profile_reads_the_account_and_grants_nothing(self):
        st, h, d = self._profile()                                         # anonymous
        self.assertEqual((st, d["signedIn"]), (200, False))
        self.assertTrue(d["sign_in_url"].startswith(self.env.origin + "/auth/login"))
        self.assertEqual(h.get("access-control-allow-origin"), self.repo_base.rstrip("/"))
        self.assertEqual(h.get("access-control-allow-credentials"), "true")
        _, h, _ = self._profile(origin="https://evil.example")             # unknown origins get no CORS grant
        self.assertNotIn("access-control-allow-origin", h)
        self.buyer("cust-21")
        b = self.env.web_sign_in("cust-21", "cust-21@example.test")
        self.signed_in("cust-21")                                          # also connect a package manager
        st, h, d = self._profile(b)
        self.assertEqual((st, d["signedIn"]), (200, True))
        self.assertEqual(d["user"]["email"], "cust-21@example.test")
        owned = {o["package"] for o in d["library"]}
        self.assertEqual(owned, {PRO})                                     # ownership from the ledger, not sign-in
        self.assertTrue(all(p["testOnly"] for p in d["purchases"]))
        self.assertTrue(d["connectedPackageManagers"])
        raw = json.dumps(d)
        self.assertNotIn(UDID, raw)                                        # device identifiers never surface
        self.assertNotRegex(raw, r'"(token|payment_secret|url)"')          # no credentials and no download links
        # Signed in without any entitlement: an empty library, still no download.
        b2 = self.env.web_sign_in("cust-21b", "cust-21b@example.test")
        self.assertEqual(self._profile(b2)[2]["library"], [])

    def test_22_protected_downloads_support_byte_ranges_after_authorization(self):
        self.buyer("cust-22")
        c = self.signed_in("cust-22")
        url = c.authorize_download(PRO, "1.0.0")[1]["url"]
        full = self.env.debs[(PRO, "1.0.0")].read_bytes()
        st, h, data = self.fetch(url, headers={"range": "bytes=10-99"})
        if st == 200 and "accept-ranges" not in h:
            self.skipTest("HCG Store checkout lacks the proposed range support")
        self.assertEqual((st, data), (206, full[10:100]))
        self.assertEqual(h["content-range"], f"bytes 10-99/{len(full)}")
        st, h, data = self.fetch(url, headers={"range": "bytes=0-"})       # a resumed download from the start
        self.assertEqual((st, hashlib.sha256(data).hexdigest()), (206, self.env.sha(self.env.debs[(PRO, "1.0.0")])))
        self.assertEqual(self.fetch(url, headers={"range": f"bytes={len(full) + 5}-"})[0], 416)
        # A range never bypasses authorization.
        st, _ = c.authorize_download(SECOND, "1.0.0")
        self.assertEqual(st, 403)
        token = url.rsplit("/", 1)[-1]
        forged = url.rsplit("/", 1)[0] + "/" + token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
        self.assertEqual(self.fetch(forged, headers={"range": "bytes=0-9"})[0], 403)


    # ------------------------------------------------------------------ HCG Store as the official provider (STORE_PROVIDER.md)
    def sileo(self, name, client=None):
        d = self.tmp / f"device-{name}"
        return SileoInstall(self.env.http, self.repo_url, client, d)

    def test_23_sileo_free_installation_is_unchanged(self):
        dev = self.sileo("free", self.client("sileo"))                   # source has a provider; free needs no account
        r = dev.install(FREE, "1.0.0")
        self.assertEqual((r["exit"], r["status"]), (0, "install ok installed"), r)
        self.assertEqual(r["sha256"], self.free_sha)
        self.assertTrue(any((dev.root / "root" / f.lstrip("./")).exists() for f in r["files"]))
        self.assertFalse(any("authorize_download" in s for s in dev.steps))

    def test_24_authenticated_commercial_package_discovery(self):
        st, _, raw = self.fetch(self.repo_url + "payment_endpoint")
        self.assertEqual(raw.decode(), self.env.endpoint)                 # plain text, no newline
        if self.env.provider_alias:
            self.assertTrue(self.env.endpoint.endswith("/api/sileo/v1/"))
        f = self.sileo("disc").stanza(PRO)
        self.assertIn("cydia::commercial", f["Tag"])
        self.assertTrue(f["Filename"].startswith("protected/"))
        for k in ("SHA256", "Architecture", "Version", "Depends"):
            self.assertIn(k, f)
        anon = self.client("sileo")
        self.assertEqual(anon.info()[1]["name"], "HCG Store")
        c = self.signed_in("cust-24")
        st, info = c.package_info(PRO)
        self.assertEqual((st, info["purchased"], info["available"]), (200, 0, True))
        self.assertIn("4.99", info["price"])                              # price from HCG Store's TEST offer
        st, ui = c.user_info()
        self.assertEqual(st, 200)
        self.assertNotIn(PRO, ui["items"])                                # signed in is not ownership

    def test_25_test_purchase_and_authorized_installation_through_sileo(self):
        c = self.signed_in("cust-25")
        with self.assertRaises(PermissionError):
            self.sileo("before", c).install(PRO, "1.0.0")
        st, p = c.purchase(PRO)
        self.assertEqual((st, p["status"]), (200, 1))                    # opens HCG Store checkout; grants nothing
        self.assertTrue(p["url"].startswith(self.env.origin + "/apps/hcg-test-pro?edition=jailbreak&buy=1"), p["url"])
        self.assertEqual(c.package_info(PRO)[1]["purchased"], 0)
        browser = self.env.web_sign_in("cust-25", "cust-25@example.test")     # the checkout page, same HCG account
        self.env.web_buy(browser, "hcg-test-pro-test")                   # TEST provider, signed webhook → entitlement
        self.assertEqual(c.package_info(PRO)[1]["purchased"], 1)
        dev = self.sileo("after", c)
        r = dev.install(PRO, "1.0.0")
        self.assertEqual((r["exit"], r["status"]), (0, "install ok installed"), r)
        self.assertEqual(r["sha256"], self.env.sha(self.env.debs[(PRO, "1.0.0")]))
        self.assertTrue((dev.root / "root" / "var/jb/usr/bin/testpro").exists())
        self.assertTrue(any("authorize_download 1.0.0 iphoneos-arm64 -> 200" in s for s in dev.steps), dev.steps)

    def test_26_unauthorized_paid_downloads_are_blocked(self):
        f = self.sileo("x").stanza(PRO)
        self.assertEqual(self.fetch(self.repo_url + f["Filename"])[0], 404)             # never on the static site
        self.assertEqual(self.fetch(self.http_repo_base + "repo/" + f["Filename"])[0], 404)
        with self.assertRaises(PermissionError):                         # not signed in
            self.sileo("anon", self.client("sileo")).install(PRO, "1.0.0")
        with self.assertRaises(PermissionError):                         # signed in, no entitlement
            self.sileo("other", self.signed_in("cust-26")).install(PRO, "1.0.0")
        self.buyer("cust-26b")
        owner = self.signed_in("cust-26b")
        with self.assertRaises(PermissionError):                         # owns PRO only
            self.sileo("wrong", owner).install(SECOND, "1.0.0")
        owner.sign_out()
        owner.token = "forged-" + "x" * 40
        with self.assertRaises(PermissionError):                         # forged or signed-out token
            self.sileo("forged", owner).install(PRO, "1.0.0")

    def test_27_zebra_compatibility_evaluation_protocol_only(self):
        """Evaluation, not qualification: Zebra's requests from source (docs/PACKAGE_MANAGERS.md); no device."""
        f = self.sileo("z").stanza(PRO)
        self.assertIsNone(urllib.parse.urlsplit(f["Filename"]).netloc or None)   # relative: ZBDownloadManager authorizes
        z = self.client("zebra")
        st, b = z.authorize_download(PRO, "1.0.0")                       # signed out: Zebra sends token "none"
        self.assertNotEqual(st, 200)
        self.assertFalse(b.get("success", True))
        self.buyer("cust-27")
        z = self.signed_in("cust-27", "zebra")
        r = self.sileo("zebra", z).install(PRO, "1.0.0")
        self.assertEqual((r["exit"], r["status"]), (0, "install ok installed"), r)

    def test_28_store_integration_status_against_the_test_store(self):
        import os
        import shutil
        from hcgrepo import storeint
        chk = self.tmp / "store-checkout"
        (chk / "catalogue").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.env.tmp / "store-catalogue" / "products.json", chk / "catalogue" / "products.json")
        os.environ["HCG_STORE_CA_FILE"] = str(self.env.tmp / "cert.pem")
        try:
            d = storeint.status(self.cfg, live=True, store_checkout=chk)
            feed, _ = storeint.fetch_feed(self.cfg)
        finally:
            os.environ.pop("HCG_STORE_CA_FILE", None)
        pv, h = d["provider"], d["store_health"]
        self.assertTrue(pv["advertise"] and pv["api"]["ok"], pv)
        self.assertEqual(pv["api"]["name"], "HCG Store")
        self.assertEqual((h["mode"], h["entitlement_authority"], h["entitlements_available"]), ("test", "test-ledger", True))
        pro = next(a for a in d["associations"] if a["key"] == "test-pro")
        self.assertEqual([(b["version"], b["indexed"]) for b in pro["builds"]], [("1.0.0", True)])
        self.assertTrue(pro["builds"][0]["store_release"])
        self.assertGreaterEqual(d["counts"]["paid_packages"], 3)
        tested = set(PRODUCTS) | {k for k, *_ in PRODUCTS.values()}
        self.assertFalse([u for u in d["unmapped"] if (u.get("store_product") in tested or u.get("key") in tested)], d["unmapped"])
        self.assertIn("fixture-quasar-shell", {u.get("store_product") for u in d["unmapped"]})   # Store side, unmapped here
        plan = {r["product"]: r for r in d["publication_plan"] if r["product"].startswith("test-")}
        self.assertEqual(plan["test-pro"]["state"], "recorded", plan)
        self.assertFalse([m for m in d["invalid_metadata"] if m["level"] == "error"], d["invalid_metadata"])
        r, edit = storeint.rollback_check(self.cfg, "test-pro", "0.9.0", feed)
        self.assertIn("rollback-not-recorded", r.codes())                # only recorded, Store-linked builds
        r, edit = storeint.rollback_check(self.cfg, "test-pro", "1.0.0", feed)
        self.assertTrue(r.ok, r.render())
        self.assertIn('approved_version = "1.0.0"', edit)

if __name__ == "__main__":
    unittest.main()

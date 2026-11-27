"""[payment_provider]: HCG Store as HCG Repo's Sileo payment provider (docs/STORE_PROVIDER.md).

The provider URL is always validated; <repo>/payment_endpoint exists only when advertise = true with an owner
approval record, and only in the stable source."""

import unittest

from support import NS, REPO, RepoTestCase, set_payment_provider, tool_deb
from hcgrepo.config import ConfigError, load_config
from hcgrepo.importer import import_deb
from hcgrepo.site import build

PROD = "https://repo.hakmicapitalgroup.com/"
URL = "https://store.hakmicapitalgroup.com/api/sileo/v1/"


class PaymentProviderConfig(RepoTestCase):
    def test_production_configuration_is_configured_but_not_advertised(self):
        prod = load_config(REPO)
        self.assertEqual(prod.payment_provider.url, URL)
        self.assertEqual(prod.payment_provider.trusted_hosts, ("store.hakmicapitalgroup.com",))
        self.assertFalse(prod.payment_provider.advertise)
        self.assertEqual(prod.payment_endpoint, "")
        site = build(self.cfg, PROD, self.tmp / "site").out_dir
        self.assertFalse((site / "repo" / "payment_endpoint").exists())
        self.assertFalse((site / "repo" / "candidate" / "payment_endpoint").exists())

    def test_advertised_provider_is_plain_text_without_newline_in_stable_only(self):
        set_payment_provider(self.root, URL)
        cfg = self.reload()
        self.assertEqual(cfg.payment_endpoint, URL)
        import_deb(cfg, tool_deb(self.work), {"source": "t"})
        site = build(cfg, PROD, self.tmp / "site").out_dir
        self.assertEqual((site / "repo" / "payment_endpoint").read_bytes(), URL.encode())
        self.assertFalse((site / "repo" / "candidate" / "payment_endpoint").exists())
        self.assertEqual(cfg.candidate().payment_endpoint, "")

    def test_trailing_slash_is_normalized(self):
        set_payment_provider(self.root, URL.rstrip("/"))
        self.assertEqual(self.reload().payment_endpoint, URL)

    def refused(self, url, **kw):
        set_payment_provider(self.root, url, **kw)
        with self.assertRaises(ConfigError):
            self.reload()

    def test_untrusted_or_malformed_destinations_are_refused(self):
        trusted = ["store.hakmicapitalgroup.com"]
        self.refused("https://evil.example/api/sileo/v1/", hosts=trusted)
        self.refused("https://store.hakmicapitalgroup.com.evil.example/api/sileo/v1/", hosts=trusted)
        self.refused("https://store.hakmicapitalgroup.com:8443/api/sileo/v1/", hosts=trusted)
        self.refused("http://store.hakmicapitalgroup.com/api/sileo/v1/", hosts=trusted)
        self.refused("https://user:pw@store.hakmicapitalgroup.com/api/sileo/v1/", hosts=trusted)
        self.refused("https://store.hakmicapitalgroup.com/api/sileo/v1/?token=x", hosts=trusted)
        self.refused("https://store.hakmicapitalgroup.com/api/sileo/v1/#x", hosts=trusted)
        self.refused("https://store.hakmicapitalgroup.com/api/../sileo/", hosts=trusted)
        self.refused("https://store.hakmicapitalgroup.com/api/sileo/v1/ ", hosts=trusted)
        self.refused(URL, hosts=["*.hakmicapitalgroup.com"])
        self.refused(URL, hosts=["https://store.hakmicapitalgroup.com"])

    def test_advertising_needs_the_owner_approval_record(self):
        self.refused(URL, approved=False)
        set_payment_provider(self.root, URL, advertise=False, approved=False)
        self.assertEqual(self.reload().payment_endpoint, "")     # configured, not advertised: no approval needed

    def test_the_old_access_setting_is_refused(self):
        p = self.root / "config" / "repo.toml"
        p.write_text(p.read_text().replace("require_explicit = false", 'require_explicit = false\npayment_endpoint = ""'))
        with self.assertRaises(ConfigError):
            self.reload()


if __name__ == "__main__":
    unittest.main()

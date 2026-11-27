"""Commercial release controls (docs/COMMERCIAL.md).

A product HCG Store will sell has independent, owner-controlled settings: listing, purchases, downloads, installation,
commercial approval, HCG Store readiness and the approved version. They start closed, and every other value needs an
owner approval record. These tests check what that guarantees in the built site and index:
- a prepared listing is not published;
- a published listing shows its price and "Coming Soon" without any purchase or install path;
- a paid build reaches the main index only when every gate is open, and only the approved version;
- a preview build can be reviewed but is never accepted as deployable;
- the free candidate demo stays in the candidate channel and is never the commercial build.
"""

import json
import re

from support import NS, REPO, RepoTestCase, set_payment_provider, tool_deb
from hcgrepo import cli
from hcgrepo.config import COMMERCIAL_DEFAULTS, Report, load_config, load_lock
from hcgrepo.debinspect import inspect_deb
from hcgrepo.importer import import_deb
from hcgrepo.repo import check_commercial, check_repository, load_pool
from hcgrepo.server import BackgroundServer
from hcgrepo.config import ConfigError
from hcgrepo.site import COMMERCIAL_NOT_READY, NO_DOWNLOADS, PREVIEW_MARKER, STORE_PAGE_PENDING, BuildError, build
from hcgrepo.verify import verify

PROD = "https://repo.hakmicapitalgroup.com/"
PKG = NS + "premium"
KEY = "premium-tool"
ENDPOINT = "https://store.example/repo-api/v1/sileo/"

PRODUCT = f'''
[[products]]
key = "{KEY}"
name = "Premium Tool"
package_ids = ["{PKG}"]
layouts = ["iphoneos-arm64"]
kind = "Jailbreak tweak"
access = "paid"
status = "Coming Soon"
summary = "Advanced test customization."
detail = "Introductory prerelease."
store_slug = "{KEY}"

[products.commercial]
release_channel = "commercial-prerelease"
publisher = "Hakmi Capital Group"
category = "iOS Customization / Jailbreak Tweaks"
price = "1.99"
currency = "USD"
price_note = "Introductory Prerelease Price"
availability = "Coming Soon"
description = ["A product used by the test suite."]
'''


def store_record(info, version="1.0.0"):
    """An HCG Store release approved for public delivery to HCG Repo (see test_store_release)."""
    art = {"id": "art-0123456789abcdef", "kind": "jailbreak-package", "platform": "ios", "format": "deb",
           "architectures": [info.architecture], "sizeBytes": info.size, "sha256": info.sha256, "status": "verified",
           "validation": {"sha256Matches": True, "problems": [],
                          "content": {"debian": {"package": info.package, "version": info.version,
                                                 "architecture": info.architecture}}},
           "package": {"id": info.package, "version": info.version, "architecture": info.architecture}}
    return {"id": "rel-0123456789abcdef", "productId": KEY, "productSource": "catalogue", "editionId": None,
            "version": version, "build": "1", "channel": "stable", "distribution": "paid", "destinations": ["hcg-repo"],
            "status": "approved", "approvedEligibility": "public", "approvedAt": 3, "artifacts": [art]}


class CommercialTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        with open(self.root / "config" / "products.toml", "a") as f:
            f.write(PRODUCT)
        self.write_meta(PKG, '[display]\nsummary = "x"\n[access]\nlevel = "paid"\n')
        self.reload()

    # ---- helpers

    def set_commercial(self, **settings):
        """Rewrite the fixture product's settings; approve=True adds an owner approval record for each."""
        approve = settings.pop("approve", True)
        store_page = settings.pop("store_page", "live" if settings.get("purchases") == "enabled" else "pending")
        p = self.root / "config" / "products.toml"
        text = p.read_text()
        text = re.sub(r"\n(listing|purchases|downloads|installation|commercial_release|store_readiness|approved_version|store_page) = .*", "", text)
        text = re.sub(r"\n\[\[products\.commercial\.approvals\]\][^\[]*", "\n", text)
        lines = f'\nstore_page = "{store_page}"' + "".join(f'\n{k} = "{v}"' for k, v in settings.items())
        text = text.replace('description = ["A product used by the test suite."]',
                            'description = ["A product used by the test suite."]' + lines)
        if approve:
            for k, v in settings.items():
                text += (f'\n[[products.commercial.approvals]]\nsetting = "{k}"\nvalue = "{v}"\napproved_by = "owner"\n'
                         f'date = "2026-11-21"\nrecord = "test approval"\n')
        p.write_text(text)
        return self.reload()

    def protected_build(self, version="1.0.0", store=True):
        """Import a paid build (recorded as protected; the file is never stored) and its HCG Store approval."""
        out = self.work / version
        out.mkdir(parents=True, exist_ok=True)
        deb = tool_deb(out, package=PKG, version=version)
        info = inspect_deb(deb)
        status, rep = import_deb(self.cfg, deb, {"source": "source-build"})
        self.assertEqual(status, "protected", rep.render())
        if store:
            rp = self.tmp / f"release-{version}.json"
            rp.write_text(json.dumps(store_record(info, version)))
            a = cli.build_parser().parse_args(["import-store-release", "--release", str(rp), "--artifact",
                                               "art-0123456789abcdef", "--deb", str(deb)])
            self.assertEqual(a.fn(a, self.reload()), 0)
        return info

    def endpoint(self):
        set_payment_provider(self.root, ENDPOINT)
        return self.reload()

    def site(self, name, **kw):
        return build(self.cfg, PROD, self.tmp / name, **kw).out_dir

    def codes(self):
        r = Report("t")
        check_repository(self.cfg, load_pool(self.cfg, r), r)
        return r.codes()

    def served(self, site, **kw):
        with BackgroundServer(site, "/") as srv:
            return verify(PROD, srv.url, cfg=self.cfg, **kw).report

    # ---- defaults: prepared, nothing public

    def test_defaults_are_closed(self):
        c = self.cfg.product_for_package(PKG).commercial
        self.assertEqual(c.settings, COMMERCIAL_DEFAULTS)
        self.assertEqual((c.price_text, c.price_note), ("$1.99 USD", "Introductory Prerelease Price"))
        self.assertFalse(c.installable)
        self.assertEqual(self.codes(), set())

    def test_prepared_listing_is_not_published_and_paid_build_not_indexed(self):
        info = self.protected_build()
        self.endpoint()     # even with HCG Store's payment provider configured and a Store-approved release
        site = self.site("s")
        self.assertFalse((site / "products" / KEY).exists())
        home = (site / "index.html").read_text()
        self.assertNotIn(KEY, home)
        self.assertNotIn("Premium Tool", home)
        packages = (site / "repo" / "Packages").read_text()
        self.assertNotIn(PKG, packages)
        self.assertNotIn(info.sha256, packages)
        self.assertFalse(list(site.rglob("*.deb")))
        self.assertFalse((site / PREVIEW_MARKER).exists())
        rep = self.served(site)
        self.assertTrue(rep.ok, rep.render())

    # ---- preview: reviewable, never deployable

    def test_preview_build_is_marked_and_refused_for_deployment(self):
        site = self.site("p", preview_listings=True)
        page = (site / "products" / KEY / "index.html").read_text()
        for text in ("$1.99 USD", "Introductory Prerelease Price", "Coming Soon", "Commercial Prerelease",
                     "Hakmi Capital Group", "iOS Customization / Jailbreak Tweaks", "data-preview",
                     '<meta name="robots" content="noindex, nofollow">'):
            self.assertIn(text, page)
        self.assertTrue((site / PREVIEW_MARKER).exists())
        rep = self.served(site)
        self.assertIn("preview-build", rep.codes())

    # ---- approved listing: visible, priced, no purchase, no install

    def test_approved_listing_shows_price_but_offers_no_purchase_or_install(self):
        self.protected_build()
        self.endpoint()
        self.set_commercial(listing="approved")
        site = self.site("a")
        page = (site / "products" / KEY / "index.html").read_text()
        for text in ("Premium Tool", "Advanced test customization.", "<strong>$1.99 USD</strong> | Introductory Prerelease Price",
                     "Coming Soon to HCG Store", COMMERCIAL_NOT_READY, NO_DOWNLOADS, STORE_PAGE_PENDING,
                     'disabled aria-disabled="true">Purchase unavailable', 'data-purchases="disabled"'):
            self.assertIn(text, page)
        self.assertNotIn(self.cfg.store_base_url, page)      # no Store page exists: no invented Store URL
        for text in ("sileo://package/", "store-access.js", "data-store-access", "Buy Product", ".deb\"",
                     "/candidate/", "sileo://source/", "data-preview", "noindex"):
            self.assertNotIn(text, page)
        home = (site / "index.html").read_text()
        self.assertIn('id="coming-soon"', home)
        self.assertIn(f'href="products/{KEY}/"', home)
        self.assertNotIn(PKG, (site / "repo" / "Packages").read_text())
        self.assertFalse(list(site.rglob("*.deb")))
        rep = self.served(site)
        self.assertTrue(rep.ok, rep.render())

    # ---- approvals and order are enforced

    def test_store_page_link_only_when_the_store_page_exists(self):
        self.set_commercial(listing="approved", store_page="live")
        site = self.site("live")
        page = (site / "products" / KEY / "index.html").read_text()
        self.assertIn(f'href="{self.cfg.store_product_url(self.cfg.product_for_package(PKG))}"', page)
        self.assertNotIn(STORE_PAGE_PENDING, page)
        self.assertNotIn("store-access.js", page)                 # a Store link is still not a purchase path
        self.assertTrue(self.served(site).ok)
        self.set_commercial(listing="approved")                     # back to pending: an old page linking the Store fails
        self.assertIn("commercial-store-link", self.served(site).codes())
        with self.assertRaises(ConfigError):                        # nobody can buy without a Store page
            self.set_commercial(listing="approved", store_readiness="ready", commercial_release="approved",
                                approved_version="1.0.0", purchases="enabled", store_page="pending")

    def test_every_change_needs_an_owner_approval(self):
        self.set_commercial(listing="approved", approve=False)
        self.assertIn("commercial-unapproved", self.codes())
        self.assertFalse(self.cfg.product_for_package(PKG).listed())
        with self.assertRaises(BuildError):
            self.site("u")

    def test_workflow_order_is_enforced(self):
        self.set_commercial(listing="approved", purchases="enabled")          # HCG Store still pending
        self.assertIn("commercial-order", self.codes())
        self.set_commercial(listing="approved", store_readiness="ready", commercial_release="approved",
                            approved_version="1.0.0", purchases="enabled", installation="enabled")   # no downloads
        self.assertIn("commercial-order", self.codes())
        self.set_commercial(listing="approved", store_readiness="ready", commercial_release="approved")   # no version
        self.assertIn("commercial-order", self.codes())

    def test_approved_version_must_be_a_protected_stable_build(self):
        self.set_commercial(listing="approved", store_readiness="ready", commercial_release="approved",
                            approved_version="9.9.9")
        self.assertIn("commercial-version", self.codes())

    def test_a_free_candidate_demo_is_never_the_commercial_build(self):
        self.write_meta(PKG, '[display]\nsummary = "x"\nknown_issues = "- beta"\n[release]\nchannel = "beta"\n[access]\n'
                             'level = "paid"\nexception = "owner approved free demo"\nexception_versions = ["0.1.0~beta1"]\n')
        deb = tool_deb(self.work, package=PKG, version="0.1.0~beta1")
        status, rep = import_deb(self.reload(), deb, {"source": "test"}, channel="candidate")
        self.assertEqual(status, "imported", rep.render())
        self.set_commercial(listing="approved", store_readiness="ready", commercial_release="approved",
                            approved_version="0.1.0~beta1")
        self.assertIn("commercial-version", self.codes())
        # The demo stays in the candidate source only; promoting it to the main source is refused.
        self.set_commercial()
        a = cli.build_parser().parse_args(["channel", PKG, "0.1.0~beta1", "iphoneos-arm64", "stable"])
        self.assertNotEqual(a.fn(a, self.reload()), 0)
        site = self.site("c")
        self.assertIn(PKG, (site / "repo" / "candidate" / "Packages").read_text())
        self.assertNotIn(PKG, (site / "repo" / "Packages").read_text())

    def test_commercial_product_must_be_paid(self):
        self.write_meta(PKG, '[display]\nsummary = "x"\n[access]\nlevel = "connect"\n')
        self.reload()
        self.assertIn("commercial-access", self.codes())

    # ---- full activation: only the approved version, only through HCG Store

    def test_installation_needs_every_gate_and_indexes_only_the_approved_version(self):
        ok = self.protected_build("1.0.0")
        later = self.protected_build("1.1.0")          # Store-approved too, but not the owner-approved version
        gates = dict(listing="approved", store_readiness="ready", commercial_release="approved",
                     approved_version="1.0.0", purchases="enabled", downloads="enabled", installation="enabled")
        self.set_commercial(**gates)
        self.assertIn("commercial-order", self.codes())     # installation without a payment endpoint
        self.endpoint()
        self.assertEqual(self.codes(), set())
        site = self.site("f")
        packages = (site / "repo" / "Packages").read_text()
        self.assertIn(f"SHA256: {ok.sha256}", packages)
        self.assertNotIn(later.sha256, packages)
        self.assertIn("Filename: protected/", packages)
        self.assertIn("cydia::commercial", packages)
        self.assertFalse(list(site.rglob("*.deb")))           # the file is never deployed; HCG Store delivers it
        page = (site / "products" / KEY / "index.html").read_text()
        self.assertIn(f"sileo://package/{PKG}", page)
        self.assertIn("store-access.js", page)
        rep = self.served(site)
        self.assertTrue(rep.ok, rep.render())
        # Closing any one gate takes it out of the index again.
        for k, v in (("installation", "disabled"), ("downloads", "disabled"), ("store_readiness", "pending")):
            closed = dict(gates, **{k: v})
            if k == "downloads":
                closed["installation"] = "disabled"
            if k == "store_readiness":
                closed.update(purchases="disabled", downloads="disabled", installation="disabled")
            self.set_commercial(**closed)
            site = self.site(f"g-{k}")
            self.assertNotIn(PKG, (site / "repo" / "Packages").read_text(), k)

    # ---- verify catches tampering or a mistaken deployment

    def test_verify_rejects_an_exposed_listing_or_indexed_package(self):
        site = self.site("t")
        (site / "products" / KEY).mkdir(parents=True)
        (site / "products" / KEY / "index.html").write_text("<html>Premium Tool $1.99</html>")
        self.assertIn("commercial-listing-exposed", self.served(site).codes())
        info = self.protected_build()
        self.endpoint()
        self.set_commercial(listing="approved", store_readiness="ready", commercial_release="approved",
                            approved_version="1.0.0", purchases="enabled", downloads="enabled", installation="enabled")
        site = self.site("t2")
        self.set_commercial(listing="approved")      # gates closed again, but the old index is still deployed
        self.assertIn("commercial-indexed", self.served(site).codes())
        self.assertIn(info.sha256, (site / "repo" / "Packages").read_text())

    def test_store_access_script_never_upgrades_a_disabled_purchase(self):
        js = (REPO / "tools" / "hcgrepo" / "assets" / "store-access.js").read_text()
        guard = js.index('getAttribute("data-purchases") === "disabled"')
        self.assertLess(guard, js.index("fetch("))   # checked before any request to HCG Store


class AnimationCenterListingTests(RepoTestCase):
    """The real HCG Animation Center entry: prepared, priced, closed, and not in the main index."""

    def test_animation_center_listing_approved_everything_else_closed(self):
        cfg = load_config(REPO)
        p = next(x for x in cfg.products if x.key == "hcg-animation-center")
        c = p.commercial
        self.assertEqual(p.package_ids, ["com.hakmicapitalgroup.animationcenter"])
        self.assertEqual((p.name, p.access, p.summary), ("HCG Animation Center", "paid", "Advanced iOS animation customization."))
        self.assertEqual((c.release_channel, c.price_text, c.price_note, c.availability, c.publisher, c.category),
                         ("commercial-prerelease", "$1.99 USD", "Introductory Prerelease Price", "Coming Soon",
                          "Hakmi Capital Group", "iOS Customization / Jailbreak Tweaks"))
        self.assertEqual((c.source_channel, c.destination), ("candidate", "main"))
        self.assertEqual(c.settings, dict(COMMERCIAL_DEFAULTS, listing="approved"))
        self.assertEqual(c.store_page, "pending")
        self.assertEqual([(a["setting"], a["value"], a["date"]) for a in c.approvals], [("listing", "approved", "2026-11-21")])
        record = c.approvals[0]["record"]
        for text in ("Approved for public product listing", "com.hakmicapitalgroup.animationcenter", "$1.99 USD",
                     "Purchases: Not approved", "Downloads: Not approved", "Installation: Not approved",
                     "Commercial release: Not approved", "HCG Store readiness: Pending", "Approved commercial version: None",
                     "not approval of any executable package"):
            self.assertIn(text, record)
        self.assertTrue(p.listed())
        self.assertFalse(c.installable)
        self.assertEqual(c.approved_version, "")
        r = Report("animation center")
        check_commercial(cfg, load_lock(cfg), r)
        self.assertTrue(r.ok, r.render())

    def test_real_build_shows_coming_soon_listing_without_any_purchase_or_install(self):
        cfg = load_config(REPO)
        site = build(cfg, PROD, self.tmp / "real").out_dir
        page = (site / "products" / "hcg-animation-center" / "index.html").read_text()
        for text in ("<h1>HCG Animation Center</h1>", "Advanced iOS animation customization.",
                     "<strong>$1.99 USD</strong> | Introductory Prerelease Price", "Coming Soon to HCG Store",
                     "Purchasing and installation will become available once HCG Store’s payment and licensing "
                     "infrastructure is ready.", 'disabled aria-disabled="true">Purchase unavailable · Coming Soon',
                     STORE_PAGE_PENDING, NO_DOWNLOADS, "videos and GIFs"):
            self.assertIn(text, page)
        for text in ("sileo://", ".deb", "/candidate/", "store-access.js", "data-store-access", "Buy Product",
                     cfg.store_base_url, "system interface", "data-preview", "noindex"):
            self.assertNotIn(text, page)
        home = (site / "index.html").read_text()
        self.assertIn('data-commercial="hcg-animation-center"', home)
        self.assertIn('href="products/hcg-animation-center/"', home)
        self.assertFalse((site / PREVIEW_MARKER).exists())
        for name in ("Packages",):
            self.assertNotIn("com.hakmicapitalgroup.animationcenter", (site / "repo" / name).read_text())
        self.assertFalse(list((site / "repo" / "debs").glob("*animationcenter*")))
        self.assertFalse((site / "repo" / "payment_endpoint").exists())
        cand = (site / "repo" / "candidate" / "Packages").read_text()
        self.assertIn("Package: com.hakmicapitalgroup.animationcenter\n", cand)
        self.assertIn("SHA256: 27a161b3cc323f09940211553b058873218697b49c995812f04e579f97429f60", cand)


if __name__ == "__main__":
    import unittest
    unittest.main()

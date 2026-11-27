"""HCG Store integration: product pages, store links and access-level labels (docs/STORE_INTEGRATION.md).
Package access itself (public / connect / paid, protected delivery) is tested in test_access.py."""

import os
import re
import unittest
from unittest import mock

from support import NS, RepoTestCase, set_payment_provider, tool_deb
from hcgrepo.config import ConfigError, load_config
from hcgrepo.importer import import_deb
from hcgrepo.site import ASSET_DIR, build
from hcgrepo.verify import verify

PROD = "https://repo.hakmicapitalgroup.com/"
STORE = "https://store.hakmicapitalgroup.com/"
PROV = {"source": "test"}
REAL = {  # key: (store_slug, store_edition, package_ids, product access)
    "hcg-terminal-unleashed": ("hcg-terminal", "unleashed", [NS + "hcgterminal.unleashed"], "connect"),
    "hcg-nexus": ("hcg-nexus", "jailbreak", [NS + "hcgnexus"], "paid"),
    "hcg-sentinel": ("hcg-sentinel", "", ["com.hakmicapitalgroup.hcgsentinel"], ""),
}


class StoreTests(RepoTestCase):
    def set_products(self, toml: str):
        (self.root / "config" / "products.toml").write_text(toml)
        return self.reload()

    def edit_repo_toml(self, old: str, new: str):
        p = self.root / "config" / "repo.toml"
        text = p.read_text()
        self.assertIn(old, text)
        p.write_text(text.replace(old, new))

    def verify_dir(self, site):
        return verify(PROD, site.as_uri() + "/", cfg=self.cfg)

    # --- configuration
    def test_real_products_mapping(self):
        products = {p.key: p for p in self.cfg.products}
        self.assertEqual(self.cfg.store_base_url, STORE)
        for key, (slug, edition, ids, access) in REAL.items():
            with self.subTest(key=key):
                p = products[key]
                self.assertEqual((p.store_slug, p.store_edition, p.package_ids, p.access), (slug, edition, ids, access))
        term = products["hcg-terminal-unleashed"]
        self.assertEqual(self.cfg.store_product_url(term), STORE + "apps/hcg-terminal?edition=unleashed")
        self.assertEqual(self.cfg.store_login_url(term),
                         STORE + "auth/login?return_to=%2Fapps%2Fhcg-terminal%3Fedition%3Dunleashed")
        self.assertEqual(self.cfg.store_product_url(products["hcg-sentinel"]), STORE + "apps/hcg-sentinel")
        self.assertEqual(self.cfg.store_access_url(products["hcg-nexus"]), STORE + "api/v1/repo/products/hcg-nexus/access")

    def test_invalid_products_are_refused(self):
        bad = {
            "access": '[[products]]\nkey = "x"\nname = "X"\nstore_slug = "x"\naccess = "login-required"\n',
            "key": '[[products]]\nname = "X"\nstore_slug = "x"\n',
            "slug": '[[products]]\nkey = "x"\nname = "X"\nstore_slug = "../x"\n',
            "duplicate": '[[products]]\nkey = "x"\nname = "X"\nstore_slug = "x"\n' * 2,
            "shared package": ('[[products]]\nkey = "a"\nname = "A"\nstore_slug = "a"\npackage_ids = ["%sx"]\n'
                               '[[products]]\nkey = "b"\nname = "B"\nstore_slug = "b"\npackage_ids = ["%sx"]\n' % (NS, NS)),
            "layout": '[[products]]\nkey = "x"\nname = "X"\nstore_slug = "x"\nlayouts = ["darwin-arm64"]\n',
        }
        for name, toml in bad.items():
            with self.subTest(name), self.assertRaises(ConfigError):
                self.set_products(toml)

    def test_store_base_url_must_be_https_and_can_be_overridden(self):
        with mock.patch.dict(os.environ, {"HCG_STORE_BASE_URL": "https://store.example.test/shop"}):
            self.assertEqual(load_config(self.root).store_base_url, "https://store.example.test/shop/")
        with mock.patch.dict(os.environ, {"HCG_STORE_BASE_URL": "http://store.example.test/"}), self.assertRaises(ConfigError):
            load_config(self.root)
        self.edit_repo_toml(f'base_url = "{STORE}"', 'base_url = "http://store.hakmicapitalgroup.com/"')
        with self.assertRaises(ConfigError):
            self.reload()

    def test_product_level_follows_package_access(self):
        cfg = self.set_products(f'[[products]]\nkey = "t"\nname = "T"\nstore_slug = "t"\npackage_ids = ["{NS}testtool"]\n')
        self.assertEqual(cfg.product_level(cfg.products[0]), "")
        self.write_meta(NS + "testtool", '[access]\nlevel = "paid"\n')
        self.assertEqual(cfg.product_level(cfg.products[0]), "paid")

    # --- product pages
    def test_product_pages(self):
        site = build(self.cfg, PROD, self.tmp / "site").out_dir
        landing = (site / "index.html").read_text()
        restricted = landing.split('id="sign-in-required"')[1].split("</section>")[0]
        development = landing.split('id="in-development"')[1].split("</section>")[0]
        self.assertFalse((site / "repo" / "products").exists())
        self.assertTrue((site / "assets" / "store-access.js").is_file())
        for key, (slug, edition, ids, access) in REAL.items():
            with self.subTest(key=key):
                html = (site / "products" / key / "index.html").read_text()
                path = f"/apps/{slug}" + (f"?edition={edition}" if edition else "")
                login = STORE + "auth/login?return_to=" + path.replace("/", "%2F").replace("?", "%3F").replace("=", "%3D")
                self.assertIn(f'href="{login}">Sign in to check access</a>', html)
                self.assertIn(f'href="{STORE}{path[1:]}">View in HCG Store</a>', html)
                self.assertIn(f'data-access-url="{STORE}api/v1/repo/products/{key}/access"', html)
                self.assertIn(f'data-access-level="{access}"', html)
                self.assertIn("Downloads are not available yet.", html)
                self.assertIn(f'<link rel="canonical" href="{PROD}products/{key}/">', html)
                self.assertIn('<script src="../../assets/store-access.js" defer></script>', html)
                self.assertNotIn("sileo://package/", html)
                self.assertNotRegex(html, r"\.deb[\"'?#]")
                self.assertIsNone(re.search(r"<script(?![^>]*src=)", html), "inline script")
                for pkg in ids:
                    self.assertIn(f"<code>{pkg}</code>", html)
                section = restricted if access in ("connect", "paid") else development
                self.assertIn(f'href="products/{key}/">Product page</a>', section)
        nexus = (site / "products" / "hcg-nexus" / "index.html").read_text()
        term = (site / "products" / "hcg-terminal-unleashed" / "index.html").read_text()
        self.assertIn(">Buy Product</h2>", nexus)
        self.assertIn("HCG Store ownership", nexus)
        self.assertIn(">Get access</h2>", term)
        sentinel = (site / "products" / "hcg-sentinel" / "index.html").read_text()
        self.assertIn("<code>com.hakmicapitalgroup.hcgsentinel</code>", sentinel)   # its real package, no duplicate
        self.assertNotIn("This product has no Debian package", sentinel)
        v = self.verify_dir(site)
        self.assertTrue(v.report.ok, v.report.render())

    def test_public_product_page_offers_install(self):
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work), PROV)[0], "imported")
        site = build(self.cfg, PROD, self.tmp / "site").out_dir
        html = (site / "products" / "test-fixtures" / "index.html").read_text()
        self.assertIn(f'href="sileo://package/{NS}testtool"', html)
        self.assertIn(f'href="../../repo/depictions/{NS}testtool/"', html)
        self.assertNotIn("Downloads are not available yet.", html)
        self.assertNotRegex(html, r"\.deb[\"'?#]")
        self.assertIn('href="products/test-fixtures/">Product page</a>', (site / "index.html").read_text())
        self.assertTrue(self.verify_dir(site).report.ok)

    def test_protected_product_with_payment_endpoint(self):
        self.write_meta(NS + "testtool", '[access]\nlevel = "connect"\n')
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work), {**PROV, "hcg_store": {"store_release": "rel-0000000000000001"}})[0], "protected")
        set_payment_provider(self.root, "https://connect.example.invalid/sileo")
        self.reload()
        site = build(self.cfg, PROD, self.tmp / "site").out_dir
        html = (site / "products" / "test-fixtures" / "index.html").read_text()
        self.assertIn(f'href="sileo://package/{NS}testtool"', html)   # Sileo asks for HCG Connect sign-in
        self.assertIn("sign in with HCG Connect", html)
        self.assertNotRegex(html, r"\.deb[\"'?#]")
        self.assertFalse([f for f in site.rglob("*.deb")])
        self.assertTrue(self.verify_dir(site).report.ok)

    def test_verify_catches_broken_product_pages(self):
        site = build(self.cfg, PROD, self.tmp / "site").out_dir
        nexus = site / "products" / "hcg-nexus" / "index.html"
        nexus.write_text(nexus.read_text().replace(
            "</article>", f'<a href="../../repo/debs/{NS}hcgnexus_0.2.0_iphoneos-arm64.deb">.deb</a>'
                          f'<a href="sileo://package/{NS}hcgnexus">Install</a></article>'))
        term = site / "products" / "hcg-terminal-unleashed" / "index.html"
        term.write_text(term.read_text().replace("Downloads are not available yet.", "")
                        .replace("</main>", "<script>alert(1)</script></main>")
                        .replace(f'href="{STORE}apps/hcg-terminal?edition=unleashed"', 'href="https://elsewhere.example/"'))
        (site / "products" / "hcg-sentinel" / "index.html").unlink()
        landing = site / "index.html"
        landing.write_text(landing.read_text().replace('href="products/hcg-nexus/"', 'href="#"'))
        report = self.verify_dir(site).report
        for code in ("product-page-install-link", "product-page-store-link", "product-page-download-notice",
                     "product-page-inline-script", "product-page-missing", "site-product-link"):
            self.assertIn(code, report.codes())
        self.assertIn("not in the index", report.render())

    def test_store_access_script_is_csp_friendly(self):
        js = (ASSET_DIR / "store-access.js").read_text()
        for banned in ("eval(", "new Function", "innerHTML", "outerHTML", "document.write", "insertAdjacentHTML",
                       "localStorage", "sessionStorage", "Authorization", ".deb"):
            self.assertNotIn(banned, js)
        self.assertIn('credentials: "include"', js)
        self.assertIn('Accept: "application/json"', js)
        self.assertIn("TIMEOUT_MS = 5000", js)
        self.assertIn('level === "connect"', js)


if __name__ == "__main__":
    unittest.main()

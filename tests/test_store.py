"""HCG Store integration: product pages, store links, the public-artifact gate and payment_endpoint."""

import contextlib
import io
import os
import re
import unittest
from unittest import mock

from support import FIXTURE_PRODUCT, NS, RepoTestCase, tool_deb
from hcgrepo import cli
from hcgrepo.config import ConfigError, Report, load_config, load_lock
from hcgrepo.importer import import_deb
from hcgrepo.repo import check_repository, load_pool
from hcgrepo.site import BuildError, build
from hcgrepo.verify import verify

PAGES = "https://f1r3srp3nt.github.io/HCG-repo/"
STORE = "https://store.hakmicapitalgroup.com/"
PROV = {"source": "test"}
REAL = {
    "hcg-terminal-unleashed": ("hcg-terminal", "unleashed", [NS + "hcgterminal.unleashed"]),
    "hcg-nexus": ("hcg-nexus", "jailbreak", [NS + "hcgnexus"]),
    "hcg-sentinel": ("hcg-sentinel", "", []),
}


class StoreTests(RepoTestCase):
    def set_products(self, toml: str, keep_fixtures: bool = True):
        (self.root / "config" / "products.toml").write_text(toml + (FIXTURE_PRODUCT if keep_fixtures else ""))
        return self.reload()

    def edit_repo_toml(self, old: str, new: str):
        p = self.root / "config" / "repo.toml"
        text = p.read_text()
        self.assertIn(old, text)
        p.write_text(text.replace(old, new))

    def verify_dir(self, site):
        return verify(PAGES, site.as_uri() + "/", cfg=self.cfg)

    # --- configuration
    def test_real_products_mapping(self):
        products = {p.key: p for p in self.cfg.products}
        self.assertEqual(self.cfg.store_base_url, STORE)
        for key, (slug, edition, ids) in REAL.items():
            with self.subTest(key=key):
                p = products[key]
                self.assertEqual((p.store_slug, p.store_edition, p.package_ids), (slug, edition, ids))
                self.assertEqual(p.access, "login-required")
        self.assertEqual(self.cfg.store_product_url(products["hcg-terminal-unleashed"]),
                         STORE + "apps/hcg-terminal?edition=unleashed")
        self.assertEqual(self.cfg.store_login_url(products["hcg-terminal-unleashed"]),
                         STORE + "auth/login?return_to=%2Fapps%2Fhcg-terminal%3Fedition%3Dunleashed")
        self.assertEqual(self.cfg.store_product_url(products["hcg-sentinel"]), STORE + "apps/hcg-sentinel")
        self.assertEqual(self.cfg.store_access_url(products["hcg-nexus"]), STORE + "api/v1/repo/products/hcg-nexus/access")
        self.assertEqual(self.cfg.payment_endpoint, "")

    def test_missing_access_is_login_required(self):
        cfg = self.set_products('[[products]]\nkey = "x"\nname = "X"\nstore_slug = "x"\npackage_ids = ["%sx"]\n' % NS)
        self.assertEqual(cfg.products[0].access, "login-required")
        status, r = import_deb(cfg, tool_deb(self.work, package=NS + "x"), PROV)
        self.assertEqual(status, "rejected")
        self.assertIn("access-not-public", r.codes())

    def test_invalid_products_are_refused(self):
        bad = {
            "access": '[[products]]\nkey = "x"\nname = "X"\nstore_slug = "x"\naccess = "free"\n',
            "key": '[[products]]\nname = "X"\nstore_slug = "x"\n',
            "slug": '[[products]]\nkey = "x"\nname = "X"\nstore_slug = "../x"\n',
            "duplicate": '[[products]]\nkey = "x"\nname = "X"\nstore_slug = "x"\n' * 2,
            "shared package": ('[[products]]\nkey = "a"\nname = "A"\nstore_slug = "a"\npackage_ids = ["%sx"]\n'
                               '[[products]]\nkey = "b"\nname = "B"\nstore_slug = "b"\npackage_ids = ["%sx"]\n' % (NS, NS)),
            "layout": '[[products]]\nkey = "x"\nname = "X"\nstore_slug = "x"\nlayouts = ["darwin-arm64"]\n',
        }
        for name, toml in bad.items():
            with self.subTest(name), self.assertRaises(ConfigError):
                self.set_products(toml, keep_fixtures=False)

    def test_store_base_url_must_be_https_and_can_be_overridden(self):
        with mock.patch.dict(os.environ, {"HCG_STORE_BASE_URL": "https://store.example.test/shop"}):
            self.assertEqual(load_config(self.root).store_base_url, "https://store.example.test/shop/")
        with mock.patch.dict(os.environ, {"HCG_STORE_BASE_URL": "http://store.example.test/"}), self.assertRaises(ConfigError):
            load_config(self.root)
        self.edit_repo_toml(f'base_url = "{STORE}"', 'base_url = "http://store.hakmicapitalgroup.com/"')
        with self.assertRaises(ConfigError):
            self.reload()

    # --- public-artifact gate
    def test_login_required_package_is_refused(self):
        status, r = import_deb(self.cfg, tool_deb(self.work, package=NS + "hcgterminal.unleashed"), PROV)
        self.assertEqual(status, "rejected")
        self.assertIn("access-not-public", r.codes())
        self.assertEqual(load_lock(self.cfg)["packages"], [])
        self.assertEqual(list((self.root / "pool").rglob("*.deb")), [])

    def test_paid_package_is_refused(self):
        cfg = self.set_products('[[products]]\nkey = "p"\nname = "P"\nstore_slug = "p"\naccess = "paid"\npackage_ids = ["%spaidtool"]\n' % NS)
        status, r = import_deb(cfg, tool_deb(self.work, package=NS + "paidtool"), PROV)
        self.assertEqual(status, "rejected")
        self.assertIn("access-not-public", r.codes())

    def test_unmapped_namespace_package_is_refused(self):
        status, r = import_deb(self.cfg, tool_deb(self.work, package=NS + "unmapped"), PROV)
        self.assertEqual(status, "rejected")
        self.assertIn("access-unmapped", r.codes())
        # Packages outside the namespace are not products of HCG, so the mapping rule does not apply.
        status, r = import_deb(self.cfg, tool_deb(self.work, package="org.example.tool"), PROV)
        self.assertEqual(status, "imported", r.render())

    def test_gated_package_in_pool_blocks_validate_build_and_verify(self):
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work), PROV)[0], "imported")
        site = build(self.cfg, PAGES, self.tmp / "site").out_dir
        # The product is later changed to login-required while its package is still in the pool.
        fixtures_gated = FIXTURE_PRODUCT.replace('access = "public"', 'access = "login-required"')
        (self.root / "config" / "products.toml").write_text(fixtures_gated)
        self.reload()
        r = Report("repository")
        check_repository(self.cfg, load_pool(self.cfg, r), r)
        self.assertIn("access-not-public", r.codes())
        with self.assertRaises(BuildError) as ctx:
            build(self.cfg, PAGES, self.tmp / "site2")
        self.assertIn("access-not-public", ctx.exception.report.codes())
        self.assertFalse((self.tmp / "site2").exists())
        # A snapshot that already contains it is caught by verification too.
        self.assertIn("index-access-not-public", self.verify_dir(site).report.codes())
        with mock.patch.dict(os.environ, {"HCG_REPO_ROOT": str(self.root)}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["validate"]), 1)
            self.assertEqual(cli.main(["build", "--target", "pages", "--out", str(self.tmp / "site3")]), 1)
            self.assertEqual(cli.main(["inspect", str(tool_deb(self.work, package=NS + "unmapped"))]), 1)

    # --- product pages
    def test_product_pages(self):
        res = build(self.cfg, PAGES, self.tmp / "site")
        site = res.out_dir
        landing = (site / "index.html").read_text()
        self.assertFalse((site / "repo" / "products").exists())
        self.assertTrue((site / "assets" / "store-access.js").is_file())
        for key, (slug, edition, ids) in REAL.items():
            with self.subTest(key=key):
                html = (site / "products" / key / "index.html").read_text()
                path = f"/apps/{slug}" + (f"?edition={edition}" if edition else "")
                login = STORE + "auth/login?return_to=" + path.replace("/", "%2F").replace("?", "%3F").replace("=", "%3D")
                self.assertIn(f'href="{login}">Sign in to check access</a>', html)
                self.assertIn(f'href="{STORE}{path[1:]}">View in HCG Store</a>', html)
                self.assertIn(f'data-access-url="{STORE}api/v1/repo/products/{key}/access"', html)
                self.assertIn("Downloads are not available yet.", html)
                self.assertIn("Buy Product", html)
                self.assertIn(f'<link rel="canonical" href="{PAGES}products/{key}/">', html)
                self.assertIn('<script src="../../assets/store-access.js" defer></script>', html)
                self.assertNotIn("sileo://package/", html)
                self.assertNotRegex(html, r"\.deb[\"'?#]")
                self.assertIsNone(re.search(r"<script(?![^>]*src=)", html), "inline script")
                for pkg in ids:
                    self.assertIn(f"<code>{pkg}</code>", html)
                self.assertIn(f'href="products/{key}/">Product page</a>', landing)
        self.assertIn("This product has no Debian package", (site / "products" / "hcg-sentinel" / "index.html").read_text())
        self.assertIn("Rootless (<code>iphoneos-arm64</code>)", (site / "products" / "hcg-nexus" / "index.html").read_text())
        v = self.verify_dir(site)
        self.assertTrue(v.report.ok, v.report.render())

    def test_public_product_page_offers_install(self):
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work), PROV)[0], "imported")
        site = build(self.cfg, PAGES, self.tmp / "site").out_dir
        html = (site / "products" / "test-fixtures" / "index.html").read_text()
        self.assertIn(f'href="sileo://package/{NS}testtool"', html)
        self.assertIn(f'href="../../repo/depictions/{NS}testtool/"', html)
        self.assertNotIn("Downloads are not available yet.", html)
        self.assertIn('href="products/test-fixtures/">Product page</a>', (site / "index.html").read_text())

    def test_verify_catches_broken_product_pages(self):
        site = build(self.cfg, PAGES, self.tmp / "site").out_dir
        nexus = site / "products" / "hcg-nexus" / "index.html"
        nexus.write_text(nexus.read_text().replace(
            "</article>", f'<a href="../../repo/debs/{NS}hcgnexus_0.2.0_iphoneos-arm64.deb">.deb</a>'
                          f'<a href="sileo://package/{NS}hcgnexus">Install</a></article>'))
        term = site / "products" / "hcg-terminal-unleashed" / "index.html"
        term.write_text(term.read_text().replace("View in HCG Store", "View").replace(
            "Downloads are not available yet.", "").replace("</main>", "<script>alert(1)</script></main>"))
        term_store = f'href="{STORE}apps/hcg-terminal?edition=unleashed"'
        term.write_text(term.read_text().replace(term_store, 'href="https://elsewhere.example/"'))
        (site / "products" / "hcg-sentinel" / "index.html").unlink()
        landing = site / "index.html"
        landing.write_text(landing.read_text().replace('href="products/hcg-nexus/"', 'href="#"'))
        codes = self.verify_dir(site).report.codes()
        for code in ("product-page-install-link", "product-page-store-link", "product-page-download-notice",
                     "product-page-inline-script", "product-page-missing", "site-product-link"):
            self.assertIn(code, codes)

    # --- payment_endpoint
    def test_payment_endpoint(self):
        site = build(self.cfg, PAGES, self.tmp / "site").out_dir
        self.assertFalse((site / "repo" / "payment_endpoint").exists())
        (site / "repo" / "payment_endpoint").write_text(STORE + "sileo/")
        self.assertIn("payment-endpoint-unexpected", self.verify_dir(site).report.codes())

        self.edit_repo_toml('endpoint = ""', f'endpoint = "{STORE}api/v1/sileo/"')
        self.reload()
        site = build(self.cfg, PAGES, self.tmp / "site2").out_dir
        self.assertEqual((site / "repo" / "payment_endpoint").read_text(), STORE + "api/v1/sileo/")
        v = self.verify_dir(site)
        self.assertTrue(v.report.ok, v.report.render())
        (site / "repo" / "payment_endpoint").write_text("https://elsewhere.example/")
        self.assertIn("payment-endpoint", self.verify_dir(site).report.codes())

    def test_payment_endpoint_must_be_https_under_store(self):
        for bad in ("http://store.hakmicapitalgroup.com/api/v1/sileo/", "https://payments.example/sileo/",
                    STORE + "api/v1/sileo/?token=x"):
            with self.subTest(bad=bad):
                self.edit_repo_toml('endpoint = ""', f'endpoint = "{bad}"')
                with self.assertRaises(ConfigError):
                    self.reload()
                self.edit_repo_toml(f'endpoint = "{bad}"', 'endpoint = ""')

    def test_store_access_script_is_csp_friendly(self):
        from hcgrepo.site import ASSET_DIR
        js = (ASSET_DIR / "store-access.js").read_text()
        for banned in ("eval(", "new Function", "innerHTML", "outerHTML", "document.write", "insertAdjacentHTML",
                       "localStorage", "sessionStorage", "Authorization"):
            self.assertNotIn(banned, js)
        self.assertIn('credentials: "include"', js)
        self.assertIn('Accept: "application/json"', js)
        self.assertIn("TIMEOUT_MS = 5000", js)


if __name__ == "__main__":
    unittest.main()

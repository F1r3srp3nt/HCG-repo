"""Isolated Sileo diagnostic sources: each adds exactly one component to a minimal source, never touches
/repo/, never contains a .deb, and is only built when asked for."""

from support import NS, RepoTestCase, tool_deb
from hcgrepo import debian
from hcgrepo.diagnostics import ALL, CONTROL, VARIANTS, build_all, verify_all
from hcgrepo.importer import import_deb
from hcgrepo.server import BackgroundServer
from hcgrepo.site import build

PROD = "https://repo.hakmicapitalgroup.com/"
PKG = NS + "testtool"
COMPONENT_FILES = {
    "icons": {"CydiaIcon.png", "CydiaIcon@2x.png", "CydiaIcon@3x.png"},
    "featured": {"sileo-featured.json", "assets"},
    "depiction": {"depictions"},   # plus assets/ when the package has an icon or header
    "payment": {"payment_endpoint", "provider"},
}
BASE_FILES = {"Release", "Packages", "Packages.xz", "Packages.gz", "Packages.bz2"}


class DiagnosticsTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.write_meta(PKG, '[display]\nsummary = "x"\n[access]\nlevel = "paid"\n')
        self.reload()
        self.assertEqual(import_deb(self.cfg, tool_deb(self.work), {"source": "t"})[0], "protected")
        self.site = build(self.cfg, PROD, self.tmp / "site").out_dir
        self.plain_repo = {f.relative_to(self.site / "repo"): f.read_bytes() for f in (self.site / "repo").rglob("*") if f.is_file()}
        build_all(self.cfg, PROD, self.site, package=PKG)

    def test_each_variant_adds_exactly_one_component(self):
        for v in VARIANTS:
            names = {p.name for p in (self.site / "diag" / v.key).iterdir()}
            want = set(BASE_FILES)
            for c in v.components:
                want |= COMPONENT_FILES.get(c, set())
            if "depiction" in v.components:
                names.discard("assets")
            self.assertEqual(names, want, v.key)
            release = (self.site / "diag" / v.key / "Release").read_bytes()
            release.decode("ascii")  # production Release is ASCII; diagnostics must not add an encoding variable
            self.assertIn(f"Origin: HCG Repo {v.title}\n".encode(), release)
            stanzas = debian.parse_stanzas((self.site / "diag" / v.key / "Packages").read_text())
            self.assertEqual(len(stanzas), 1 if "package" in v.components else 0, v.key)
            for st in stanzas:
                self.assertEqual(debian.field(st, "Package"), PKG)   # the real package ID, unchanged
                has_dep = debian.field(st, "SileoDepiction") is not None
                self.assertEqual(has_dep, "depiction" in v.components, v.key)

    def test_control_is_the_production_source_byte_for_byte(self):
        d = self.site / "diag" / CONTROL.key
        for name in ("Release", "Packages", "Packages.xz", "sileo-featured.json", "CydiaIcon@3x.png"):
            self.assertEqual((d / name).read_bytes(), (self.site / "repo" / name).read_bytes(), name)

    def test_production_untouched_no_package_files_and_all_variants_verify(self):
        after = {f.relative_to(self.site / "repo"): f.read_bytes() for f in (self.site / "repo").rglob("*") if f.is_file()}
        self.assertEqual(after, self.plain_repo)
        self.assertFalse(list(self.site.rglob("*.deb")))
        self.assertIn(b"noindex", (self.site / "diag" / "index.html").read_bytes())
        with BackgroundServer(self.site, "/") as srv:
            reports = verify_all(self.cfg, PROD, srv.url)
            for key, rep in reports.items():
                self.assertTrue(rep.ok, f"{key}: {rep.render()}")
            fn = debian.field(debian.parse_stanzas((self.site / "diag" / "d4-package" / "Packages").read_text())[0], "Filename")
            from hcgrepo.verify import Fetcher
            code, _, _ = Fetcher(PROD, srv.url).get(f"{PROD}diag/d4-package/{fn}")
            self.assertNotEqual(code, 200)
        self.assertEqual(set(reports), {v.key for v in ALL})

    def test_candidate_is_the_next_production_source_at_its_own_url(self):
        d = self.site / "diag" / "d8-candidate"
        self.assertIn(b"Origin: HCG Repo Candidate\n", (d / "Release").read_bytes())
        self.assertEqual((d / "Packages").read_bytes(), (self.site / "repo" / "Packages").read_bytes())  # protected: not indexed
        self.assertEqual((d / "CydiaIcon@3x.png").read_bytes(), (self.site / "repo" / "CydiaIcon@3x.png").read_bytes())
        self.assertIn("/diag/d8-candidate/assets/", (d / "sileo-featured.json").read_text())

    def test_not_built_by_default(self):
        self.assertFalse((build(self.cfg, PROD, self.tmp / "plain").out_dir / "diag").exists())


if __name__ == "__main__":
    import unittest
    unittest.main()

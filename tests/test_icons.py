"""Repository (Sources-list) icon: Sileo fetches <source>/CydiaIcon@3x.png, @2x, then CydiaIcon.png
and stops at the first download that returns data, so each variant must be the approved PNG at the
right size and must never be an HTML page."""

import io

from PIL import Image

from support import RepoTestCase
from hcgrepo.server import BackgroundServer
from hcgrepo.site import build
from hcgrepo.verify import REPO_ICONS, icon_problem, verify

PROD = "https://repo.hakmicapitalgroup.com/"


def png(w, h, color=(10, 200, 220)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


class IconTests(RepoTestCase):
    def built(self):
        return build(self.cfg, PROD, self.tmp / "site").out_dir

    def codes(self, site):
        with BackgroundServer(site, "/") as srv:
            return verify(PROD, srv.url, cfg=self.cfg).report.codes()

    def test_deployed_icons_are_the_approved_files(self):
        site = self.built()
        for name, side in REPO_ICONS.items():
            data = (site / "repo" / name).read_bytes()
            self.assertEqual(data, (self.root / "branding" / name).read_bytes(), name)
            self.assertIsNone(icon_problem(data, side), name)
        self.assertNotIn("repo-icon", self.codes(site))

    def test_wrong_scale_non_square_and_html_are_rejected(self):
        self.assertIn("expected 180x180", icon_problem(png(120, 120), 180))
        self.assertIn("not square", icon_problem(png(180, 120), 180))
        self.assertIn("HTML", icon_problem(b"<!DOCTYPE html><html>404</html>", 180))
        site = self.built()
        (site / "repo" / "CydiaIcon@3x.png").write_bytes(b"<!DOCTYPE html><html><body>Not Found</body></html>")
        self.assertIn("repo-icon", self.codes(site))

    def test_valid_png_that_is_not_the_approved_icon(self):
        site = self.built()
        (site / "repo" / "CydiaIcon@2x.png").write_bytes(png(120, 120))
        self.assertIn("repo-icon-mismatch", self.codes(site))


if __name__ == "__main__":
    import unittest
    unittest.main()

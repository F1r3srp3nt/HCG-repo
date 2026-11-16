"""The verifier walks a source the way Sileo does, including the parts where Sileo is lenient or
surprising: it reads payment_endpoint whatever the HTTP status, follows redirects, and draws
whatever an icon URL returns. A faulty server answer must show up as an error with evidence."""

import http.server
import json
import threading
from pathlib import Path

from support import RepoTestCase
from hcgrepo.site import build
from hcgrepo.verify import sileo_payment_endpoint, verify

PROD = "https://repo.hakmicapitalgroup.com/"


class _Server:
    """Serves a built site; `overrides` maps a path to (status, headers, body) to imitate a faulty host."""

    def __init__(self, root: Path, overrides: dict):
        outer = self

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(root), **kw)

            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path in outer.overrides:
                    status, headers, body = outer.overrides[self.path]
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                super().do_GET()

        self.overrides = overrides
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


class SileoProbeTests(RepoTestCase):
    def site(self):
        return build(self.cfg, PROD, self.tmp / "site").out_dir

    def probe(self, overrides=None, evidence=None):
        with _Server(self.site(), overrides or {}) as srv:
            return verify(PROD, srv.url, cfg=self.cfg, evidence=evidence)

    def test_payment_endpoint_is_read_like_sileo(self):
        self.assertIsNone(sileo_payment_endpoint(b"<!DOCTYPE html><html><body>404</body></html>"))
        self.assertIsNone(sileo_payment_endpoint(b"http://store.example/sileo"))
        self.assertIsNone(sileo_payment_endpoint(b""))
        self.assertEqual(sileo_payment_endpoint(b"  https://store.example/repo-api/v1/sileo\n"),
                         "https://store.example/repo-api/v1/sileo/")

    def test_clean_build_passes_and_evidence_is_saved(self):
        ev = self.tmp / "evidence"
        res = self.probe(evidence=ev)
        self.assertTrue(res.report.ok, res.report.render())
        rows = json.loads((ev / "responses.json").read_text())
        release = next(r for r in rows if r["url"].endswith("/repo/Release"))
        self.assertEqual((release["status"], release["redirects"]), (200, []))
        self.assertEqual((ev / release["body_file"]).read_bytes()[:16], b"Origin: HCG Repo")
        gpg = next(r for r in rows if r["url"].endswith("/repo/Release.gpg"))
        self.assertEqual(gpg["status"], 404)  # unsigned: Sileo accepts a missing Release.gpg

    def test_error_page_that_sileo_would_take_as_a_payment_provider(self):
        # A 404 whose body is a bare https URL: Sileo ignores the status and registers it.
        res = self.probe({"/repo/payment_endpoint": (404, {"Content-Type": "text/plain"}, b"https://evil.example/\n")})
        self.assertIn("payment-endpoint-unexpected", res.report.codes())

    def test_redirect_loop_and_downgrade_are_errors(self):
        loop = {"/repo/Packages.xz": (301, {"Location": "/repo/Packages.xz"}, b"")}
        self.assertIn("redirect-loop", self.probe(loop).report.codes())

    def test_html_answer_for_apt_metadata_is_an_error(self):
        page = b"<!DOCTYPE html><html><body>Sign in</body></html>"
        res = self.probe({"/repo/Release": (200, {"Content-Type": "text/html"}, page)})
        self.assertIn("release-html", res.report.codes())
        res = self.probe({"/repo/sileo-featured.json": (200, {"Content-Type": "text/html; charset=utf-8"}, page)})
        codes = res.report.codes()
        self.assertIn("content-type-html", codes)
        self.assertIn("featured-json", codes)

    def test_redirected_icon_is_reported(self):
        res = self.probe({"/repo/CydiaIcon@3x.png": (302, {"Location": "/repo/CydiaIcon@2x.png"}, b"")})
        self.assertIn("redirect", [c for c, _ in res.report.warnings])
        self.assertIn("repo-icon", [c for c, _ in res.report.errors])  # the @2x image is not 180x180


if __name__ == "__main__":
    import unittest
    unittest.main()

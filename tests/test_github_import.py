"""GitHub release / workflow-artifact imports against a local mock of the GitHub REST API."""

import hashlib
import http.server
import io
import json
import threading
import zipfile

from support import NS, RepoTestCase, tool_deb
from hcgrepo.importer import GitHub, ImportError_, fetch_release_asset, fetch_workflow_artifact

COMMIT = "a" * 40
TAG_OBJ = "b" * 40


class MockGitHub:
    def __init__(self, routes: dict[str, tuple[int, bytes]]):
        self.routes = routes
        self.requests: list[tuple[str, str | None]] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append((self.path, self.headers.get("Authorization")))
                code, body = outer.routes.get(self.path, (404, b'{"message":"Not Found"}'))
                self.send_response(code)
                if code in (301, 302, 307):
                    self.send_header("Location", body.decode())
                    body = b""
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.api = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def j(obj) -> tuple[int, bytes]:
    return 200, json.dumps(obj).encode()


class GitHubImportTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        import os
        self._np = os.environ.get("no_proxy")
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        self.deb = tool_deb(self.work).read_bytes()
        self.sha = hashlib.sha256(self.deb).hexdigest()
        self.dl = self.tmp / "dl"
        self.dl.mkdir()

    def release_routes(self, tag_commit=COMMIT, digest=None, annotated=True):
        name = f"{NS}testtool_1.0.0_iphoneos-arm64.deb"
        routes = {
            "/repos/o/r/releases/tags/v1.0.0": j({"id": 7, "draft": False, "html_url": "https://x/rel", "assets": [
                {"id": 99, "name": name, "size": len(self.deb), "digest": digest or f"sha256:{self.sha}"}]}),
            "/repos/o/r/releases/assets/99": (200, self.deb),
        }
        if annotated:
            routes["/repos/o/r/git/ref/tags/v1.0.0"] = j({"object": {"type": "tag", "sha": TAG_OBJ}})
            routes[f"/repos/o/r/git/tags/{TAG_OBJ}"] = j({"object": {"type": "commit", "sha": tag_commit}})
        else:
            routes["/repos/o/r/git/ref/tags/v1.0.0"] = j({"object": {"type": "commit", "sha": tag_commit}})
        return routes, name

    def test_release_import_records_provenance(self):
        routes, name = self.release_routes()
        m = MockGitHub(routes)
        try:
            path, prov = fetch_release_asset(GitHub(m.api, token=""), "o/r", "v1.0.0", COMMIT, name, self.dl)
        finally:
            m.close()
        self.assertEqual(path.read_bytes(), self.deb)
        self.assertEqual((prov["commit"], prov["release_id"], prov["asset_id"]), (COMMIT, 7, 99))
        self.assertEqual(prov["downloaded_sha256"], self.sha)

    def test_release_tag_must_match_commit(self):
        routes, name = self.release_routes(tag_commit="c" * 40, annotated=False)
        m = MockGitHub(routes)
        try:
            with self.assertRaisesRegex(ImportError_, "not the expected commit"):
                fetch_release_asset(GitHub(m.api, token=""), "o/r", "v1.0.0", COMMIT, name, self.dl)
        finally:
            m.close()

    def test_release_digest_mismatch(self):
        routes, name = self.release_routes(digest="sha256:" + "0" * 64)
        m = MockGitHub(routes)
        try:
            with self.assertRaisesRegex(ImportError_, "digest"):
                fetch_release_asset(GitHub(m.api, token=""), "o/r", "v1.0.0", COMMIT, name, self.dl)
        finally:
            m.close()

    def test_unpinned_references_refused(self):
        gh = GitHub("http://127.0.0.1:9", token="")
        for tag, commit in (("latest", COMMIT), ("v1", "main"), ("v1", "abc123")):
            with self.subTest(tag=tag, commit=commit), self.assertRaises(ImportError_):
                fetch_release_asset(gh, "o/r", tag, commit, "x.deb", self.dl)
        with self.assertRaises(ImportError_):
            fetch_release_asset(gh, "not-a-repo", "v1", COMMIT, "x.deb", self.dl)

    def artifact_routes(self, head_sha=COMMIT, members=None, conclusion="success"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n in members or ["out/pkg.deb"]:
                z.writestr(n, self.deb)
        zdata = buf.getvalue()
        return {
            "/repos/o/r/actions/runs/123": j({"head_sha": head_sha, "status": "completed", "conclusion": conclusion,
                                              "path": ".github/workflows/build.yml", "html_url": "https://x/run"}),
            "/repos/o/r/actions/runs/123/artifacts?per_page=100": j({"artifacts": [
                {"id": 5, "name": "deb-rootless", "expired": False, "digest": "sha256:" + hashlib.sha256(zdata).hexdigest()}]}),
            "/repos/o/r/actions/artifacts/5/zip": (200, zdata),
        }

    def test_artifact_import(self):
        m = MockGitHub(self.artifact_routes())
        try:
            path, prov = fetch_workflow_artifact(GitHub(m.api, token="t"), "o/r", 123, COMMIT, "deb-rootless", None, self.dl)
        finally:
            m.close()
        self.assertEqual(path.read_bytes(), self.deb)
        self.assertEqual((prov["run_id"], prov["artifact_id"], prov["member"]), (123, 5, "out/pkg.deb"))
        self.assertTrue(all(auth == "Bearer t" for _, auth in m.requests))

    def test_token_not_sent_to_redirected_download(self):
        routes = self.artifact_routes()
        zdata = routes["/repos/o/r/actions/artifacts/5/zip"][1]
        routes["/repos/o/r/actions/artifacts/5/zip"] = (302, b"/blob/signed-zip")
        routes["/blob/signed-zip"] = (200, zdata)
        m = MockGitHub(routes)
        try:
            fetch_workflow_artifact(GitHub(m.api, token="t"), "o/r", 123, COMMIT, "deb-rootless", None, self.dl)
        finally:
            m.close()
        auth = dict(m.requests)
        self.assertEqual(auth["/repos/o/r/actions/artifacts/5/zip"], "Bearer t")
        self.assertIsNone(auth["/blob/signed-zip"])

    def test_artifact_guards(self):
        cases = [
            (self.artifact_routes(head_sha="d" * 40), None, "not the expected commit"),
            (self.artifact_routes(conclusion="failure"), None, "only successful runs"),
            (self.artifact_routes(members=["a.deb", "b.deb"]), None, "choose one with --member"),
            (self.artifact_routes(members=["a.deb", "b.deb"]), "c.deb", "does not contain"),
        ]
        for routes, member, msg in cases:
            m = MockGitHub(routes)
            try:
                with self.subTest(msg=msg), self.assertRaisesRegex(ImportError_, msg):
                    fetch_workflow_artifact(GitHub(m.api, token="t"), "o/r", 123, COMMIT, "deb-rootless", member, self.dl)
            finally:
                m.close()
        with self.assertRaisesRegex(ImportError_, "token"):
            fetch_workflow_artifact(GitHub("http://127.0.0.1:9", token=""), "o/r", 1, COMMIT, "x", None, self.dl)


if __name__ == "__main__":
    import unittest
    unittest.main()

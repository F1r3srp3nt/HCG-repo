"""Serve a built site under a URL prefix, the way GitHub Pages serves a project site.

`hcgrepo serve --prefix /HCG-repo/` makes http://127.0.0.1:8000/HCG-repo/repo/ the APT source,
and everything outside the prefix returns 404. That reproduces project-Pages path handling, so a
metadata URL that forgets (or doubles) the prefix fails locally instead of on a device.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path


class PrefixHandler(http.server.SimpleHTTPRequestHandler):
    prefix = "/"

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if not clean.startswith(self.prefix):
            return str(Path(self.directory) / ".__outside_prefix__")
        return super().translate_path("/" + clean[len(self.prefix):])

    def send_head(self):
        clean = self.path.split("?", 1)[0]
        if clean + "/" == self.prefix:
            # GitHub Pages redirects /HCG-repo to /HCG-repo/
            self.send_response(301)
            self.send_header("Location", self.prefix)
            self.end_headers()
            return None
        return super().send_head()

    def log_message(self, fmt, *args):  # quieter default
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)


def make_server(directory: Path, prefix: str = "/", host: str = "127.0.0.1", port: int = 0,
                verbose: bool = False) -> http.server.ThreadingHTTPServer:
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if not prefix.endswith("/"):
        prefix += "/"
    handler = type("Handler", (PrefixHandler,), {"prefix": prefix})
    srv = http.server.ThreadingHTTPServer((host, port), functools.partial(handler, directory=str(directory)))
    srv.verbose = verbose  # type: ignore[attr-defined]
    srv.prefix = prefix  # type: ignore[attr-defined]
    return srv


class BackgroundServer:
    def __init__(self, directory: Path, prefix: str = "/"):
        self.srv = make_server(directory, prefix)
        self.prefix = self.srv.prefix  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.srv.server_address[:2]
        return f"http://{host}:{port}{self.prefix}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()

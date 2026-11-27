"""TEST environment for HCG Repo commercial distribution (docs/COMMERCIAL_TEST.md).

Builds, entirely in temporary directories:
  - real .deb files (dpkg-deb) for TEST products in the HCG namespace;
  - an HCG Store TEST catalogue (the Store's fixture catalogue plus these products), offers and a PRIVATE artifact
    store that is never inside the HCG Repo site;
  - an HCG Repo root whose paid packages are Store-held (recorded as protected, indexed as cydia::commercial with an
    undeployed `protected/` Filename) and whose payment_endpoint is the TEST Store;
then starts HCG Store's real server code over HTTPS (tools/commercial-test/store-test-server.mts) with the Store's
HCG Connect protocol mock, its test entitlement ledger and its test payment provider.

Protocol clients reproduce the exact requests of Sileo (Sileo/Sileo c9f7015, Payment Manager/PaymentProvider.swift)
and Zebra (zbrateam/Zebra v1 e2f3c27, ZBPaymentVendor.m, ZBDownloadManager.m). They are protocol fixtures, not the
apps: physical-device results are recorded separately (docs/DEVICE_QUALIFICATION_COMMERCIAL.md).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path

from support import NS, REPO, control, make_deb

STORE_CHECKOUT = Path(os.environ.get("HCG_STORE_CHECKOUT", "/home/user/f1r3srp3nt/store/web-hcg-store"))
TSX = STORE_CHECKOUT / "node_modules" / ".bin" / "tsx"
AVAILABLE = TSX.exists() and (STORE_CHECKOUT / "server" / "src" / "app.ts").exists() and shutil.which("openssl")

# TEST products (never in production configuration).
PRO, SECOND, TRIAL, FREE = NS + "testpro", NS + "testsecond", NS + "testtrial", NS + "testfree"
PRODUCTS = {
    # store product id: (HCG Repo key, debian package, central id, price minor units or None)
    "hcg-test-pro": ("test-pro", PRO, "hcg.test.pro", 499),
    "hcg-test-second": ("test-second", SECOND, "hcg.test.second", 299),
    "hcg-test-trial": ("test-trial", TRIAL, "hcg.test.trial", 199),
}
UDID = "00008030-TESTDEVICE0000"   # what Sileo/Zebra send; HCG must never treat it as a credential
WEBHOOK_SECRET = "hcg-repo-test-webhook-secret-0123456789"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Http:
    """Minimal HTTP(S) client: no proxy, no automatic redirects, optional cookie jar, trusts only the TEST CA."""

    def __init__(self, cafile: Path | None = None):
        self.ctx = ssl.create_default_context(cafile=str(cafile)) if cafile else None
        self.cookies: dict[str, str] = {}
        self.log: list[str] = []

    def request(self, method: str, url: str, body=None, headers: dict | None = None, use_cookies=False):
        u = urllib.parse.urlsplit(url)
        conn = (http.client.HTTPSConnection(u.hostname, u.port, context=self.ctx, timeout=30) if u.scheme == "https"
                else http.client.HTTPConnection(u.hostname, u.port, timeout=30))
        h = dict(headers or {})
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            h.setdefault("content-type", "application/json")
        if use_cookies and self.cookies:
            h["cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        path = u.path + (("?" + u.query) if u.query else "")
        conn.request(method, path or "/", body=data, headers=h)
        r = conn.getresponse()
        raw = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        if use_cookies:
            for k, v in r.getheaders():
                if k.lower() == "set-cookie":
                    name, _, rest = v.partition("=")
                    val = rest.split(";", 1)[0]
                    if val == "" or "max-age=0" in v.lower():
                        self.cookies.pop(name, None)
                    else:
                        self.cookies[name] = val
        self.log.append(f"{method} {u.scheme}://{u.netloc}{u.path} -> {r.status}")
        conn.close()
        return r.status, hdrs, raw

    def json(self, method, url, body=None, **kw):
        st, h, raw = self.request(method, url, body, **kw)
        try:
            return st, json.loads(raw or b"null")
        except ValueError:
            return st, {"_raw": raw[:200].decode("utf-8", "replace")}


class PaymentClient:
    """The payment-provider protocol exactly as Sileo or Zebra speak it (see module docstring)."""

    def __init__(self, flavour: str, repo_url: str, http: Http):
        assert flavour in ("sileo", "zebra", "irisin")
        self.flavour, self.repo_url, self.http = flavour, repo_url, http
        self.endpoint = ""
        self.token: str | None = None
        self.payment_secret: str | None = None

    # discovery: <repo>/payment_endpoint (both clients), HTTPS only
    def discover(self) -> str:
        if self.flavour == "zebra":
            # Zebra looks for a payment endpoint only on HTTPS sources (ZBDatabaseManager.m:363).
            assert self.repo_url.startswith("https://"), "Zebra ignores payment_endpoint on non-HTTPS sources"
        st, _, raw = self.http.request("GET", self.repo_url + "payment_endpoint")
        assert st == 200, f"payment_endpoint HTTP {st}"
        text = raw.decode()
        if self.flavour == "irisin":
            # Irisin passes the body to URL(string:) without trimming: any whitespace breaks it.
            assert text == text.strip(), "Irisin cannot parse a payment_endpoint with surrounding whitespace"
        ep = text.strip()
        if not ep.endswith("/"):
            ep += "/"
        assert ep.startswith("https://"), "both clients refuse a non-HTTPS payment endpoint"
        self.endpoint = ep
        return ep

    def info(self):
        return self.http.json("GET", self.endpoint + "info")

    def _body(self, extra=None, include_token=True):
        b = {"udid": UDID, "device": "iPhone12,5"}
        if include_token:
            if self.flavour == "zebra":
                b["token"] = self.token or "none"     # Zebra sends "none" when signed out
            elif self.token:
                b["token"] = self.token
        b.update(extra or {})
        return b

    def authenticate(self) -> bool:
        """ASWebAuthenticationSession / ZBSafariAuthenticationSession: follow redirects in a fresh browser-like
        cookie jar until the provider redirects to sileo://authentication_success (both apps use scheme 'sileo')."""
        browser = Http(None)
        browser.ctx = self.http.ctx
        url = self.endpoint + f"authenticate?udid={UDID}&model=iPhone12%2C5"
        for _ in range(12):
            st, h, _ = browser.request("GET", url, use_cookies=True)
            loc = h.get("location")
            if st not in (301, 302, 303, 307, 308) or not loc:
                return False
            if loc.startswith("sileo://"):
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)
                self.token, self.payment_secret = q.get("token", [None])[0], q.get("payment_secret", [None])[0]
                return bool(self.token and self.payment_secret)
            url = urllib.parse.urljoin(url, loc)
        return False

    def user_info(self):
        return self.http.json("POST", self.endpoint + "user_info", self._body())

    def package_info(self, pkg):
        return self.http.json("POST", self.endpoint + f"package/{pkg}/info", self._body())

    def purchase(self, pkg):
        return self.http.json("POST", self.endpoint + f"package/{pkg}/purchase",
                              self._body({"payment_secret": self.payment_secret}))

    def authorize_download(self, pkg, version, arch="iphoneos-arm64"):
        extra = {"version": version, "repo": self.repo_url, "architecture": arch}
        if self.flavour == "irisin":
            extra["payment_secret"] = self.payment_secret     # Irisin also sends it here (VendorService.swift:93-99)
        return self.http.json("POST", self.endpoint + f"package/{pkg}/authorize_download", self._body(extra))

    def sign_out(self):
        st, b = self.http.json("POST", self.endpoint + "sign_out", self._body())
        self.token = self.payment_secret = None
        return st, b


class SileoInstall:
    """Sileo's install path for one package, step by step as Sileo c9f7015 does it, ending in a real dpkg install.

    1. Packages index of the source (Sileo parses the stanza; `Tag: cydia::commercial` marks a provider package).
    2. Free package: download <source>/<Filename>. Provider package: package/<id>/info, then authorize_download for the
       exact version and architecture, and download the returned https URL (PaymentProvider.downloadURL).
    3. Compare the file's SHA-256 with the index (Sileo DownloadManager refuses a mismatch).
    4. Install. Sileo runs `apt-get install --allow-unauthenticated` on the downloaded file; here real dpkg installs it
       into an isolated root with its own database (tools/hcgrepo/deepcheck._dpkg). Not a device: no SpringBoard,
       no uicache, no jailbreak.
    """

    def __init__(self, http: Http, repo_url: str, client: "PaymentClient | None", root: Path):
        self.http, self.repo_url, self.client, self.root = http, repo_url, client, root
        for d in ("root", "admin/info", "admin/updates", "admin/triggers", "debs"):
            (root / d).mkdir(parents=True, exist_ok=True)
        for f in ("status", "available"):
            (root / "admin" / f).touch()
        self.steps: list[str] = []

    def stanza(self, pkg: str, version: str | None = None) -> dict:
        st, _, raw = self.http.request("GET", self.repo_url + "Packages")
        assert st == 200, f"Packages HTTP {st}"
        for s in raw.decode().split("\n\n"):
            f = dict(l.split(": ", 1) for l in s.splitlines() if ": " in l and not l.startswith(" "))
            if f.get("Package") == pkg and (version is None or f.get("Version") == version):
                return f
        raise LookupError(f"{pkg} {version or ''} is not in the index")

    def download(self, pkg: str, version: str | None = None) -> tuple[Path, dict]:
        f = self.stanza(pkg, version)
        commercial = "cydia::commercial" in [x.strip() for x in f.get("Tag", "").split(",")]
        if commercial:
            assert self.client and self.client.endpoint, "a provider package needs the source's payment provider"
            st, info = self.client.package_info(pkg)
            self.steps.append(f"package/{pkg}/info -> {st} price={info.get('price')} purchased={info.get('purchased')}")
            st, b = self.client.authorize_download(pkg, f["Version"], f["Architecture"])
            self.steps.append(f"authorize_download {f['Version']} {f['Architecture']} -> {st}")
            if st != 200 or not b.get("url"):
                raise PermissionError(b.get("error") or f"authorize_download HTTP {st}")
            url = b["url"]
            assert url.startswith("https://"), "Sileo requires an https download URL"
        else:
            url = urllib.parse.urljoin(self.repo_url, f["Filename"])
        st, _, data = self.http.request("GET", url)
        self.steps.append(f"GET {'<authorized URL>' if commercial else f['Filename']} -> {st} ({len(data)} bytes)")
        if st != 200:
            raise PermissionError(f"download HTTP {st}")
        sha = hashlib.sha256(data).hexdigest()
        if sha != f["SHA256"]:
            raise ValueError(f"SHA-256 mismatch: index {f['SHA256']}, got {sha}")       # Sileo refuses it
        out = self.root / "debs" / f"{pkg}_{f['Version']}.deb"
        out.write_bytes(data)
        return out, f

    def install(self, pkg: str, version: str | None = None) -> dict:
        import sys as _sys
        _sys.path.insert(0, str(REPO / "tools"))
        from hcgrepo.deepcheck import _dpkg, _status
        deb, f = self.download(pkg, version)
        cp = _dpkg(self.root, "-i", str(deb))
        status = _status(self.root, pkg)
        self.steps.append(f"dpkg -i -> exit {cp.returncode}, status {status!r}")
        files = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True).stdout
        return {"exit": cp.returncode, "status": status, "version": f["Version"], "sha256": f["SHA256"],
                "stderr": cp.stderr[-400:], "files": [l.split()[-1] for l in files.splitlines() if not l.endswith("/")]}


class CommercialEnv:
    """Everything a test needs. start() builds and launches; stop() tears down."""

    def __init__(self, tmp: Path, root: Path, work: Path):
        self.tmp, self.root, self.work = tmp, root, work
        self.debs: dict[tuple[str, str], Path] = {}
        self.proc = None

    # ------------------------------------------------------------------ artifacts
    def deb(self, pkg: str, version: str) -> Path:
        d = self.work / f"{pkg}-{version}"
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{pkg}_{version}_iphoneos-arm64.deb"
        make_deb(out, control(pkg, version, Depends="firmware (>= 15.0)"),
                 files={f"var/jb/usr/bin/{pkg.rsplit('.', 1)[-1]}": (f"#!/bin/sh\necho {pkg} {version}\n".encode(), 0o755)},
                 compression="gzip")
        self.debs[(pkg, version)] = out
        return out

    @staticmethod
    def sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    # ------------------------------------------------------------------ Store side
    def store_registry(self):
        cat = self.tmp / "store-catalogue"
        shutil.copytree(STORE_CHECKOUT / "tests" / "fixtures" / "catalogue-demo", cat)
        products = json.loads((cat / "products.json").read_text())
        releases = json.loads((cat / "releases.json").read_text())
        template = next(p for p in products["products"] if p["id"] == "fixture-quasar-shell")
        rtemplate = next(r for r in (releases["releases"] if isinstance(releases, dict) else releases)
                         if r["productId"] == "fixture-quasar-shell")
        rel_list = releases["releases"] if isinstance(releases, dict) else releases
        offers, artifacts = [], []
        self.storage = self.tmp / "store-private-storage"
        self.storage.mkdir()
        versions = {"hcg-test-pro": ["1.0.0", "0.9.0", "0.8.0"], "hcg-test-second": ["1.0.0"], "hcg-test-trial": ["1.0.0"]}
        for pid, (key, pkg, central, price) in PRODUCTS.items():
            prod = json.loads(json.dumps(template))
            prod.update(id=pid, slug=pid, name=f"{key.replace('-', ' ').title()} (TEST)", monogram="T",
                        summary="TEST product for HCG Repo commercial acceptance tests.", description=["TEST only."],
                        currentReleaseId=f"{pid}-1-0-0", registry={"status": "central", "centralId": central, "references": []},
                        hcgRepo=[{"key": key, "editionId": "jailbreak"}])
            ed = next(e for e in prod["editions"] if e["id"] == "unleashed")
            ed = dict(ed, id="jailbreak", name=f"{key} jailbreak (TEST)", centralEditionKey="jailbreak",
                      identifiers=[{"kind": "debian-package", "value": pkg}])
            prod["editions"] = [ed]
            products["products"].append(prod)
            for v in versions[pid]:
                deb = self.deb(pkg, v)
                rid = f"{pid}-{v.replace('.', '-')}"
                rel = json.loads(json.dumps(rtemplate))
                rel.update(id=rid, productId=pid, version=v, build=v.replace(".", ""), status="approved")
                art = dict(rel["artifacts"][0], id="jb-rootless", editionId="jailbreak", fileName=deb.name,
                           architectures=["iphoneos-arm64"], sha256=self.sha(deb), sizeBytes=deb.stat().st_size)
                rel["artifacts"] = [art]
                rel_list.append(rel)
                key_path = f"{pid}/{deb.name}"
                (self.storage / pid).mkdir(exist_ok=True)
                shutil.copyfile(deb, self.storage / key_path)
                artifacts.append({"id": f"{pid}-{v.replace('.', '-')}-arm64", "productId": pid, "releaseId": rid,
                                  "catalogueArtifactId": "jb-rootless", "editionId": "jailbreak", "fileName": deb.name,
                                  "contentType": "application/vnd.debian.binary-package", "sha256": self.sha(deb),
                                  "storageKey": key_path, "deliveryApproved": v != "0.8.0", "testOnly": True})
            if pid != "hcg-test-trial":
                offers.append({"id": f"{pid}-test", "productId": pid, "centralProductId": central, "editionId": "jailbreak",
                               "title": f"{key} (TEST)", "kind": "one-time", "interval": None, "currency": "EUR",
                               "unitAmount": price, "grants": [{"edition": "jailbreak"}], "status": "approved-for-sale",
                               "testOnly": True})
        (cat / "products.json").write_text(json.dumps(products, indent=1))
        (cat / "releases.json").write_text(json.dumps(releases, indent=1))
        reg = self.tmp / "store-registry"
        reg.mkdir()
        (reg / "offers.json").write_text(json.dumps({"offers": offers}))
        (reg / "artifacts.json").write_text(json.dumps({"artifacts": artifacts}))
        return cat, reg

    def start_store(self, repo_origin: str | None = None):
        """repo_origin defaults to the HTTPS origin the HCG Repo TEST site will be served on (self.repo_port)."""
        self.repo_port = free_port()
        repo_origin = repo_origin or f"https://127.0.0.1:{self.repo_port}"
        cat, reg = self.store_registry()
        self.port, idp, self.control_port = free_port(), free_port(), free_port()
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(self.tmp / "key.pem"),
                        "-out", str(self.tmp / "cert.pem"), "-days", "1", "-subj", "/CN=127.0.0.1",
                        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
        conf = {"storeDir": str(STORE_CHECKOUT), "port": self.port, "idpPort": idp, "controlPort": self.control_port,
                "certFile": str(self.tmp / "cert.pem"), "keyFile": str(self.tmp / "key.pem"),
                "catalogueDir": str(cat), "offersFile": str(reg / "offers.json"),
                "artifactsFile": str(reg / "artifacts.json"), "storageDir": str(self.storage),
                "repoOrigins": repo_origin, "webhookSecret": WEBHOOK_SECRET, "downloadTtlSeconds": 60}
        static = self.tmp / "store-static"
        static.mkdir(exist_ok=True)
        (static / "index.html").write_text("<!doctype html><title>HCG Store (TEST)</title><h1>HCG Store (TEST)</h1>"
                                           "<p>TEST environment: HCG Connect protocol mock, test ledger, test payments.</p>")
        conf["staticDir"] = str(static)
        (self.tmp / "store-test.json").write_text(json.dumps(conf))
        self.log_path = self.tmp / "store-server.log"
        self._logf = open(self.log_path, "w")
        self.proc = subprocess.Popen([str(TSX), str(REPO / "tools" / "commercial-test" / "store-test-server.mts"),
                                      str(self.tmp / "store-test.json")], stdout=self._logf,
                                     stderr=subprocess.STDOUT, cwd=str(STORE_CHECKOUT))
        for _ in range(120):
            if "READY" in self.log_path.read_text(errors="replace"):
                break
            if self.proc.poll() is not None:
                raise RuntimeError("TEST Store failed to start:\n" + self.log_path.read_text()[-3000:])
            time.sleep(0.5)
        else:
            raise RuntimeError("TEST Store did not start")
        self.origin = f"https://127.0.0.1:{self.port}"
        self.http = Http(self.tmp / "cert.pem")
        # The provider path HCG Repo advertises (docs/STORE_PROVIDER.md); a Store without the proposed alias patch
        # serves it only at the original /repo-api/v1/sileo/.
        st, _, _ = self.http.request("GET", self.origin + "/api/sileo/v1/info")
        self.provider_alias = st == 200
        self.endpoint = self.origin + ("/api/sileo/v1/" if self.provider_alias else "/repo-api/v1/sileo/")

    def serve_repo_https(self, site: Path, port: int = 0) -> str:
        """Serve the HCG Repo TEST site over HTTPS (same certificate): Zebra uses payment endpoints only on HTTPS
        sources, and the Profile's credentialed request to the Store needs a same-site HTTPS origin."""
        import functools
        import http.server
        import threading
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
        handler.log_message = lambda *a, **k: None
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(self.tmp / "cert.pem"), str(self.tmp / "key.pem"))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.repo_server = srv
        return f"https://127.0.0.1:{srv.server_address[1]}/"

    def control(self, path: str, body=None):
        st, b = Http(None).json("POST", f"http://127.0.0.1:{self.control_port}/{path.lstrip('/')}", body or {})
        assert st == 200, (path, st, b)
        return b

    def stop(self):
        if getattr(self, "repo_server", None):
            self.repo_server.shutdown()
        if getattr(self, "_logf", None):
            self._logf.close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    # ------------------------------------------------------------------ web customer (browser) against the Store
    def web_sign_in(self, sub: str, email: str) -> Http:
        """A browser session on the Store: /auth/login → HCG Connect protocol mock (PKCE) → /auth/callback."""
        self.control("next-user", {"sub": sub, "email": email, "name": sub})
        b = Http(self.tmp / "cert.pem")
        url = self.origin + "/auth/login?return_to=/account"
        for _ in range(10):
            st, h, _ = b.request("GET", url, use_cookies=True)
            loc = h.get("location")
            if st not in (301, 302, 303) or not loc:
                break
            if loc.startswith(self.origin + "/account") or loc == "/account":
                break
            url = urllib.parse.urljoin(url, loc)
        return b

    def web_buy(self, browser: Http, offer_id: str, outcome="pay") -> str:
        st, start = browser.json("POST", self.origin + "/api/v1/checkout", {"offerId": offer_id},
                                 headers={"origin": self.origin}, use_cookies=True)
        assert st == 201, (st, start)
        page = self.origin + start["redirectUrl"]
        st, _, _ = browser.request("GET", page, use_cookies=True)
        assert st == 200, st
        st, _, _ = browser.request("POST", page, {"outcome": outcome}, headers={"origin": self.origin}, use_cookies=True)
        assert st in (302, 303), st
        return start["checkoutId"]

    def refund(self, checkout_id: str, amount: int):
        import hmac
        raw = json.dumps({"id": f"evt_refund_{checkout_id}", "type": "payment.refunded", "checkoutId": checkout_id,
                          "paymentId": f"pay_test_{checkout_id}", "amount": amount, "currency": "EUR"})
        ts = int(time.time() * 1000) + int(self.control("clock", {"advanceSeconds": 0})["offsetMs"])
        sig = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.{raw}".encode(), hashlib.sha256).hexdigest()
        return self.http.json("POST", self.origin + "/api/v1/payments/webhook/test", raw.encode(),
                              headers={"hcg-test-signature": f"t={ts},v1={sig}", "content-type": "application/json"})

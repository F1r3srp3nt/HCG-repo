#!/usr/bin/env python3
"""STAGING end-to-end test: HCG Store release -> HCG Repo index -> Sileo download, with one real package.

Nothing here touches production, the real HCG Connect, real entitlements or a device:

1. Starts HCG Store's own server code in its TEST mode (tools/staging/store-staging-server.mts) with the
   mock HCG Connect provider and a TEST entitlement ledger.
2. Ships the package like HCG Executable Maker does (desktop bearer token; draft release; declared
   artifact; chunked upload; Store-side verification; submit) and has a DIFFERENT person approve it for
   public delivery, as the Store requires.
3. Records the approved artifact in a temporary copy of this repository with `import-store-release`, and
   builds a staging site whose payment_endpoint points at the staging Store.
4. Acts as Sileo, following Sileo's source (RepoManager, PaymentManager, PaymentProvider): refreshes the
   source, signs in through the provider, asks for the download and checks the bytes against the index.
5. Checks every denial: signed out, non-owner, revoked entitlement (including an already issued link),
   Sileo sign-out, Store withdrawal, and that no public path serves the file.

    python3 tools/staging/store_e2e.py --store-dir <store>/web-hcg-store --deb <the .deb> --out <dir>

The report (report.json, REPORT.md) lists each check as PASS or FAIL. Device behaviour is NOT covered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from hcgrepo import debian  # noqa: E402
from hcgrepo.config import load_config, load_lock, load_package_meta  # noqa: E402
from hcgrepo.debinspect import inspect_deb  # noqa: E402
from hcgrepo.server import BackgroundServer  # noqa: E402
from hcgrepo.site import build  # noqa: E402
from hcgrepo.store import check_release  # noqa: E402
from hcgrepo.verify import sileo_payment_endpoint, verify  # noqa: E402

# Placeholder https names. Sileo only accepts https endpoints and download URLs; the staging servers
# listen on 127.0.0.1 over http, so the client maps these names to them (see LIMITS in the report).
STAGING_REPO = "https://repo.staging.invalid/"
STAGING_STORE = "https://store.staging.invalid"
ENDPOINT = STAGING_STORE + "/repo-api/v1/sileo/"
UDID = "0" * 40  # a fixed dummy device identifier; never a real one
MODEL = "iPhone14,2"
PUBLISHER, APPROVER, OWNER, STRANGER = "staging-publisher", "staging-approver", "staging-owner", "staging-stranger"

# Store product/edition and Store distribution for each package HCG Repo maps (config/products.toml).
PRODUCTS = {
    "com.hakmicapitalgroup.hcgnexus": ("hcg-nexus", "jailbreak", "paid"),
    "com.hakmicapitalgroup.hcgterminal.unleashed": ("hcg-terminal", "unleashed", "free"),
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http(method: str, url: str, body=None, headers=None, opener=None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Accept": "application/json", **(headers or {})}
    if body is not None and raw is None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    op = opener or urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=60) as r:
            return r.status, r.read(), dict(r.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers.items()) if e.headers else {}


def js(b: bytes):
    try:
        return json.loads(b)
    except ValueError:
        return None


class Run:
    def __init__(self, out: Path):
        self.out = out
        self.checks: list[dict] = []
        self.notes: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail if detail else ''}", flush=True)
        return ok


class Sileo:
    """The parts of Sileo's payment-provider client this test needs, with Sileo's request shapes."""

    def __init__(self, store_origin: str):
        self.origin = store_origin
        self.token: str | None = None

    def map(self, url: str) -> str:
        return self.origin + url[len(STAGING_STORE):] if url.startswith(STAGING_STORE) else url

    def get_info(self):
        s, b, _ = http("GET", self.map(ENDPOINT + "info"))
        return s, js(b)

    def authenticate(self) -> tuple[str | None, str | None]:
        """ASWebAuthenticationSession: open authenticate?udid&model, follow redirects (with cookies) to sileo://."""
        jar = http_cookiejar()
        op = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPCookieProcessor(jar), urllib.request.ProxyHandler({}))
        url = self.map(ENDPOINT + f"authenticate?udid={UDID}&model={MODEL}")
        for _ in range(10):
            s, _, h = http("GET", url, opener=op, headers={"Accept": "text/html"})
            loc = next((v for k, v in h.items() if k.lower() == "location"), "")
            if not loc:
                return None, f"stopped at HTTP {s} {url}"
            if loc.startswith("sileo://"):
                q = parse_qs(urlsplit(loc).query)
                self.token = q.get("token", [None])[0]
                return loc, None
            url = urljoin(url, loc)
        return None, "too many redirects"

    def post(self, path: str, extra: dict | None = None, token: str | None = "default"):
        body = {"udid": UDID, "device": MODEL, **(extra or {})}
        tok = self.token if token == "default" else token
        if tok:
            body["token"] = tok
        s, b, _ = http("POST", self.map(ENDPOINT + path), body)
        d = js(b)
        # PaymentProvider.makeRequest: success == false or an "error" string is a failure.
        failed = not isinstance(d, dict) or (d.get("success") is not None and d.get("success") is not True) or isinstance(d.get("error"), str)
        return s, d, failed


def http_cookiejar():
    import http.cookiejar as cj  # local import: the module-level name "http" is the request helper
    return cj.CookieJar()


def start_store(store_dir: Path, work: Path):
    info = work / "staging-store.json"
    tsx = store_dir / "node_modules" / ".bin" / "tsx"
    if not tsx.exists():
        raise SystemExit(f"{tsx} missing: run `npm ci` in {store_dir} first")
    log = open(work / "staging-store.log", "w")
    proc = subprocess.Popen([str(tsx), str(ROOT / "tools/staging/store-staging-server.mts"), str(info)],
                            cwd=store_dir, env={**os.environ, "HCG_STORE_DIR": str(store_dir)}, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(120):
        if info.exists() and info.stat().st_size:
            return proc, json.loads(info.read_text())
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    proc.kill()
    raise SystemExit(f"staging Store did not start; see {work / 'staging-store.log'}")


def ship(run: Run, st: dict, deb: Path, version: str, build_no: str, approve: bool) -> dict:
    """Ship to Store exactly as HCG Executable Maker's HcgStoreClient does, then (optionally) approve."""
    info = inspect_deb(deb)
    product, edition, distribution = PRODUCTS[info.package]
    origin, ctl = st["storeOrigin"], st["control"]
    for sub, central in ((PUBLISHER, "hcg.store.publish"), (APPROVER, "hcg.store.approve")):
        http("POST", ctl + "/grant", {"sub": sub, "central": central, "edition": "*"})
    token = js(http("POST", ctl + "/token", {"sub": PUBLISHER})[1])["token"]
    auth = {"Authorization": f"Bearer {token}"}
    s, b, _ = http("POST", origin + "/api/v1/publishing/releases", {
        "productId": product, "editionId": edition, "version": version, "build": build_no, "channel": "stable",
        "releaseNotes": ["Staging test release."], "distribution": distribution, "eligibility": "public",
        "compatibility": {"minimumOs": {}, "architectures": [info.architecture], "notes": []},
        "destinations": ["hcg-store", "hcg-repo"]}, auth)
    rel = js(b) or {}
    run.check(f"Store accepts a draft release {version} build {build_no} for {product}/{edition}", s in (200, 201), f"HTTP {s} {rel.get('error') if s >= 400 else rel.get('id')}")
    s, b, _ = http("POST", f"{origin}/api/v1/publishing/releases/{rel['id']}/artifacts", {
        "fileName": deb.name, "platform": "ios", "format": "deb", "sizeBytes": info.size, "sha256": info.sha256,
        "architectures": [info.architecture], "kind": "jailbreak-package",
        "package": {"id": info.package, "version": info.version, "architecture": info.architecture}}, auth)
    dec = js(b) or {}
    run.check("Store issues a scoped upload URL for the declared .deb", s == 201, f"HTTP {s}")
    data = deb.read_bytes()
    chunk = int(dec.get("chunkBytes") or len(data))
    for start in range(0, len(data), chunk):
        part = data[start:start + chunk]
        s, _, _ = http("PUT", dec["uploadUrl"], raw=part, headers={"Content-Range": f"bytes {start}-{start + len(part) - 1}/{len(data)}"})
        if s != 200:
            run.check("chunked upload", False, f"HTTP {s} at byte {start}")
    s, b, _ = http("POST", f"{origin}/api/v1/publishing/artifacts/{dec['artifact']['id']}/complete", {}, auth)
    art = (js(b) or {}).get("artifact", {})
    run.check("Store verifies the upload (SHA-256, format, control fields, catalogue mapping)",
              s == 200 and art.get("status") == "verified" and art.get("sha256") == info.sha256,
              f"HTTP {s}, status {art.get('status')}, kind {art.get('kind')}")
    if approve:
        s, _, _ = http("POST", f"{origin}/api/v1/publishing/releases/{rel['id']}/submit", {}, auth)
        run.check("publisher submits the release for review", s == 200, f"HTTP {s}")
        s, b, _ = http("POST", ctl + "/approve", {"releaseId": rel["id"], "eligibility": "public", "confirm": version, "approver": APPROVER})
        run.check("a different person approves it for public delivery in the Store", s == 200, f"HTTP {s} {(js(b) or {}).get('error', '')}")
    s, b, _ = http("GET", f"{origin}/api/v1/publishing/releases/{rel['id']}", headers=auth)
    record = js(b)
    return {"record": record, "artifact": art.get("id"), "auth": auth}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store-dir", required=True, type=Path)
    ap.add_argument("--deb", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    run = Run(a.out)
    work = Path(tempfile.mkdtemp(prefix="hcg-staging-"))
    proc, st = start_store(a.store_dir.resolve(), work)
    try:
        _run(run, a, st, work)
        return 0 if run.checks and all(c["result"] == "PASS" for c in run.checks) else 1
    finally:
        proc.terminate()
        (a.out / "report.json").write_text(json.dumps({"checks": run.checks, "notes": run.notes}, indent=2) + "\n")
        failed = [c for c in run.checks if c["result"] == "FAIL"]
        lines = ["# Staging end-to-end: HCG Store -> HCG Repo -> Sileo protocol", "",
                 f"{len(run.checks) - len(failed)} passed, {len(failed)} failed. Automated, staging only; no device was used.", "",
                 "| Result | Check | Detail |", "|---|---|---|"]
        lines += [f"| {c['result']} | {c['check']} | {c['detail'].replace('|', '/')} |" for c in run.checks]
        lines += ["", "## Notes and limits", ""] + [f"- {n}" for n in run.notes]
        (a.out / "REPORT.md").write_text("\n".join(lines) + "\n")
        shutil.rmtree(work, ignore_errors=True)


def _tree_digest() -> str:
    h = hashlib.sha256()
    for d in ("pool", "packages", "config"):
        for f in sorted((ROOT / d).rglob("*")):
            if f.is_file():
                h.update(str(f.relative_to(ROOT)).encode() + f.read_bytes())
    h.update((ROOT / "repo-lock.json").read_bytes())
    return h.hexdigest()


def _run(run: Run, a, st: dict, work: Path) -> int:
    before = _tree_digest()
    try:
        return _run_checks(run, a, st, work)
    finally:
        run.check("this checkout's pool, packages, config and lock are unchanged (staging used a copy)", _tree_digest() == before)


def _run_checks(run: Run, a, st: dict, work: Path) -> int:
    info = inspect_deb(a.deb)
    run.notes.append(f"Package under test: {info.package} {info.version} {info.architecture}, {info.size} bytes, SHA-256 {info.sha256}.")
    run.notes.append(f"Staging Store: HCG Store server code from {a.store_dir} in TEST mode, mock HCG Connect, TEST entitlement ledger; "
                     f"staging-only central IDs {st['centralIds']} (real products have none yet).")
    run.notes.append("Sileo requires https for the payment endpoint and download URLs. The staging servers use http on 127.0.0.1; "
                     "the client maps https://store.staging.invalid and https://repo.staging.invalid to them. The Store's real public "
                     "origin must be https for Sileo to accept its download URLs.")

    # ---- 1. ship and approve (and one release left as a draft)
    shipped = ship(run, st, a.deb, info.version.split("-")[0], "1", approve=True)
    rec, art_id = shipped["record"], shipped["artifact"]
    run.check("Store record: approved for public, shipped to hcg-repo",
              rec.get("status") == "approved" and rec.get("approvedEligibility") == "public" and "hcg-repo" in rec.get("destinations", []),
              f"status {rec.get('status')}, eligibility {rec.get('approvedEligibility')}")
    (a.out / "store-release-approved.json").write_text(json.dumps(rec, indent=2) + "\n")

    # ---- 2. record it in a staging copy of HCG Repo
    root = work / "repo"
    for d in ("config", "branding", "packages", "pool"):
        if (ROOT / d).exists():
            shutil.copytree(ROOT / d, root / d)
    shutil.copy(ROOT / "repo-lock.json", root / "repo-lock.json")
    cfgp = root / "config" / "repo.toml"
    cfgp.write_text(cfgp.read_text().replace('payment_endpoint = ""', f'payment_endpoint = "{ENDPOINT}"'))
    cfg = load_config(root)
    product = cfg.product_for_package(info.package)
    run.check("product/edition mapping: HCG Repo products.toml and the Store release agree",
              bool(product) and (product.store_slug, product.store_edition) == (rec.get("productId"), rec.get("editionId")),
              f"{info.package} -> {product.key if product else None} -> Store {rec.get('productId')}/{rec.get('editionId')}")
    rep = check_release(cfg, rec, art_id, info)
    run.check("HCG Repo accepts the approved Store artifact (identity, size, SHA-256, access rule)", rep.ok, rep.render() if not rep.ok else "")
    s = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); from hcgrepo.cli import main; sys.exit(main(sys.argv[2:]))",
                        str(ROOT / "tools"), "import-store-release", "--release", str(a.out / "store-release-approved.json"),
                        "--artifact", art_id, "--deb", str(a.deb), "--store-origin", STAGING_STORE], cwd=root, capture_output=True, text=True,
                       env={**os.environ, "HCG_REPO_ROOT": str(root)})  # the staging copy, never this checkout
    run.check("import-store-release records it", s.returncode == 0, (s.stdout + s.stderr).strip().splitlines()[-1] if (s.stdout + s.stderr).strip() else "")
    cfg = load_config(root)
    entry = next((e for e in load_lock(cfg)["packages"] if (e["package"], e["version"], e["architecture"]) == info.key), {})
    run.check("lock entry is protected and pinned to the Store artifact's SHA-256",
              entry.get("status") == "protected" and entry.get("sha256") == rec["artifacts"][0]["sha256"], f"{entry.get('status')} {entry.get('sha256', '')[:16]}")

    draft = ship(run, st, a.deb, _bump(info.version), "2", approve=False)
    codes = check_release(cfg, draft["record"], draft["artifact"]).codes()
    run.check("an unapproved draft release is refused by HCG Repo", "store-release-not-approved" in codes, ", ".join(sorted(codes)))

    # ---- 3. build the staging source and refresh it like Sileo
    site = build(cfg, STAGING_REPO, work / "site").out_dir
    run.check("no .deb file anywhere in the public site", not list(site.rglob("*.deb")))
    with BackgroundServer(site, "/") as srv:
        vr = verify(STAGING_REPO, srv.url, cfg=cfg, evidence=a.out / "repo-evidence")
        run.check("Sileo-style refresh of the staging source: no errors", vr.report.ok, vr.summary())
        rel = (site / "repo" / "Release").read_text()
        run.check("source identity: Origin and Label are HCG Repo", "Origin: HCG Repo\n" in rel and "Label: HCG Repo\n" in rel)
        icon_ok = all((site / "repo" / n).read_bytes() == (ROOT / "branding" / n).read_bytes() for n in ("CydiaIcon.png", "CydiaIcon@2x.png", "CydiaIcon@3x.png"))
        run.check("source icon: the approved square HCG Repo icon at the paths Sileo requests", icon_ok)
        stanzas = debian.parse_stanzas((site / "repo" / "Packages").read_text())
        st_pkg = next((x for x in stanzas if debian.field(x, "Package") == info.package), None)
        run.check("package listing: the package is indexed once, as cydia::commercial",
                  st_pkg is not None and "cydia::commercial" in (debian.field(st_pkg, "Tag") or "") and len(stanzas) == len({debian.field(x, "Package") for x in stanzas}),
                  f"{len(stanzas)} stanza(s)")
        if st_pkg is None:
            return 1
        idx_sha256, idx_sha512 = debian.field(st_pkg, "SHA256"), debian.field(st_pkg, "SHA512")
        filename = debian.field(st_pkg, "Filename")
        s, _, _ = http("GET", srv.url + "repo/" + filename)
        run.check("the indexed Filename is not downloadable from the public site", s != 200, f"HTTP {s} for repo/{filename}")
        s, b, _ = http("GET", srv.url + "repo/payment_endpoint")
        run.check("payment_endpoint is read by Sileo as the staging Store", sileo_payment_endpoint(b) == ENDPOINT, (b or b"").decode()[:80])

    # ---- 4. Sileo against the staging Store
    sileo = Sileo(st["storeOrigin"])
    s, inf = sileo.get_info()
    run.check("provider info: name and description strings (PaymentProvider.fetchInfo)",
              s == 200 and isinstance(inf, dict) and isinstance(inf.get("name"), str) and isinstance(inf.get("description"), str), f"{(inf or {}).get('name')}")
    body = {"version": info.version, "repo": STAGING_REPO + "repo/", "architecture": info.architecture}
    s, d, failed = sileo.post(f"package/{info.package}/authorize_download", body, token=None)
    run.check("signed out: download refused and Sileo told to sign in again", failed and d.get("invalidate") is True, d.get("error", "") if d else "")

    http("POST", st["control"] + "/next-user", {"sub": OWNER})
    loc, err = sileo.authenticate()
    run.check("sign-in through the provider returns sileo://authentication_success with token and payment_secret",
              bool(loc) and "token=" in loc and "payment_secret=" in loc and sileo.token is not None, err or "")
    owner_token = sileo.token
    level = load_package_meta(cfg, info.package).access
    run.notes.append(f"HCG Repo access level of {info.package}: {level!r} "
                     + ("(any signed-in HCG account may download)." if level == "connect" else "(HCG Store ownership required)."))
    s, d, failed = sileo.post(f"package/{info.package}/info")
    s2, d2, failed2 = sileo.post(f"package/{info.package}/authorize_download", body)
    if level == "connect":
        run.check("connect package, signed in without any entitlement: package info says available", not failed and d.get("purchased") == 1, json.dumps(d))
        run.check("connect package, signed in without any entitlement: download allowed", not failed2, (d2 or {}).get("error", ""))
    else:
        run.check("signed in, no entitlement: package info says not purchased", not failed and d.get("purchased") == 0, json.dumps(d))
        run.check("signed in, no entitlement: download refused", failed2, (d2 or {}).get("error", ""))

    product_id, edition, _ = PRODUCTS[info.package]
    central = st["centralIds"][product_id]
    http("POST", st["control"] + "/grant", {"sub": OWNER, "central": central, "edition": edition})
    s, d, failed = sileo.post("user_info")
    run.check("owner: user_info lists the package", not failed and info.package in d.get("items", []), json.dumps(d.get("items")) if d else "")
    s, d, failed = sileo.post(f"package/{info.package}/info")
    run.check("owner: package info says purchased", not failed and d.get("purchased") == 1, json.dumps(d))
    s, d, failed = sileo.post(f"package/{info.package}/authorize_download", body)
    url = (d or {}).get("url") if not failed else None
    run.check("owner: authorize_download returns a short-lived URL", bool(url), (d or {}).get("error", "") if failed else "")
    if url:
        s, data, _ = http("GET", url)
        got256, got512 = hashlib.sha256(data).hexdigest(), hashlib.sha512(data).hexdigest()
        run.check("owner: the delivered file matches the index SHA256 and SHA512 (Sileo's DownloadManager check)",
                  s == 200 and got256 == idx_sha256 and got512 == idx_sha512, f"HTTP {s}, sha256 {got256[:16]}")
        run.check("the delivered file is the lock's and the Store artifact's file",
                  got256 == entry.get("sha256") == rec["artifacts"][0]["sha256"] == info.sha256)

    stranger = Sileo(st["storeOrigin"])
    http("POST", st["control"] + "/next-user", {"sub": STRANGER})
    stranger.authenticate()
    s, d, failed = stranger.post(f"package/{info.package}/authorize_download", body)
    if level == "connect":
        run.check("connect package: another signed-in account is allowed", not failed, (d or {}).get("error", ""))
    else:
        run.check("another signed-in account without ownership: refused", failed, (d or {}).get("error", ""))

    s, d, failed = sileo.post(f"package/{info.package}/authorize_download", body)
    issued = (d or {}).get("url")
    http("POST", st["control"] + "/revoke-all", {"sub": OWNER})
    s1, _, _ = http("GET", issued) if issued else (0, b"", {})
    run.check("revocation applies to a link issued before it", issued is not None and s1 == 403, f"HTTP {s1}")
    s, d, failed = sileo.post(f"package/{info.package}/authorize_download", body)
    run.check("revocation: new download requests refused", failed, (d or {}).get("error", ""))

    http("POST", st["control"] + "/grant", {"sub": OWNER, "central": central, "edition": edition})
    s, _, _ = http("POST", f"{st['storeOrigin']}/api/v1/publishing/releases/{rec['id']}/withdraw", {"reason": "staging rollback test"}, shipped["auth"])
    s2, d, failed = sileo.post(f"package/{info.package}/authorize_download", body)
    run.check("Store withdrawal stops delivery even to the owner", s == 200 and failed, (d or {}).get("error", ""))
    s, b, _ = http("GET", f"{st['storeOrigin']}/api/v1/publishing/releases/{rec['id']}", headers=shipped["auth"])
    codes = check_release(cfg, js(b), art_id).codes()
    run.check("HCG Repo then refuses the withdrawn release and says to withdraw it here", "store-release-withdrawn" in codes)

    sileo.post("sign_out")
    s, d, failed = sileo.post("user_info", token=owner_token)
    run.check("after Sileo sign-out the token no longer works", failed and (d or {}).get("invalidate") is True, (d or {}).get("error", ""))
    return 0


def _bump(version: str) -> str:
    base = version.split("-")[0]
    major, minor, patch = (base.split(".") + ["0", "0"])[:3]
    return f"{major}.{minor}.{int(patch) + 1}"


if __name__ == "__main__":
    sys.exit(main())

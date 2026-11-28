"""Local administration dashboard: `bin/hcgrepo admin` (docs/ADMIN_DASHBOARD.md).

A page plus a small JSON API on 127.0.0.1, over the SAME functions as the CLI: the Release Manager (release.py), the
importer's withdraw, the compatibility registry and release passports. It adds no backend, store or rule of its own,
so everything it shows or changes is what `bin/hcgrepo release …`, `compat`, `passport` and `withdraw` show or change.

It never publishes: publication remains the manual Publish repository workflow. Changes it makes (an approval, a
Candidate preparation, a withdrawal) are local files to review and commit, exactly as with the CLI.

Safety: bound to the loopback interface only; every API call needs the random token printed at start (header
X-HCG-Admin-Token); POST requests must come from the dashboard's own origin and the Host header must be the loopback
address (no DNS rebinding, no cross-site requests). No credentials are read or shown.
"""

from __future__ import annotations

import hmac
import http.server
import json
import secrets
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import compat, release as rm
from .config import RepoConfig, entry_channel, load_lock
from .importer import ImportError_, withdraw
from .passport import all_passports
from .submission import SubmissionError


class AdminError(Exception):
    pass


# ---------------------------------------------------------------------------- data (all from existing functions)


def overview(cfg: RepoConfig, store: Path) -> dict:
    inc = rm.list_incoming(cfg, store)
    reg = compat.registry(cfg)
    lock = load_lock(cfg)["packages"]
    failures = [{"package": p["package"], "version": v["version"], "status": v["status"], **f}
                for p in reg["packages"] for v in p["versions"] for f in v["known_failures"]]
    return {
        "incoming": inc["submissions"],
        "pending_approvals": [s for s in inc["submissions"] if s["state"] == "validated"],
        "ready_to_prepare": [s for s in inc["submissions"] if s["state"] == "approved"],
        "superseded_candidates": inc["superseded_candidates"],
        "published": [{"package": e["package"], "version": e["version"], "architecture": e["architecture"],
                       "channel": entry_channel(e), "status": e.get("status", "published"), "sha256": e["sha256"],
                       "size": e["size"], "imported_at": e.get("imported_at")} for e in lock],
        "compatibility": reg["packages"],
        "known_failures": failures,
        "history": history(cfg, store),
    }


def history(cfg: RepoConfig, store: Path) -> list[dict]:
    ev = []
    for e in load_lock(cfg)["packages"]:
        who = f"{e['package']} {e['version']} {e['architecture']}"
        for key, what in (("imported_at", "imported"), ("protected_at", "recorded as protected"),
                          ("withdrawn_at", "withdrawn")):
            if e.get(key):
                ev.append({"at": e[key], "subject": who, "event": what,
                           "detail": e.get("withdrawn_reason") if key == "withdrawn_at" else entry_channel(e)})
    for d in sorted(p for p in store.iterdir() if (p / "review.json").is_file()):
        r = json.loads((d / "review.json").read_text(encoding="utf-8"))
        pkg = r.get("package") or {}
        for h in r.get("history", []):
            ev.append({"at": h.get("at"), "subject": f"{pkg.get('identifier')} {pkg.get('version')} (submission)",
                       "event": h.get("event"), "detail": h.get("by") or h.get("state") or h.get("route") or h.get("reason")})
    return sorted(ev, key=lambda x: x["at"] or "", reverse=True)


def submission(cfg: RepoConfig, store: Path, sid: str) -> dict:
    m, review, _ = rm.load_review(store, sid)
    return {"result": rm.submission_result(cfg, m, review), "manifest": m, "text": rm.render_review(m, review)}


def act(cfg: RepoConfig, store: Path, action: str, body: dict) -> dict:
    """The only state-changing operations, each one existing CLI function."""
    def need(*keys):
        missing = [k for k in keys if not str(body.get(k, "")).strip()]
        if missing:
            raise AdminError(f"missing {', '.join(missing)}")
    if action == "approve":
        need("id", "sha256", "by", "record")
        r = rm.approve(store, body["id"], body["sha256"], body["by"], body["record"])
        return {"ok": True, "message": f"Approved {r['approval']['sha256']} for a Candidate import. Not published."}
    if action == "prepare":
        need("id")
        status, rep, _ = rm.prepare(cfg, store, body["id"], free_demo_approval=body.get("free_demo_approval") or None,
                                    create_package_metadata=bool(body.get("create_package_metadata")),
                                    via=body.get("via") or "pool")
        return {"ok": status in ("imported", "already-present", "staged"), "message": f"{status}: review and commit "
                "the change on a branch; publishing is the separate Publish workflow", "report": rep.render()}
    if action == "withdraw-submission":
        need("id", "reason")
        rm.withdraw_submission(store, body["id"], body["reason"])
        return {"ok": True, "message": "submission withdrawn"}
    if action == "withdraw":
        need("package", "version", "architecture", "reason")
        if body.get("confirm") != f"{body['package']} {body['version']}":
            raise AdminError("type the package and version exactly to confirm a withdrawal")
        f = withdraw(cfg, body["package"], body["version"], body["architecture"], body["reason"])
        return {"ok": True, "message": f"{f} withdrawn in repo-lock.json (bytes never reused). Commit, review, and "
                                       "publish with the owner's approval to remove it from the live index."}
    raise AdminError(f"unknown action {action!r}")


# ---------------------------------------------------------------------------- HTTP


def store_integration(cfg: RepoConfig, store_checkout: Path | None) -> dict:
    """The HCG Store Integration section: the same data as `bin/hcgrepo store status` (storeint.status), fetched live."""
    from . import storeint
    return storeint.status(cfg, live=True, store_checkout=store_checkout)


def make_handler(cfg: RepoConfig, store: Path, token: str, port_ref: dict, store_checkout: Path | None = None):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "hcgrepo-admin"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                                        "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, indent=1).encode(), "application/json")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in (f"127.0.0.1:{port_ref['port']}", f"localhost:{port_ref['port']}")

        def _token_ok(self) -> bool:
            return hmac.compare_digest(self.headers.get("X-HCG-Admin-Token", ""), token)

        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, b"forbidden host", "text/plain")
            u = urlsplit(self.path)
            if u.path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if not u.path.startswith("/api/") or not self._token_ok():
                return self._send(403, b"token required", "text/plain")
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/api/overview":
                    return self._json(200, overview(cfg, store))
                if u.path == "/api/submission":
                    return self._json(200, submission(cfg, store, q.get("id", "")))
                if u.path == "/api/store-integration":
                    return self._json(200, store_integration(cfg, store_checkout))
                if u.path == "/api/passport":
                    return self._json(200, all_passports(cfg, q.get("package") or None, q.get("version") or None))
            except (rm.ReleaseError, SubmissionError, ImportError_, AdminError) as exc:
                return self._json(400, {"ok": False, "message": str(exc)})
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            origin = self.headers.get("Origin", "")
            if not self._host_ok() or not self._token_ok() or origin not in (
                    f"http://127.0.0.1:{port_ref['port']}", f"http://localhost:{port_ref['port']}"):
                return self._send(403, b"forbidden", "text/plain")
            u = urlsplit(self.path)
            if not u.path.startswith("/api/act/"):
                return self._send(404, b"not found", "text/plain")
            try:
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
                return self._json(200, act(cfg, store, u.path.rsplit("/", 1)[-1], body))
            except (rm.ReleaseError, SubmissionError, ImportError_, AdminError, ValueError) as exc:
                return self._json(400, {"ok": False, "message": str(exc)})

    return Handler


def serve(cfg: RepoConfig, store: Path, port: int = 8765, token: str | None = None, store_checkout: Path | None = None):
    """Start the dashboard on 127.0.0.1. Returns (server, url, thread)."""
    token = token or secrets.token_urlsafe(24)
    port_ref: dict = {}
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, store, token, port_ref, store_checkout))
    port_ref["port"] = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{port_ref['port']}/#token={token}", t


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HCG Repo Admin</title>
<style>
:root{--bg:#05080b;--panel:#0d141b;--line:#1d2a36;--text:#e6eef5;--muted:#8aa0b3;--accent:#2fd3e6;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header h1{font-size:17px;margin:0}header .note{color:var(--muted);font-size:12px}
nav{display:flex;gap:4px;flex-wrap:wrap;padding:8px 20px;border-bottom:1px solid var(--line)}
nav button{background:none;border:1px solid var(--line);color:var(--muted);padding:6px 10px;border-radius:6px;cursor:pointer}
nav button.on{color:var(--text);border-color:var(--accent)}main{padding:16px 20px;max-width:1300px}
table{width:100%;border-collapse:collapse;margin:8px 0 18px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px}code,.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px;word-break:break-all}
.pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:12px;border:1px solid var(--line)}
.passed,.validated,.approved,.prepared,.published,.ok,.recorded,.yes{color:var(--ok)}.new,.no,.unavailable,.warning{color:var(--warn)}.acknowledged{color:var(--muted)}.conflict,.error{color:var(--bad)}.failed,.refused,.withdrawn{color:var(--bad)}
.partial,.passed-with-warnings,.protected{color:var(--warn)}.not-recorded,.not-run,.not-applicable{color:var(--muted)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}
pre{white-space:pre-wrap;background:var(--panel);border:1px solid var(--line);padding:10px;border-radius:6px;font-size:12px}
input,textarea,select{background:#081016;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px;width:100%;font:inherit}
label{display:block;margin:6px 0 2px;color:var(--muted);font-size:12px}.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
button.act{background:var(--accent);color:#001318;border:0;border-radius:6px;padding:7px 12px;font-weight:600;cursor:pointer;margin-top:8px}
button.danger{background:var(--bad)}.msg{margin:8px 0;padding:8px;border-radius:6px;background:var(--panel)}
.muted{color:var(--muted)}a{color:var(--accent)}
</style></head><body>
<header><h1>HCG Repo · Administration</h1>
<span class="note">Local only. Uses the same engine as <code>bin/hcgrepo</code>. Nothing here publishes: publication is the manual Publish repository workflow.</span></header>
<nav id="tabs"></nav><main id="main"><p class="muted">Loading…</p></main>
<script>
const TOKEN = new URLSearchParams(location.hash.slice(1)).get('token') || '';
const TABS = [['incoming','Incoming'],['approvals','Pending approvals'],['prepare','Candidate preparation'],['published','Published packages'],
  ['compat','Compatibility evidence'],['failures','Known failures'],['history','Release history'],['provenance','Artifact provenance'],['withdraw','Withdrawals'],['store','HCG Store Integration']];
const LEVELS = [['static_inspection','Static package inspection'],['maintainer_script_review','Maintainer script review (read, not run)'],
  ['script_free_lifecycle','Script-free dpkg lifecycle'],['device_installation','Device installation'],['application_launch','Application launch'],['runtime_verification','Full runtime verification']];
let DATA = null, TAB = 'incoming';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pill = s => `<span class="pill ${esc(s)}">${esc(s)}</span>`;
async function api(path, body) {
  const r = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json','X-HCG-Admin-Token':TOKEN}, body:JSON.stringify(body)}
                                   : {headers:{'X-HCG-Admin-Token':TOKEN}});
  const t = await r.text(); try { return JSON.parse(t); } catch { return {ok:false, message:t}; }
}
function table(rows, cols) {
  if (!rows.length) return '<p class="muted">None.</p>';
  return '<table><tr>' + cols.map(c => `<th>${esc(c[1])}</th>`).join('') + '</tr>' +
    rows.map(r => '<tr>' + cols.map(c => `<td>${c[2] ? c[2](r) : esc(r[c[0]])}</td>`).join('') + '</tr>').join('') + '</table>';
}
function levels(L) { return '<table>' + LEVELS.map(([k,n]) => `<tr><td>${esc(n)}</td><td>${pill((L[k]||{}).state||'not-recorded')}</td><td class="muted">${esc((L[k]||{}).note||'')}</td></tr>`).join('') + '</table>'; }
const subCols = [['package','Package'],['version','Version'],['state','State',r=>pill(r.state)],['provider','Provider'],
  ['sha256','SHA-256',r=>`<code>${esc(r.sha256)}</code>`],['errors','Errors / warnings',r=>`${r.errors} / ${r.warnings}`],['id','',r=>`<a href="#" onclick="openSub('${esc(r.id)}');return false">Review</a>`]];
async function openSub(id) {
  const d = await api('/api/submission?id=' + encodeURIComponent(id)); const r = d.result || {};
  let h = `<div class="card"><h3>${esc(id)}</h3>${levels(r.levels||{})}<pre>${esc(d.text||d.message)}</pre>`;
  h += '<h4>Requirements</h4>' + table(r.requirements||[], [['kind','Requirement'],['satisfied','Satisfied',x=>x.satisfied?pill('passed'):pill('not-recorded')],['detail','Detail',x=>esc(x.detail||x.command||'')]]);
  if (r.state === 'validated') h += `<h4>Approve exactly these bytes</h4><div class="row"><div><label>SHA-256 (type it from the review above)</label><input id="a-sha"></div><div><label>Approved by</label><input id="a-by"></div></div>
    <label>Approval, verbatim</label><textarea id="a-rec" rows="2"></textarea><button class="act" onclick="doAct('approve',{id:'${esc(id)}',sha256:v('a-sha'),by:v('a-by'),record:v('a-rec')})">Record approval</button>`;
  if (r.state === 'approved') h += `<h4>Prepare the Candidate import</h4><label>Route</label><select id="p-via"><option value="pool">pool: import here (default)</option><option value="local-file">local-file: stage for Prepare packages in CI</option></select>
    <label>Free-demo approval for this version (only for a sign-in or paid package), verbatim</label><textarea id="p-demo" rows="2"></textarea>
    <label><input type="checkbox" id="p-meta" style="width:auto"> Create packages/&lt;id&gt;/ from the submission's draft (only if it does not exist)</label>
    <button class="act" onclick="doAct('prepare',{id:'${esc(id)}',via:v('p-via'),free_demo_approval:v('p-demo'),create_package_metadata:document.getElementById('p-meta').checked})">Prepare (does not publish)</button>`;
  if (['validated','approved','refused'].includes(r.state)) h += `<h4>Withdraw this submission</h4><input id="w-r" placeholder="reason"><button class="act danger" onclick="doAct('withdraw-submission',{id:'${esc(id)}',reason:v('w-r')})">Withdraw submission</button>`;
  document.getElementById('detail').innerHTML = h + '</div>';
}
const v = id => document.getElementById(id).value;
async function doAct(action, body) { const r = await api('/api/act/' + action, body); alert((r.ok ? 'OK: ' : 'Refused: ') + r.message + (r.report ? '\n' + r.report : '')); await load(); }
async function showPassport(pkg, ver) { const d = await api(`/api/passport?package=${encodeURIComponent(pkg)}&version=${encodeURIComponent(ver)}`);
  document.getElementById('detail').innerHTML = `<div class="card"><h3>Release passport: ${esc(pkg)} ${esc(ver)}</h3>${levels((d[0]||{}).validation?.levels||{})}<pre>${esc(JSON.stringify(d[0]||d, null, 1))}</pre></div>`; }
function render() {
  const D = DATA, m = document.getElementById('main');
  document.getElementById('tabs').innerHTML = TABS.map(([k,n]) => `<button class="${k===TAB?'on':''}" onclick="TAB='${k}';render()">${n}${k==='approvals'&&D.pending_approvals.length?' ('+D.pending_approvals.length+')':''}</button>`).join('');
  let h = '';
  if (TAB === 'incoming') h = '<h2>Incoming submissions</h2><p class="muted">Private incoming store. Nothing here is in Git until approved and prepared.</p>' + table(D.incoming, subCols);
  if (TAB === 'approvals') h = '<h2>Pending checksum approvals</h2>' + table(D.pending_approvals, subCols);
  if (TAB === 'prepare') h = '<h2>Approved, ready for Candidate preparation</h2>' + table(D.ready_to_prepare, subCols) + '<h3>Superseded Candidate versions</h3>' + table(D.superseded_candidates, [['package','Package'],['version','Version'],['superseded_by','Superseded by']]);
  if (TAB === 'published') h = '<h2>Recorded packages</h2>' + table(D.published, [['package','Package'],['version','Version'],['channel','Channel'],['status','Status',r=>pill(r.status)],['sha256','SHA-256',r=>`<code>${esc(r.sha256)}</code>`],['size','Size'],['imported_at','Imported']]);
  if (TAB === 'compat') h = '<h2>Compatibility evidence</h2>' + D.compatibility.map(p => `<div class="card"><h3>${esc(p.package)}</h3><p class="muted">Last physically verified: ${esc(p.last_verified_version||'none')} · last launched: ${esc(p.last_launched_version||'none')}</p>` +
     table(p.versions, [['version','Version'],['channel','Channel'],['status','Status'],['runtime_compatibility','Overall runtime',r=>pill(r.runtime_compatibility==='failed'?'failed':r.runtime_compatibility)],...LEVELS.map(([k,n]) => [k, n, r => pill(r.levels[k].state)])]) + '</div>').join('');
  if (TAB === 'failures') h = '<h2>Known failures</h2>' + table(D.known_failures, [['package','Package'],['version','Version'],['stage','Stage'],['result','Result',r=>pill(r.result==='fail'?'failed':r.result)],['device','Device'],['ios','iOS'],['jailbreak','Jailbreak'],['date','Date'],['evidence','Evidence']]);
  if (TAB === 'history') h = '<h2>Release history</h2>' + table(D.history, [['at','When'],['subject','Release'],['event','Event'],['detail','Detail']]);
  if (TAB === 'provenance') h = '<h2>Artifact provenance</h2><p class="muted">Release passports (hcg.release-passport v1), derived from the lock, provenance and reviews.</p>' + table(D.published, [['package','Package'],['version','Version'],['channel','Channel'],['sha256','SHA-256',r=>`<code>${esc(r.sha256)}</code>`],['x','',r=>`<a href="#" onclick="showPassport('${esc(r.package)}','${esc(r.version)}');return false">Passport</a>`]]);
  if (TAB === 'withdraw') h = `<h2>Withdrawal</h2><p class="muted">Withdrawing keeps the lock record (the bytes are never reused) and removes the version from the index on the next approved publication.</p>
     <div class="card"><div class="row"><div><label>Package</label><input id="wd-p"></div><div><label>Version</label><input id="wd-v"></div></div><label>Architecture</label><input id="wd-a" value="iphoneos-arm64">
     <label>Reason</label><input id="wd-r"><label>Type "package version" exactly to confirm</label><input id="wd-c">
     <button class="act danger" onclick="doAct('withdraw',{package:v('wd-p'),version:v('wd-v'),architecture:v('wd-a'),reason:v('wd-r'),confirm:v('wd-c')})">Withdraw version</button></div>`;
  if (TAB === 'store') { m.innerHTML = '<h2>HCG Store Integration</h2><p class="muted">Checking HCG Store live…</p>'; loadStore(); return; }
  m.innerHTML = h + '<div id="detail"></div>';
}
async function loadStore() {
  const S = await api('/api/store-integration'); const m = document.getElementById('main');
  if (!S.provider) { m.innerHTML = '<h2>HCG Store Integration</h2><p>' + esc(S.message || 'unavailable') + '</p>'; return; }
  const pv = S.provider, api_ = pv.api || {}, H = S.store_health, C = S.counts, yn = b => pill(b ? 'yes' : 'no');
  let h = '<h2>HCG Store Integration</h2><p class="muted">Live from HCG Store\'s public endpoints and this repository\'s records (same data as <code>bin/hcgrepo store status</code>). Read-only: nothing here imports, publishes or changes a setting.</p>';
  h += '<div class="card"><h3>Payment provider</h3><table>' +
    `<tr><td>Configured URL</td><td><code>${esc(pv.url)}</code></td></tr><tr><td>Advertised (payment_endpoint)</td><td>${yn(pv.advertise)} ${esc(pv.advertised_url)}</td></tr>` +
    `<tr><td>Trusted hosts</td><td>${esc(pv.trusted_hosts.join(', '))}</td></tr><tr><td>Owner approval</td><td>${pv.approval ? esc(pv.approval.approved_by + ' · ' + pv.approval.date + ' · ' + pv.approval.record) : '<span class="muted">none</span>'}</td></tr>` +
    `<tr><td>Provider API (<code>${esc(api_.endpoint)}</code>)</td><td>${pill(api_.ok ? 'ok' : 'unavailable')} HTTP ${esc(api_.http_status)} ${esc(api_.ok ? api_.name : api_.error)}</td></tr></table></div>`;
  h += '<div class="card"><h3>HCG Store health and entitlements</h3><table>' +
    [['Endpoint', H.endpoint], ['Status', H.ok ? 'ok' : 'HTTP ' + H.http_status + ' ' + (H.error || '')], ['Mode', H.mode], ['HCG Connect', H.hcg_connect], ['Entitlement authority', H.entitlement_authority],
     ['Payments', H.payments], ['Protected downloads', H.protected_downloads], ['Publishing', H.publishing]].map(r => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td></tr>`).join('') +
    `<tr><td>Entitlement service</td><td>${pill(H.entitlements_available ? 'ok' : 'unavailable')} <span class="muted">as reported by HCG Store; HCG Store re-checks it on every protected request</span></td></tr></table></div>`;
  h += `<div class="card"><h3>Packages</h3><p>Free: <b>${C.free_packages}</b> packages (${C.free_versions} versions) · Paid: <b>${C.paid_packages}</b> (${C.paid_versions}) · Account-required: <b>${C.account_packages}</b> (${C.account_versions})</p></div>`;
  h += '<h3>Published package associations</h3>' + table(S.associations.filter(a => a.key !== 'test-fixtures'), [['key','HCG Repo product'],['store_product','HCG Store product',a=>esc((a.store_product||'—') + (a.store_edition ? ' / ' + a.store_edition : ''))],
    ['packages','Packages',a=>a.packages.map(x=>`<code>${esc(x)}</code>`).join('<br>')],['access','Access'],['commercial','Commercial',a=>a.commercial ? esc('approved version ' + (a.commercial.approved_version || 'none')) + ' ' + pill(a.commercial.installable ? 'yes' : 'no') : '<span class="muted">—</span>'],
    ['builds','Builds (indexed / Store release)',a=>a.builds.map(b=>`${esc(b.version)} ${pill(b.indexed ? 'yes' : 'no')} ${esc(b.status)}${b.store_release ? ' <code>' + esc(b.store_release) + '</code>' : ''}`).join('<br>')]]);
  h += `<h3>Unmapped products</h3><p class="muted">Source: ${esc(S.catalogue_source)}</p>` + table(S.unmapped, [['side','Side'],['x','Product',u=>esc(u.store_product || u.key)],['reason','Reason']]);
  h += '<h3>Invalid package metadata</h3>' + table(S.invalid_metadata, [['level','Level',r=>pill(r.level)],['code','Code'],['message','Message']]);
  h += `<h3>HCG Store → HCG Repo publication</h3><p class="muted">HCG Store published feed: ${esc(S.published_feed)}. Each row is what a maintainer does next with the reviewed commands; nothing is imported automatically.</p>` +
    table(S.publication_plan, [['state','State',r=>pill(r.state)],['product','Product'],['version','Version'],['store_release','Store release'],['next','Next step']]);
  h += '<h3>Integration and publication history</h3>' + table(S.history, [['at','When'],['kind','Kind'],['subject','Event'],['ref','Reference']]);
  m.innerHTML = h;
}
async function load() { DATA = await api('/api/overview'); if (!DATA.incoming) { document.getElementById('main').innerHTML = '<p>Open the URL printed by <code>bin/hcgrepo admin</code> (it carries the session token).</p>'; return; } render(); }
load();
</script></body></html>
"""

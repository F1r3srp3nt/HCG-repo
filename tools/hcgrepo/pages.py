"""HCG Repo website pages: My Library (account profile), Package Trust Center, and package-manager installation guides.

Display only. The Profile reads the signed-in account from HCG Store's read-only `GET /api/v1/repo/profile`
(credentialed CORS for HCG Repo origins); HCG Store and HCG's central entitlements stay the only authority, and nothing
on these pages grants access. Client facts on the install page come from upstream source (docs/PACKAGE_MANAGERS.md).
"""

from __future__ import annotations

from html import escape as _esc

SOURCE_DOCS = "https://github.com/F1r3srp3nt/HCG-repo/blob/main/docs/"


def e(s) -> str:
    return _esc(str(s), quote=True)


def account_body(store_base: str, repo_url: str, candidate_url: str) -> str:
    store = store_base.rstrip("/") + "/"
    return f"""<section class="section prose account" aria-labelledby="acct-h" data-profile
  data-store-base="{e(store)}" data-profile-url="{e(store)}api/v1/repo/profile" data-repo-url="{e(repo_url)}">
<p class="crumbs"><a href="../">HCG Repo</a> <span aria-hidden="true">›</span> My Library</p>
<div class="dep-title card">
  <div>
    <h1 id="acct-h">My Library</h1>
    <p class="lead">One HCG account, one library, every supported package manager.</p>
  </div>
</div>
<div class="card note" data-profile-state role="status">
  <p>Checking your HCG account…</p>
  <noscript><p>Your library is shown with JavaScript. You can also open it in <a href="{e(store)}account">HCG Store</a>.</p></noscript>
</div>
<div data-profile-signed-in hidden>
  <section class="section-tight card" aria-labelledby="who-h">
    <h2 id="who-h">Account</h2>
    <dl class="kv"><div><dt>Signed in as</dt><dd data-profile-name></dd></div><div><dt>Email</dt><dd data-profile-email></dd></div>
    <div><dt>Identity</dt><dd>HCG Connect, through HCG Store. HCG Repo never asks for your password.</dd></div></dl>
    <div class="actions"><a class="btn btn-metal" href="{e(store)}account">Manage account in HCG Store</a>
    <a class="btn btn-metal" href="{e(store)}account">Sign out (HCG Store)</a></div>
  </section>
  <section class="section-tight" aria-labelledby="lib-h">
    <h2 id="lib-h">Packages in your library</h2>
    <p class="hint" data-profile-downloads></p>
    <ul class="cards" role="list" data-profile-library></ul>
    <p class="muted" data-profile-library-empty hidden>No HCG Repo packages are in your library yet.
    Free public packages need no account: they install from the HCG Repo source directly.</p>
  </section>
  <section class="section-tight card" aria-labelledby="upd-h">
    <h2 id="upd-h">Available updates</h2>
    <p>Your package manager compares what is installed on your device with the HCG Repo index. HCG Store and HCG Repo
    do not know which version is installed, so they never claim that an update is or is not installed. Each package
    above shows the version HCG Repo currently offers.</p>
  </section>
  <section class="section-tight" aria-labelledby="buy-h">
    <h2 id="buy-h">Purchase history</h2>
    <div data-profile-purchases></div>
  </section>
  <section class="section-tight card" aria-labelledby="pm-h">
    <h2 id="pm-h">Connected package managers</h2>
    <ul data-profile-devices></ul>
    <p class="hint" data-profile-devices-note></p>
  </section>
  <section class="section-tight card" aria-labelledby="restore-h">
    <h2 id="restore-h">Restore purchases on a device</h2>
    <p>Add the HCG Repo source in Sileo, Zebra or Saily, then sign in with the same HCG account when the package manager
    asks (it opens HCG Connect in a secure browser sheet). Your packages are restored from your account; nothing is
    stored on the device except a revocable HCG Store token. <a href="../install/">Installation guides</a>.</p>
  </section>
</div>
<section class="section-tight card" aria-labelledby="beta-h">
  <h2 id="beta-h">Betas and testing channel</h2>
  <p>Free betas are in the separate HCG Repo Candidate source: <code>{e(candidate_url)}</code>. Candidate builds never
  appear in the stable source, and paid builds are never in Candidate.</p>
</section>
<section class="section-tight card" aria-labelledby="trust-h">
  <h2 id="trust-h">Package trust</h2>
  <p>Repository signing status, fingerprints and how to verify a downloaded package: <a href="../trust/">Package Trust Center</a>.</p>
</section>
</section>"""


def trust_body(signed: bool, fingerprint: str, repo_url: str, candidate_url: str, built: str) -> str:
    if signed and fingerprint:
        status = (f'<p><span class="badge badge-ok">Signed</span> Release files of both sources are signed with the HCG Repo '
                  f'archive key:</p><p><code class="fpr">{e(fingerprint)}</code></p>'
                  '<p>Compare this fingerprint with at least one other place you trust before relying on it.</p>')
    else:
        status = ('<p><span class="badge badge-muted">Not signed yet</span> HCG Repo\'s repository metadata is <strong>not '
                  'signed</strong> today. Package checksums in the index can be compared, but the index itself is not '
                  'cryptographically authenticated: it relies on HTTPS. No fingerprint is published until the founder creates '
                  'the production key.</p>')
    return f"""<section class="section prose" aria-labelledby="trust-h">
<p class="crumbs"><a href="../">HCG Repo</a> <span aria-hidden="true">›</span> Package Trust Center</p>
<div class="dep-title card"><div><h1 id="trust-h">Package Trust Center</h1>
<p class="lead">What protects a package on its way from HCG to your device, and what does not.</p></div></div>
<section class="section-tight card" aria-labelledby="sig-h"><h2 id="sig-h">Repository signing</h2>{status}
<p class="hint">Built {e(built)}. Sources: <code>{e(repo_url)}</code> and <code>{e(candidate_url)}</code>.</p></section>
<section class="section-tight card" aria-labelledby="three-h"><h2 id="three-h">Three separate checks</h2>
<dl class="kv">
<div><dt>Repository signing</dt><dd>An OpenPGP signature over <code>Release</code>, which lists the checksums of the package index.
Checked by Sileo (after you install the HCG Repo Keyring) and by APT. Zebra and Saily do not check repository signatures.</dd></div>
<div><dt>Package verification</dt><dd>Each package's SHA-256 is in the index. Sileo, Saily and APT compare the downloaded
file with it; we found no such check in Zebra's source, so Zebra relies on HTTPS.</dd></div>
<div><dt>Entitlement authorization</dt><dd>For paid packages, HCG Store checks your HCG account's entitlement on the server
before issuing a short-lived link to that exact file. A signature or a checksum never grants access.</dd></div>
<div><dt>Code signing</dt><dd>The app or tweak inside the package carries its own Mach-O signature (ldid or Apple). It is a
different thing from repository signing.</dd></div>
</dl></section>
<section class="section-tight card" aria-labelledby="ver-h"><h2 id="ver-h">Verify a downloaded package yourself</h2>
<p>With a checkout of the HCG Repo tools: <code>bin/hcgrepo verify-artifact &lt;file.deb&gt;</code>. It reports
<em>verified</em> only against a signed index, <em>matches-unauthenticated-index</em> against an unsigned one, and it
covers only the file you checked, never a different file a package manager installs later.</p></section>
<section class="section-tight card" aria-labelledby="doc-h"><h2 id="doc-h">Details</h2><ul>
<li><a href="{SOURCE_DOCS}SIGNING.md">Repository signing and client behaviour</a></li>
<li><a href="{SOURCE_DOCS}KEYRING.md">HCG Repo Keyring and fingerprint verification</a></li>
<li><a href="{SOURCE_DOCS}NEXUS_TRUST_CENTER.md">Verification contracts (HCG Nexus)</a></li>
<li><a href="{SOURCE_DOCS}PACKAGE_MANAGERS.md">Package manager compatibility matrix</a></li></ul></section>
</section>"""


PACKAGE_MANAGERS = [
    # name, status, environments, notes (facts from upstream source, docs/PACKAGE_MANAGERS.md)
    ("Sileo", "Recommended", "Rootless and rootful",
     "Free and paid HCG packages. Paid packages: sign in with your HCG account when Sileo asks; Sileo verifies "
     "repository signatures once the HCG Repo Keyring is installed."),
    ("Zebra", "Supported", "Rootless and rootful",
     "Free and paid HCG packages (Zebra uses the same payment-provider protocol on HTTPS sources). Zebra does not "
     "verify repository signatures."),
    ("Saily (Irisin)", "Supported", "Rootless and roothide, iOS 15+",
     "Free packages; paid packages through the same payment-provider protocol. No repository signature verification; "
     "package checksums are checked."),
    ("APT (command line)", "Free packages only", "Rootless and rootful",
     "Verifies repository signatures and checksums. APT has no payment protocol, so paid packages install through a "
     "package manager above."),
    ("Cydia", "Legacy, not supported for paid packages", "Rootful",
     "Cydia's paid packages use saurik's own legacy system, which HCG does not use. Free packages may work; not tested."),
    ("Installer 5", "Free packages only, not tested", "Rootful",
     "Closed source. The last public source snapshot skips paid (commercial) packages entirely."),
]


def install_body(repo_url: str, candidate_url: str) -> str:
    cards = "".join(f"""<li class="card"><h3>{e(n)}</h3><p class="pkg-meta"><span class="badge">{e(st)}</span> {e(env)}</p>
<p>{e(note)}</p></li>""" for n, st, env, note in PACKAGE_MANAGERS)
    return f"""<section class="section prose" aria-labelledby="inst-h">
<p class="crumbs"><a href="../">HCG Repo</a> <span aria-hidden="true">›</span> Install</p>
<div class="dep-title card"><div><h1 id="inst-h">Install HCG packages</h1>
<p class="lead">Choose your package manager. One HCG account works in all of those that support paid packages.</p></div></div>
<section class="section-tight card" aria-labelledby="src-h"><h2 id="src-h">Add the source</h2>
<dl class="kv"><div><dt>Stable</dt><dd><code>{e(repo_url)}</code></dd></div>
<div><dt>Testing (betas)</dt><dd><code>{e(candidate_url)}</code></dd></div></dl>
<p>Add the stable source for released packages and, if you want betas, also the testing source.</p></section>
<section class="section-tight" aria-labelledby="pm-h"><h2 id="pm-h">Package managers</h2>
<ul class="cards" role="list">{cards}</ul></section>
<section class="section-tight card" aria-labelledby="paid-h"><h2 id="paid-h">Paid packages</h2>
<ol><li>Buy in <strong>HCG Store</strong> (HCG Repo never takes payments).</li>
<li>In Sileo, Zebra or Saily, open the package and sign in with your HCG account when asked.</li>
<li>Install. The package manager receives a short-lived link to the exact file after HCG Store has checked your
entitlement on its server.</li></ol>
<p class="hint">Paid packages are not available yet: HCG Store's protected delivery is not switched on.</p></section>
</section>"""

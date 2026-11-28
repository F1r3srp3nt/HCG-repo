# Physical Sileo qualification: HCG Store as HCG Repo's payment provider

**Status (28 Nov 2026): prepared, not deployed.** It needs the owner's deployment approval and the HCG Store items
below. The public sources (`/repo/`, `/repo/candidate/`) stay byte-identical throughout. Nothing is advertised there,
and no paid package is listed there.

## The qualification source

| | |
|---|---|
| **Sileo source URL** | **`https://repo.hakmicapitalgroup.com/qualification/54906bcc56/repo/`** (live only after deployment) |
| Identity | Origin and Label `HCG Repo Qualification`, Suite `qualification`: its own source, separate from HCG Repo |
| Payment provider | `payment_endpoint` → `https://store.hakmicapitalgroup.com/api/sileo/v1/` (this source only) |
| Packages | `com.hakmicapitalgroup.qualification.free` 1.0.0: free, served by this source. `com.hakmicapitalgroup.qualification.paid` 1.0.0: TEST commercial package, held and delivered only by HCG Store |
| Configuration | `qualification/config/repo.toml` and `products.toml`, `qualification/packages/`, `qualification/repo-lock.json` |
| Architecture | `iphoneos-arm64` (rootless, e.g. Dopamine), iOS 15.0 or later |

**Private means unlisted, not access-controlled.** GitHub Pages cannot restrict who reads a public repository's
site, and the path is visible in this public Git repository. The path is not linked from anywhere, and its pages are
`noindex`.

The paid package is protected regardless of who adds the source: HCG Store delivers it only after its own
entitlement check. HCG Store should restrict the sandbox offer to the tester's HCG account (handoff item 5).

**Package files** (reproducible: `tools/qualification/build_packages.py <dir>` yields the same bytes anywhere):

| File | SHA-256 | Size |
|---|---|---|
| `com.hakmicapitalgroup.qualification.free_1.0.0_iphoneos-arm64.deb` | `1e73f104879fef7847e3184912da88434beaed7b64177c8d6207d9a313a29b78` | 988 |
| `com.hakmicapitalgroup.qualification.paid_1.0.0_iphoneos-arm64.deb` | `9c5820dc1cc3cdbca88cb312b3d772dc2e9ffd0476d4f4e2a5b20a358c71803b` | 1,082 |

Each package installs a text file under `/var/jb/usr/share/hcg-qualification/` (`free.txt` or `paid.txt`) and a
script `/var/jb/usr/bin/hcg-qualification-free` or `-paid` that prints it. There are no maintainer scripts, no
binaries and no respring.

## Required configuration before deployment

**HCG Store** ([store-integration/QUALIFICATION_HANDOFF.md](store-integration/QUALIFICATION_HANDOFF.md)):
1. Serve the provider at `/api/sileo/v1/` (patch 0002) with the official profile: name, icon, description and
   sign-in banner.
2. Add the catalogue product `hcg-repo-qualification`, edition `jailbreak`, with Debian identifier
   `com.hakmicapitalgroup.qualification.paid`.
3. Ship to Store release 1.0.0 with exactly the paid file above, approved by a second person for
   `internal-testing`, with destination `hcg-repo`.
4. A sandbox-only offer through HCG Payments (Stripe test mode).
5. Restrict that offer to the tester's HCG account.
6. Allow protected delivery for this product.

**HCG Repo** (done here once HCG Store has finished):
1. Record the Store's release: save `GET /api/v1/publishing/releases/<id>` as JSON, then run
   `HCG_REPO_ROOT=qualification bin/hcgrepo import-store-release --release <record.json> --artifact <art-id> --deb <paid.deb>`.
   The qualification source records only `internal-testing` releases.
2. Owner's deployment approval → set `[qualification] active = true` in `qualification/config/repo.toml` and merge.
3. Run the **Qualification source** workflow from `main` with `QUALIFY`. It refuses to deploy unless all of these hold:
   - the rebuilt public site is byte-identical to live (all files checked);
   - the qualification source verifies the way Sileo reads it;
   - `https://store.hakmicapitalgroup.com/api/sileo/v1/info` answers as *HCG Store*.

   After deploying it re-checks that the public `Packages` is unchanged and that no public `payment_endpoint` exists.

You can deploy in two steps: first without the paid release (checks 1–5 below), then again after it is recorded
(checks 6–8).

## Physical test (Sileo, iPhone 11 Pro Max and iPhone 13 Pro)

UI labels are Sileo's own (`Base.lproj/Localizable.strings` at `c9f7015`: *Payment Providers*, *Purchases*, *Signed in*); a newer Sileo may word them differently.

**Record first:** device, iOS version, jailbreak and version (rootless), Sileo version (Settings → About), date and
time. Take a screenshot or screen recording of each step. Use your own HCG account (A). For check 8, use a second HCG
account without a purchase (B).

| # | Check | Steps | Pass when |
|---|---|---|---|
| 1 | Source and provider discovery | Sileo → **Sources** → **+** → paste the source URL → Add. Refresh | Source *HCG Repo Qualification* with the HCG icon. Refresh without errors |
| 2 | **HCG Store under Payment Providers** | Sileo → **Settings** tab → section **Payment Providers** | **HCG Store** is listed there with its icon |
| 3 | **Official branding** | Look at the HCG Store entry and open it | Official HCG Store name and icon, description *Access to HCG Repo packages with your HCG account.* (or the Store's final profile text), and the sign-in banner |
| 4 | **HCG Connect sign-in** | Tap HCG Store's sign-in (the provider cell or the source's sign-in banner) | A secure browser sheet opens on `store.hakmicapitalgroup.com` and goes to HCG Connect. Sign in with account A. Back in Sileo the HCG Store cell says *Signed in* (with your name). Sileo itself never asks for your password |
| 5 | **Free package installs normally** | Search *HCG Qualification (Free)* → Get → Confirm | Installs without sign-in or payment. Filza shows `/var/jb/usr/share/hcg-qualification/free.txt`, or `hcg-qualification-free` prints `com.hakmicapitalgroup.qualification.free 1.0.0` |
| 6 | **Sandbox purchase** | Open *HCG Qualification (Paid TEST)*: it shows the sandbox price and is not installable. Tap the price/buy button | HCG Store's checkout opens. Pay with the Stripe test card **4242 4242 4242 4242**, any future date, any CVC. Back in Sileo (refresh the package page) it shows as owned. No real money moves |
| 7 | **Purchased packages retrieved** and **authorized install** | Settings → Payment Providers → **HCG Store**: the profile's **Purchases** section. Then Get → Confirm on the paid package | The paid package is listed as purchased. It installs, and Sileo gets a short-lived HCG Store link for this exact file. `paid.txt` and `hcg-qualification-paid` are present. On a second device signed in with the same account, it is restorable without paying |
| 8 | **Unauthorized downloads denied** | (a) In Safari open `https://repo.hakmicapitalgroup.com/qualification/54906bcc56/repo/protected/com.hakmicapitalgroup.qualification.paid_1.0.0_iphoneos-arm64.deb`. (b) Sign out of HCG Store in Sileo and try to reinstall the paid package. (c) Sign in as account B and try to install it | (a) 404, nothing downloads. (b) Sileo asks you to sign in; no download. (c) A price/purchase prompt only; no download. HCG Store's records show no download for B |

After the test:
1. Remove both packages in Sileo.
2. Sign out of HCG Store.
3. Remove the source, or keep it for the second phone.

Ask HCG Store to revoke or refund the sandbox entitlement if a clean repeat is wanted.

**Recording results.** Write `docs/device-tests/<date>-qualification-sileo-<device>.md` with step 0, each check
(pass/fail, time) and the screenshots. Add HCG Store's own records: the purchase, the entitlement, and the
`authorize_download` and download log lines for account A, and none for B. Only then does
[PACKAGE_MANAGERS.md](PACKAGE_MANAGERS.md) move Sileo from "protocol-tested" to "device-qualified" for that device,
iOS and jailbreak. Zebra is qualified separately and later.

## After qualification

1. Set `[qualification] active = false`.
2. Run Publish or the Qualification workflow. The qualification directory is then no longer deployed.

Activating HCG Store for the public sources is a separate owner decision ([STORE_PROVIDER.md](STORE_PROVIDER.md),
"Activation").

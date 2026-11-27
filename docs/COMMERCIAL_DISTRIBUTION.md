# Commercial distribution: contracts, authorities and production prerequisites

**HCG Repo. One account. One library. Multiple package managers.**

How paid and account-gated HCG packages reach Sileo, Zebra and Saily/Irisin through HCG Repo, without HCG Repo
becoming a second product, price, payment, identity or licence authority. Everything below was exercised in the
isolated TEST environment (`docs/COMMERCIAL_TEST.md`, 22 tests). **None of it is switched on in production**:
HCG Repo advertises no payment endpoint, HCG Store has payments and protected downloads disabled, and no paid package
is listed. Activation needs the founder approvals listed at the end.

Related: client facts `docs/PACKAGE_MANAGERS.md`, account page `docs/PROFILE.md`, ecosystem inventory
`docs/ECOSYSTEM_STATE.md`, commercial gates `docs/COMMERCIAL.md`, Store releases `docs/STORE_RELEASES.md`, signing
`docs/SIGNING.md`, device procedure `docs/DEVICE_QUALIFICATION_COMMERCIAL.md`.

## 1. Authorities

| Concern | Authority | HCG Repo's role |
|---|---|---|
| Identity, sign-in, sessions | **HCG Connect** (central; Supabase-hosted OAuth 2.1, PKCE S256) through HCG Store | None. HCG Repo never shows a password field and has no user accounts |
| Products, editions, prices, offers | HCG ERP → HCG Store (`offers.json`, server-side) | Mirrors product → package mapping in `config/products.toml`; shows a price only from an approved listing |
| Payments, refunds, chargebacks | **HCG Payments** via HCG Store (signed provider webhooks) | None. A checkout redirect is never evidence of payment |
| Entitlements and licences | **HCG central entitlements** (`hcg-ecosystem` authority); `test-ledger` only in TEST | None. No entitlement database or cache in HCG Repo |
| Protected artifact storage and delivery | **HCG Store** (`storageDir`, signed short-lived URLs) | Lists the build (identity only); never stores or serves the bytes |
| Public package index, Candidate/Stable channels, rollback, Release Passport, signing | **HCG Repo** | Owner |
| Device posture (future) | HCG UEM | None today; see §10 |

## 2. HCG Store API contracts used by HCG Repo

Status column: **deployed** = verified read-only on `store.hakmicapitalgroup.com` (27 Nov 2026); **main** = merged
in F1r3srp3nt/store `57e442c` but disabled in production; **proposed** = in
`docs/store-integration/0001-repo-profile-and-ranges.patch`, not merged, not deployed.

| Endpoint | Who calls it | Status | Contract |
|---|---|---|---|
| `GET /api/v1/health` | operators, tests | deployed | `{status, mode, hcgConnect, entitlementAuthority, payments, protectedDownloads, publishing}` — no secrets |
| `GET /auth/login?return_to=` → HCG Connect → `/auth/callback` | browser | deployed | OAuth 2.1 authorization code + PKCE S256; session cookie `HttpOnly; Secure; SameSite=Lax` |
| `GET /api/v1/repo/products/<hcgRepoKey>/access` | HCG Repo product pages (credentialed CORS for `HCG_STORE_REPO_ORIGINS`) | main | `state` ∈ signed-out · free · not-owned · not-for-sale · owned · unavailable; never a URL |
| `GET /api/v1/repo/profile` | HCG Repo **My Library** | **proposed** | §5 and `docs/PROFILE.md` |
| `GET /repo-api/v1/sileo/info` | package managers | deployed | provider name/icon/description; public |
| `GET /repo-api/v1/sileo/authenticate` | package manager web-auth sheet | deployed (redirects to HCG Connect) | ends in `sileo://authentication_success?token=&payment_secret=` |
| `POST /repo-api/v1/sileo/{user_info, package/<id>/info, purchase, authorize_download, sign_out}` | package managers | main (downloads disabled in production) | `docs/PACKAGE_MANAGERS.md` §"payment provider API" |
| `GET /repo-api/v1/files/<signed>` | package managers | main (disabled) | streams one exact artifact; **Range** support proposed |
| `POST /api/v1/checkout` → `/test-checkout/<id>` | browser | main (payments disabled; test provider only in TEST) | creates a pending checkout; grants nothing |
| `POST /api/v1/payments/webhook/<provider>` | HCG Payments | main | HMAC-verified (`hcg-test-signature: t=,v1=` in TEST), idempotent by event id; `payment.succeeded` → grant, `payment.refunded` → revoke |
| `GET /api/public/v1/release-resolution/<id>` | Release Manager | main | resolves a Store release id to its artifacts for `import-store-release` |

HCG Repo never consumes the Store's public pricing catalogue as a product directory. It renders only products that
are in `config/products.toml` with an approved listing (`docs/COMMERCIAL.md`).

## 3. Authentication per client

| Client | Flow | Credential on the device |
|---|---|---|
| Website (My Library, product pages) | Browser → HCG Store `/auth/login` → HCG Connect (PKCE S256) → Store session cookie. HCG Repo reads `profile`/`access` with `credentials: "include"` | Store session cookie (HttpOnly), never visible to HCG Repo's JavaScript |
| Sileo, Zebra, Saily/Irisin | `ASWebAuthenticationSession` opens `authenticate` → the same HCG Connect login in the system browser sheet → `sileo://` callback | Random repo token + payment secret in the keychain; revocable (`sign_out`, account page, refund/revocation does not need it) |
| Cydia, Installer 5, APT | No supported flow. Paid packages are not offered to them; customers use the website and a supported package manager | — |

Rules (tested): no HCG password field anywhere in HCG Repo; no OAuth client secret in any package (the Store is the
confidential client); no token in a repository URL, `Packages`, `Release`, depiction, page or log (test 19); device
UDIDs are never a credential (test 21). Production needs a registered HCG Connect client and the exact callback
`https://store.hakmicapitalgroup.com/auth/callback` — already configured (`hcgConnect: configured`); HCG Repo needs
no client of its own.

## 4. Entitlement categories

HCG Store decides; HCG Repo only chooses how a package is *listed*. Being signed in never implies ownership (tests
4, 21).

| Category | Where it is decided | Index entry in HCG Repo | Download path |
|---|---|---|---|
| Public free | `[access] level = "public"` | normal `Filename: debs/…` | GitHub Pages |
| Account-required free | `level = "connect"` + Store `distribution: free-with-account` | `Tag: cydia::commercial`, `Filename: protected/…` | `authorize_download` (signed-in only) |
| Temporary free testing | Store free window (Ship-to-Store releases) or time-limited grant | as paid | `authorize_download` until the window/grant ends (test 11) |
| Purchased (one-time) | HCG Payments webhook → entitlement | as paid | `authorize_download` |
| Subscription | entitlement with `expiresAt` = period end | as paid | stops at expiry |
| Private beta | Store release eligibility `private`/`internal-testing` | **not listed** in HCG Repo (only `public` eligibility is importable) | Store only |
| Developer access | central entitlement (`founderPolicy`/developer grant) | as paid | `authorize_download` |
| Organizational licence | central entitlement (organization source) | as paid | `authorize_download` |
| Expired | authority returns not-granted | unchanged | 403 |
| Refunded | webhook `payment.refunded` → revoked | unchanged | 403 for new downloads (test 10) |
| Revoked | authority revocation | unchanged | 403 (test 10) |
| Unavailable (authority/IdP outage) | Store fails closed | unchanged | 503, nothing granted (test 18) |

Version-specific licensing: an entitlement is per product **edition**; each Store release maps edition → exact
artifact (package, version, architecture, SHA-256). A licence for edition A never authorizes edition B's artifact
(test 9). A future "major version" licence is a new edition, not a new mechanism.

Caching: HCG Repo caches nothing. HCG Store's only cache is its session freshness check (re-validated against HCG
Connect on each protected request); every `authorize_download` and every file fetch re-checks the authority.

## 5. HCG Repo Profile ("My Library")

`/account/` on the website. Reads `GET /api/v1/repo/profile` and renders: account, library (owned products mapped to
HCG Repo packages, with the version HCG Repo currently offers), purchase history (TEST purchases labelled), connected
package managers, restore instructions, betas, trust. Display only; no token reaches the page. Native account pages:
Sileo (Settings → Payment Providers → profile, `PaymentProfileViewController`) and Zebra (source account banner) show
`user_info` from the same Store; nothing in those apps was modified. Contract and states: `docs/PROFILE.md`.

## 6. Delivery binding

`authorize_download(package, version, architecture, repo)`:

1. Token → Store repo session → HCG account (`sub`). Unknown/revoked token → `invalidate: true`.
2. Package → HCG Repo product key → Store product + edition. Unknown → 404.
3. Version + architecture → exactly one approved, verified Store artifact with matching Debian identity; not
   `deliveryApproved` → 404 (test 13).
4. Entitlement check against the authority **now**. Not granted → 403; unavailable → 503.
5. URL `…/repo-api/v1/files/<signed>` where the HMAC-signed payload is `{a: artifact id, s: sub, h: session hash,
   exp}`; TTL 120 s by default (60 s in TEST).
6. On fetch: signature, expiry (test 8), session still alive and same `sub`, entitlement re-checked, then the exact
   bytes with `Cache-Control: private, no-store`. Retries within the TTL work; byte ranges with the proposed patch
   (test 22).

The artifact id binds package, version, architecture, channel/release and SHA-256 implicitly (the artifact record is
immutable). Recommendation R1 (§11): add the SHA-256 to the signed payload so a registry edit can never re-point a
live URL.

## 7. Purchase flow

Package manager `purchase` → status 1 + Store checkout URL (opens in the browser) → HCG Payments → signed webhook →
entitlement → package manager re-queries `package/<id>/info`. The `purchase` response, the browser return URL and the
package manager's own claim never grant anything (tests 4, 5). Refund and revocation stop new downloads at once; files
already installed on a device are not remotely removed (no client supports that). Production: HCG Payments with a
Stripe **sandbox** account first; real payments only after founder approval.

## 8. Lifecycle mapping

| HCG lifecycle | HCG Repo (index) | HCG Store (delivery) |
|---|---|---|
| Private draft | not in `repo-lock.json` | `draft` release |
| Candidate | `/repo/candidate/` (free betas only; paid builds never in Candidate — test 12) | release channel `beta` |
| Stable | `/repo/` | channel `stable` |
| Current compatible | newest version with a non-failing registry verdict | `pub_current` on the target |
| Superseded | kept in the index, labelled (rollback) | `superseded`, files kept |
| Disabled | paid entry kept in the index; the Store refuses delivery (404) until restored — test 13 | `availability: disabled` / not `deliveryApproved` (restorable) |
| Withdrawn | `bin/hcgrepo withdraw`: removed from the index and `pool/`; the lock entry stays as `withdrawn` so the version can never be reused with other bytes | `withdrawn` |
| Archived | no separate state: a superseded or withdrawn entry stays in `repo-lock.json` history | `archived` |
| Revoked | same as withdrawn (HCG Repo has no separate revoked state) | `revoked` (irreversible, DB trigger) |

Bytes are immutable on both sides: a version is never re-uploaded with different content. Changing one architecture
or channel never touches another (Store targets are product × edition × platform × architecture × channel).

**Policy gap found (P-1):** for Ship-to-Store published releases the Store delivers only the *current* release of a
target, while HCG Repo keeps superseded versions in the index for rollback. A paid rollback selected in Sileo would
be refused. Catalogue-registered artifacts (as in TEST) stay deliverable while `deliveryApproved`. Founder decision:
either the Store also delivers superseded-but-available releases to entitled customers, or HCG Repo lists only the
current paid version. Until decided, HCG Repo should list only the current paid version.

## 9. Executable Maker "Ship to Repo"

Executable Maker never publishes. "Ship to Repo" = produce a handoff and submit it to the existing Release Manager:

1. Select the project and package (Debian `Package` id must already be mapped in `config/products.toml`).
2. Build the `.deb`; write `submission.json` (`docs/schemas/artifact-submission-v1.schema.json`) and a Release
   Passport v1 (`docs/RELEASE_PASSPORT.md`) with source commit, build run, SHA-256, size, architecture, layout,
   minimum iOS, evidence levels claimed.
3. Upload: free packages → `bin/hcgrepo release submit --handoff <dir>` (or a pinned GitHub artifact); paid packages
   → HCG Store Distribution Studio (PR #28) only; HCG Repo then records it with `import-store-release`.
4. Result: `submission-result-v1` JSON with state `submitted` → `validated` → `approved` / `rejected`, as today.
5. Publication only by the owner (`docs/RELEASE_APPROVALS.md`). No flag in the handoff can skip review or publish.

## 10. Nexus Trust Center extension and UEM readiness

**Nexus (read-only):** for each HCG package Nexus may show ownership and licence eligibility (from the Store's
`/api/v1/repo/products/<key>/access` or profile, through HCG Connect), installed version (dpkg on device), current
and available versions (HCG Repo `Packages` + `compatibility.json`), verification (`docs/NEXUS_TRUST_CENTER.md`
contracts), signing status, registry verdict and availability. Nexus grants nothing, stores no entitlement and never
calls `authorize_download` on behalf of another app.

**UEM readiness:** device identifiers (serial, UDID, MAC) are never authentication credentials; a jailbroken device
is never automatically trusted; a device registration is an *additional* signal on top of an HCG Connect session,
never a replacement. `HCG_STORE_DEVICE_POLICY` stays off; ordinary purchases never require UEM.

## 11. Security review (TEST phase, 27 Nov 2026)

| # | Finding | Severity | Status |
|---|---|---|---|
| F1 | Every supported client sends the device UDID (Irisin also as `X-Unique-ID`) | Info | Store ignores it; never stored or used; asserted in tests |
| F2 | Irisin accepts any `sileo://` callback with `token` and `payment_secret` (token injection by another app) | Medium (client) | Server-side: tokens random, account-bound, revocable, scoped to the provider API. Report upstream |
| F3 | Signed file URL is a bearer link for its TTL (120 s), bound to artifact + session, not to the device | Low | Accepted (clients fetch through their own downloader); keep TTL short |
| R1 | Signed payload omits the artifact SHA-256 | Low | Recommend adding `sha` and checking it on fetch |
| F4 | An unhandled static-file `ENOENT` crashes the Store process | Medium (availability) | Store fix needed; TEST supplies its own static dir |
| F5 | No HTTP Range support on protected downloads | Low (reliability) | Proposed patch; test 22 |
| F6 | Zebra checks no package checksum; Zebra and Irisin verify no repository signature | Info | Documented; HTTPS + signed URL are the protection for those clients |
| F7 | Irisin does not trim `payment_endpoint` | Fixed | HCG Repo writes it without a newline |
| F8 | Store `CLAUDE.md` says the server is not deployed; it is (payments/downloads off) | Docs | Store docs update |
| F9 | Store `docs` claim Zebra is unsupported; Zebra implements the same API | Docs | Store docs update |
| P-1 | Paid rollback policy differs between HCG Repo and Store (§8) | Policy | Founder decision |

Separation kept: repository signing (Release.gpg), package verification (SHA-256 in the index, Release Passport) and
entitlement authorization (Store) are three independent checks; none implies another. A full external security
review of the Store's delivery path is required before commercial delivery goes live.

## 12. Production prerequisites (nothing here is done; each needs explicit founder approval)

1. Review and merge the Store patch (`docs/store-integration/0001-repo-profile-and-ranges.patch`), deploy, and add
   `https://repo.hakmicapitalgroup.com` to `HCG_STORE_REPO_ORIGINS`.
2. Store fixes F4 and R1; documentation fixes F8/F9.
3. HCG Payments with a Stripe **sandbox** account through the Store; end-to-end sandbox purchase on production
   infrastructure; then live payments by separate approval.
4. Decide policy P-1.
5. An approved Store offer and Store release (`approvedEligibility: public`) for the first paid product, then the
   HCG Repo commercial gates (`docs/COMMERCIAL.md`).
6. Turn on Store protected downloads, then set `[payment_provider] advertise = true` with the owner approval record, and run Publish with `advertise_payment_provider` ([STORE_PROVIDER.md](STORE_PROVIDER.md)).
7. Physical qualification on iPhone 11 Pro Max and iPhone 13 Pro with Sileo and Zebra
   (`docs/DEVICE_QUALIFICATION_COMMERCIAL.md`).
8. Production repository signing (founder key ceremony, `docs/SIGNING.md`) — independent of commerce, recommended
   before paid launch so Sileo users can verify the index.
9. Security review of the delivery path.

Third-party marketplace (future): sellers would be Store publishers with their own products and editions; HCG Repo
would list them under the same gates; payouts, commissions and marketplace payment processing are **not** active and
need their own legal, tax and payments design.

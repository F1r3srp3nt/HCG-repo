# Commercial TEST environment

An isolated, local environment that runs the **real, unmodified HCG Store server code** in test mode next to a real
HCG Repo build, and drives it with protocol fixtures for Sileo, Zebra and Saily/Irisin. It contacts no production
system, takes no payment, publishes nothing and uses only TEST products (`com.hakmicapitalgroup.test*`, every name
and price labelled TEST).

## Components

| Part | What runs | File |
|---|---|---|
| HCG Store (TEST) | Store server from a local checkout (`HCG_STORE_CHECKOUT`, default `~/f1r3srp3nt/store/web-hcg-store`), `HCG_STORE_MODE=test`, HTTPS with a throw-away self-signed certificate on 127.0.0.1 | `tools/commercial-test/store-test-server.mts` |
| HCG Connect | The Store's own `MockHcgConnect` (OAuth 2.1 authorization code + PKCE S256, ES256 ID tokens). A protocol mock, **not** HCG Connect | same |
| Entitlements | `TestLedgerAuthority` (`test-ledger`) behind a proxy that can simulate an outage | same |
| Payments | The Store's test payment provider; refunds through an HMAC-signed `payment.refunded` webhook | `tests/commercial_env.py` |
| HCG Repo (TEST) | A real `bin/hcgrepo` build with three TEST paid products recorded through `import-store-release`, a free stable package and a free Candidate package with a rollback version; served over HTTPS (Zebra needs HTTPS for payment providers) | `tests/test_commercial_acceptance.py` |
| Package managers | `PaymentClient("sileo" \| "zebra" \| "irisin")`: the exact requests each client's source sends, including its quirks (Zebra HTTPS-only, Irisin no trimming and `payment_secret` on downloads, UDID headers) | `tests/commercial_env.py` |
| Controls | Loopback HTTP: `next-user`, `clock`, `idp` (down), `authority` (down), `grant`, `revoke-all`, `free-window`, `status` | `store-test-server.mts` |

## Run

```sh
# once, in the Store checkout
(cd ~/f1r3srp3nt/store/web-hcg-store && npm ci)

python3 -m unittest tests.test_commercial_acceptance -v      # from HCG-repo, with tools/ and tests/ on PYTHONPATH
# or the whole suite: python3 -m unittest discover -s tests

tools/commercial-test/run_test_environment.py              # keep it running for manual checks and screenshots
```

Without a Store checkout the suite is skipped. Tests 21 (Profile) and 22 (Range) skip on a Store that lacks the
proposed patch (`docs/store-integration/0001-repo-profile-and-ranges.patch`).

## Results (27 Nov 2026)

| Store revision | Result |
|---|---|
| `57e442c` (Store `main`, 27 Nov) | 22 run: 20 passed, 2 skipped (21, 22 need the patch) |
| `bc19ab5` (Store `main`, later) | 28 run: 26 passed, 2 skipped |
| `06c8575` (`bc19ab5` + patches 0001 and 0002, local only, not pushed) | 28 run: 28 passed; Store server tests 165 passed |
| Store's own server tests with the patch | 146 passed; `tsc` clean |
| Whole HCG Repo suite (`python3 -m unittest discover -s tests`) | 278 run, all passed |

| # | Acceptance item | Test |
|---|---|---|
| 1 | Anonymous discovery of public packages | `test_01` |
| 2 | Anonymous users cannot download paid packages (no file at `protected/…`, `authorize_download` refused) | `test_02` |
| 3 | Sign-in through the HCG Connect protocol (PKCE) from the package-manager flow | `test_03` |
| 4 | Signed in without entitlement → no download | `test_04` |
| 5 | Sandbox (test-provider) purchase creates a TEST entitlement | `test_05` |
| 6 | Entitled customer downloads the exact package (SHA-256 = HCG Repo index = built deb) | `test_06` |
| 7 | One entitlement across Sileo, Zebra and Irisin adapters | `test_07` |
| 8 | Expired authorization fails (`link_expired`) | `test_08` |
| 9 | Authorization for one package cannot fetch another; a forged link fails | `test_09` |
| 10 | Refund and revocation stop new downloads | `test_10` |
| 11 | Temporary free testing ends at its expiry | `test_11` |
| 12 | Candidate and Stable stay separate; paid builds never in Candidate | `test_12` |
| 13 | Superseded (still delivery-approved) stays available for rollback; disabled → 404 | `test_13` |
| 14 | Protected artifacts absent from the public site and pool | `test_14` |
| 15 | Free installs unchanged | `test_15` |
| 16 | Release Passport and `verify-artifact` still work | `test_16` |
| 17 | Rollback versions intact | `test_17` |
| 18 | IdP or entitlement-authority outage grants nothing | `test_18` |
| 19 | No token, payment secret or webhook secret in logs or public files | `test_19` |
| 20 | Test mode, test ledger, no production payment endpoint or signing configured | `test_20` |
| 21 | Profile: CORS only for HCG Repo origins, ownership from the ledger, no UDID/token/URL in the response | `test_21` |
| 22 | Byte ranges after authorization (206/416), never without it | `test_22` |
| — | HCG Store as the official provider: Sileo free install, authenticated discovery, TEST purchase and authorized install with real `dpkg`, blocked downloads, Zebra evaluation, integration status ([STORE_PROVIDER.md](STORE_PROVIDER.md)) | `test_23` … `test_28` |

## What this does not prove

- **No physical device was used.** These are protocol tests against fixtures written from upstream source
  (`docs/PACKAGE_MANAGERS.md`). Real Sileo and Zebra behaviour is qualified separately
  (`docs/DEVICE_QUALIFICATION_COMMERCIAL.md`).
- The HCG Connect used is the Store's protocol mock, not the production identity service.
- Payments use the Store's test provider, not HCG Payments or Stripe sandbox.
- Temporary free testing (11) uses an expiring ledger grant: the Store's free windows apply to Ship-to-Store published
  releases, while the TEST products are catalogue-registered.

## Screenshots

Captured from this environment with Playwright: home page with filters, My Library signed out and signed in (after a
TEST purchase), Package Trust Center, Install page, owned product page. Every account, product and purchase shown is
TEST data.

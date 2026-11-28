# HCG Store as HCG Repo's official payment provider

**Status (27 Nov 2026): implemented and tested in the isolated TEST environment. Not advertised and not active.**
- Production `config/repo.toml` sets the provider URL `https://store.hakmicapitalgroup.com/api/sileo/v1/` with
  `advertise = false`, so `https://repo.hakmicapitalgroup.com/repo/payment_endpoint` does not exist.
- No paid package is indexed. HCG Store's payments are disabled in production.
- Activation needs your explicit approval and the steps at the end.

HCG Store is the only authority for products, prices, purchases, purchase history and entitlements. HCG Repo only
publishes where the provider is, lists eligible paid builds, and never stores or serves their bytes.

## Protocol reference

The requested reference, `https://developer.getsileo.app/payment-providers`, could not be fetched from this build
environment: the network proxy refuses that host and web.archive.org. Everything here is implemented and tested against
the clients' own source code, which is what the apps actually do:
- Sileo `c9f7015` (2.5.1), `Sileo/Backend/Payment Manager/`;
- Zebra `e2f3c27` (1.1.36), `ZBPaymentVendor.m` and `ZBDownloadManager.m`;
- Irisin `3924284` (v5.1.8).

Details per client are in [PACKAGE_MANAGERS.md](PACKAGE_MANAGERS.md). Re-check this page against the official
documentation before activation.

| Step | Request | HCG Store |
|---|---|---|
| Discovery | `GET <source>/payment_endpoint` → one HTTPS URL as plain text | written by HCG Repo |
| Provider info | `GET <provider>info` | name, icon, description, sign-in banner |
| Sign-in | `GET <provider>authenticate?udid=&model=` in the system web sheet → `sileo://authentication_success?token=&payment_secret=` | HCG Connect, PKCE S256. No password field anywhere in HCG Repo. The UDID is ignored |
| Account | `POST <provider>user_info` `{token, udid, device}` | name, email, owned package IDs, from the entitlement authority |
| Package | `POST <provider>package/<id>/info` | price from HCG Store's approved offer; `purchased` from the authority |
| Purchase | `POST <provider>package/<id>/purchase` `{token, payment_secret}` | `status: 1` + HCG Store checkout URL. **Never grants** |
| Download | `POST <provider>package/<id>/authorize_download` `{token, version, repo, architecture}` | Re-checks the entitlement, the exact approved artifact and the launch switch, then returns a 120 s signed HTTPS URL |
| Sign-out | `POST <provider>sign_out` | revokes the token |

Errors are `{success: false, error, invalidate}`.

## Payment provider discovery (`config/repo.toml [payment_provider]`)

```toml
[payment_provider]
url = "https://store.hakmicapitalgroup.com/api/sileo/v1/"
trusted_hosts = ["store.hakmicapitalgroup.com"]
advertise = false          # true writes <repo>/payment_endpoint (stable source only)
approved_by = ""           # required when advertise = true
date = ""                  # YYYY-MM-DD
record = ""                # the owner's approval, verbatim
```

**Validation, always (`tools/hcgrepo/config.py`, `tests/test_payment_provider.py`).** The URL must be `https`, and
its host (with port, if any) must exactly match an entry in `trusted_hosts`. Refused:
- lookalike hosts and other ports;
- credentials, a query or a fragment;
- `..` or empty path segments;
- whitespace or control characters (Irisin does not trim);
- wildcard or scheme-prefixed `trusted_hosts` entries.

The old `[access] payment_endpoint` setting is refused, with a pointer here.

**Advertisement needs two keys.**
1. `advertise = true` with the owner approval record in the reviewed config. Without the record, the configuration
   does not load.
2. The **Publish repository** run must also be started with `advertise_payment_provider` ticked. A build that
   advertises a provider without that input fails, and so does the input without an advertising build.

The file is the URL alone, without a trailing newline. The Candidate source never has one.

**Provider path.** The production Store serves the provider at `/repo-api/v1/sileo/` today; `/api/sileo/v1/`
returns 404 (`bin/hcgrepo store status` shows this live). The Store patch
`docs/store-integration/0002-provider-api-sileo-v1.patch` serves the same handlers at `/api/sileo/v1/` and keeps
`/repo-api/v1/sileo/` as an alias. It includes a Store test, and the Store's 165 server tests pass with it. Either
deploy that patch, or change `url` to the live path, before activation.

## Commercial packages

A paid build is listed only when all of these hold:
1. The provider is advertised.
2. HCG Store approved the exact artifact for public delivery to HCG Repo, and HCG Repo recorded it with
   `import-store-release`, pinned by SHA-256.
3. The product's commercial gates are open, for exactly `approved_version` ([COMMERCIAL.md](COMMERCIAL.md)).

The index entry keeps the package's own control data: version, architecture, dependencies and checksums (MD5, SHA1,
SHA256, SHA512, size). It adds `Tag: cydia::commercial`, and `Filename: protected/<file>`, a relative path that is
never deployed. A relative, host-less Filename is what makes Zebra ask the provider.

Free packages are unchanged: ordinary `Filename: debs/…` downloads with no account, in both sources.

Package identifiers come from HCG Store's catalogue. HCG Repo's `config/products.toml` maps them, and
`bin/hcgrepo store status` cross-checks the two (live, or against a Store checkout's catalogue):
- `store_slug` and `store_edition` ↔ HCG Store product and edition;
- `package_ids` ↔ the edition's `debian-package` identifier.

Version mapping: the Debian `Version` must equal the Store release's version, or `<version>-<revision>`. This is
enforced by `import-store-release`, because HCG Store's `authorize_download` matches it that way.

## Secure distribution

- Paid bytes never enter Git, `pool/`, the Pages site or a public release. The build fails if a protected file or
  install link would be published, and `verify` reports `protected-exposed` if one appears live.
- Downloads go only through `authorize_download`: a server-side entitlement check, then a 120 s link bound to the
  artifact and the sign-in session, re-checked when used. Range requests are supported with patch 0001.
- Store outages fail closed (503), and nothing is cached in HCG Repo.
- Sileo checks the downloaded file against the index SHA-256 (tests 23, 25). Zebra does not, so for Zebra, HTTPS and
  the signed link are the protection.

## Publishing integration (HCG Store release approval → HCG Repo publication)

| Stage | Where | Mechanism |
|---|---|---|
| Private draft | HCG Store | Ship to Store draft, `eligibility: private`. Never importable into HCG Repo (`store-release-not-public`) |
| Artifact validation | HCG Store, then HCG Repo | Store-side verification (SHA-256, format, control fields). `import-store-release` re-reads the bytes, compares identity, size and SHA-256, and runs HCG Repo's package validation |
| Product association | both | Catalogue edition ↔ `config/products.toml`; refused on mismatch (`store-unmapped`, `store-product-mismatch`, `store-edition-mismatch`) |
| Release identifiers | lock and provenance | `store_release`, `store_artifact` and the record's SHA-256 are kept with the lock entry |
| Package metadata validation | HCG Repo | `bin/hcgrepo validate` (also in CI and Publish) |
| Commercial status | `config/products.toml` | Owner-approved gates; only `approved_version` is indexed |
| Publication approval | HCG Store, then HCG Repo | A different person approves in HCG Store. In HCG Repo, the recording PR is reviewed and the manual **Publish** run is started (`approval check-publication` when `[approvals] required_for_publish`) |
| Plan | `bin/hcgrepo store plan` | Lists what HCG Store currently serves for each mapped product (public release-resolution and published feeds) against the lock: `new` (exact record-and-import command), `recorded`, `conflict`, `recorded-not-current`, `not-published-in-store`. It never imports |
| Index regeneration | **Publish** | Rebuilds the index from the lock; the guard compares against the live site |
| Controlled rollback | `bin/hcgrepo store rollback --product K --to V` | Read-only check that V is a recorded, protected, stable, Store-linked build that HCG Store serves now. Prints the exact reviewed `products.toml` edit (`approved_version` plus an approval record). Bytes never change, and other architectures and channels are untouched |

Manual governance is unchanged: nothing is imported, approved or published automatically.

## Administration

`bin/hcgrepo admin` → **HCG Store Integration** tab, or `bin/hcgrepo store status [--json]`. Data sources:

| Item | Source |
|---|---|
| Configured payment provider | `[payment_provider]` |
| Provider API health | live `GET <provider>info` |
| Entitlement service | live `GET <store>api/v1/health`: authority, HCG Connect, payments, protected downloads |
| Package associations | `config/products.toml`, lock and provenance; shows which builds are indexed and their Store release |
| Free and paid counts | lock and package access levels |
| Unmapped products | Store catalogue (local checkout) or live release-resolution |
| Invalid metadata | the same checks as `validate` |
| History | Git history of the lock and configs, Store links and owner approvals |

Live findings on 27 Nov 2026 (production, read-only):
- The provider API at `/api/sileo/v1/` returns 404 (the patch is not deployed).
- HCG Store: `production`, HCG Connect configured, entitlements `hcg-ecosystem`, payments `disabled`, protected
  downloads `free-testing-only`, publishing `enabled`.
- `hcg-animation-center` points to a Store product that is not in HCG Store's catalogue.
- `hcg-vortex` has no Store product.
- 8 metadata warnings and no errors. Since 28 Nov they are reviewed acknowledgements (`[[validation.acknowledged]]` in the package's `package.toml`) for the exact published builds whose bytes cannot change. Nexus 0.6.0~beta1 and Vortex 0.1.0~beta2 already fixed them, and no package identity changed.
- HCG Store currently publishes no `.deb` for HCG Repo's products.

## Tests (TEST environment, [COMMERCIAL_TEST.md](COMMERCIAL_TEST.md))

| Test | What it shows |
|---|---|
| 23 | Free install through Sileo's path on a source with a provider: index, `debs/` download, SHA-256 check, real `dpkg -i` into an isolated root. No account, and no provider call |
| 24 | Authenticated discovery: `payment_endpoint` is exactly the provider URL; the paid entry has `cydia::commercial`, a `protected/` Filename, checksums, architecture and dependencies; `info` gives the TEST price; signed in ≠ owned |
| 25 | `purchase` returns the HCG Store checkout and grants nothing. A TEST checkout (signed webhook) creates the entitlement. Then `authorize_download` and download, SHA-256 check and a real `dpkg -i`: `install ok installed`, files present |
| 26 | Blocked: the static `protected/` path (404 on both servers), not signed in, signed in without entitlement, the wrong package, and a forged or signed-out token |
| 27 | **Zebra evaluation (protocol only, not qualified):** relative Filename → authorization; a signed-out `token: "none"` is refused; signed-in owner installs |
| 28 | `store status`, `plan` and `rollback` against the TEST Store: provider OK, test mode, associations, plan `recorded`, rollback to an unrecorded version refused |

Results:
- Store `main` + patches 0001/0002: 28 run, 28 passed.
- Store `main` `bc19ab5` without the patches: 28 run, 26 passed, 2 skipped (the profile and ranges tests). The TEST
  environment then uses `/repo-api/v1/sileo/`.

Not shown by these tests: a physical Sileo or Zebra. Device qualification follows
[DEVICE_QUALIFICATION_COMMERCIAL.md](DEVICE_QUALIFICATION_COMMERCIAL.md).

## Activation (each step needs your explicit approval)

1. Deploy Store patches 0001 and 0002, or set `url` to the path that is live.
2. HCG Store: protected downloads `enabled`, and HCG Payments with Stripe sandbox, then live.
3. An approved Store release and offer for the first paid product; recorded with `import-store-release`; commercial
   gates opened.
4. Physical qualification on iPhone 11 Pro Max and iPhone 13 Pro (Sileo first; Zebra separately): [QUALIFICATION_SILEO.md](QUALIFICATION_SILEO.md).
5. `[payment_provider] advertise = true` with your approval record, in a reviewed PR. Then **Publish** with
   `advertise_payment_provider`.

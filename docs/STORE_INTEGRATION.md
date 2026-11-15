# HCG Store integration

**Status: links only.** HCG Repo product pages link to HCG Store, and HCG Store product pages can
link back. HCG Store (`https://store.hakmicapitalgroup.com/`) is **not live yet**, so every store
link and the access check fail until it is. Nothing can be installed through the store yet.

## Products and mapping

`config/products.toml` is the mapping. The HCG ERP remains the source of truth for products;
update this file to match it.

| Key (Repo page) | Name | Store page | Package IDs | Access |
|---|---|---|---|---|
| `hcg-terminal-unleashed` | HCG Terminal Unleashed | `apps/hcg-terminal?edition=unleashed` | `com.hakmicapitalgroup.hcgterminal.unleashed` | `login-required` |
| `hcg-nexus` | HCG Nexus | `apps/hcg-nexus?edition=jailbreak` | `com.hakmicapitalgroup.hcgnexus` | `login-required` |
| `hcg-sentinel` | HCG Sentinel | `apps/hcg-sentinel` | none | `login-required` |

HCG Sentinel has no Debian package and no jailbreak build. It is listed only so that navigation
between the store and the repository works. It cannot be installed from HCG Repo.

`access` is one of:

- `public`: anyone may download the package. Only these packages may be in this repository.
- `login-required`: the visitor must be signed in to HCG Store (free tools included).
- `paid`: the visitor must have bought the product in HCG Store.

A product without `access` is treated as `login-required`. Under the current HCG release policy,
every product is `login-required` or `paid`.

## Routes

| From | To | URL |
|---|---|---|
| HCG Repo | Repo product page | `<repo base>products/<key>/` (for example `https://repo.hakmicapitalgroup.com/products/hcg-nexus/`) |
| Repo product page | Store product page | `<store>apps/<store_slug>[?edition=<store_edition>]` |
| Repo product page | Store sign-in | `<store>auth/login?return_to=<URL-encoded /apps/<store_slug>[?edition=…]>` |
| Repo product page | Access check | `GET <store>api/v1/repo/products/<key>/access` |
| HCG Store | Repo product page | `<repo base>products/<key>/` |

`<store>` is `[store] base_url` in `config/repo.toml` (`HCG_STORE_BASE_URL` overrides it). It must
be `https://`. Product pages are at the site root, not under `repo/`, so the APT source
`<repo base>repo/` and everything under it is unchanged. The landing page's cards link each product
page ("Product page").

## Product page and access states

Without JavaScript, the page shows two links: **Sign in to check access** and **View in HCG Store**.
`assets/store-access.js` then asks the store's access endpoint, sending the store's own cookie
(`credentials: "include"`, `Accept: application/json`, 5-second timeout). The store answers:

```json
{ "state": "signed-out | not-owned | owned | not-for-sale | unavailable",
  "buy_url": "optional", "store_url": "…", "downloads_available": false, "message": "optional" }
```

| State | Shown |
|---|---|
| `signed-out` | The default links stay |
| `not-owned` | **Buy Product**: `buy_url` if it is under the store base URL, otherwise the store product page |
| `owned` | **Owned** badge. **Get from HCG Repo** is disabled with "Downloads are not available yet." unless `downloads_available` is `true`. Even then the page only explains how to add the source and install in Sileo; it never links a `.deb` |
| `not-for-sale` | **Not for sale yet** (disabled) |
| `unavailable`, network or CORS error, timeout, non-JSON | The default links stay, plus "Access could not be checked right now." |

The script uses only URLs from the response that start with the configured store base URL. It puts
no token in any URL, stores nothing, and uses no inline code, so it works under a strict CSP. The
store must allow this site's origin with credentials in its CORS policy for the check to work.

## Why installation is unavailable

Everything in this repository is downloadable by anyone who knows the URL. A login-gated or paid
package must therefore never be in `pool/`, the `Packages` index or `debs/`. The validator enforces
this for `inspect`, every `import-*`, `validate`, `build` and `verify`:

- `access-not-public`: the package belongs to a product whose `access` is not `public`.
- `access-unmapped`: the package is in the `com.hakmicapitalgroup.` namespace but no product lists
  it. It is refused until it is mapped (fail closed). The only exception is repository
  infrastructure listed in `public_packages` in `config/repo.toml`, currently the archive keyring
  `com.hakmicapitalgroup.repo-keyring`, which is public by nature and is not a product.
- `index-access-*` (`verify`): a published index lists such a package.

Product pages of non-public products say "Downloads are not available yet." and have no
"Install in Sileo" button and no `.deb` link. `verify` checks this on every build.

## Withdrawn packages

Two packages were imported on `main` (PR #2) and then withdrawn under the HCG release policy (both products are `login-required`, and nothing login-gated may be published at a public URL) pending sign-in/access enforcement and launch approval. They were never published by the
**Publish repository** workflow.

| Package | Version | Layout | SHA-256 |
|---|---|---|---|
| `com.hakmicapitalgroup.hcgnexus` | 0.2.0 | `iphoneos-arm64` | `1987156996c2e76fb005bb66590aff76b77c474c4387ede6e015f19a081616a5` |
| `com.hakmicapitalgroup.hcgterminal.unleashed` | 0.1.0-1 | `iphoneos-arm64` | `7fdb735e74e551fa05a23a81714ff537f97556a67d600f49f090b543ef1d42bf` |

They were withdrawn with `bin/hcgrepo withdraw`. That command deleted each `.deb` from `pool/` and marked
its `repo-lock.json` entry `withdrawn` with the reason. The lock entries and the `provenance.json` files
stay, so these versions can never be re-imported with other bytes, and **Prepare packages** treats
their import requests in `imports/` as done.

**The `.deb` files remain in the public git history** of this repository (the commits from PR #2).
Removing them completely would need a history rewrite and force-push. That is a founder decision, and
it has not been done.

## Sileo payment endpoint

`[payments] endpoint` in `config/repo.toml` is empty, which disables it. When HCG Store's Sileo
payment provider is approved and live, set it to the provider's `https://` URL under the store
base URL. The build then writes `repo/payment_endpoint` containing that URL, which is where Sileo
looks for it, and `verify` checks the file. While the setting is empty, `verify` reports an error
if the file exists.

## What remains

1. **Protected index entries.** Commercial packages need `Packages` entries with
   `Tag: cydia::commercial`-style metadata, whose binaries live only behind the store's authorised
   download service and never in `debs/`. This is not implemented. It is the remaining
   package-manager integration work, so installation stays unavailable until it is done.
2. **Store go-live.** The store, its sign-in, its access endpoint, its CORS policy for this site and
   its payment provider must be live before any of the store links work.
3. **Domain and HTTPS.** Builds now embed `https://repo.hakmicapitalgroup.com/` (`default_target =
   "production"`), and the old project URL redirects there. HTTPS on the domain does not work yet
   ([TRANSPORT.md](TRANSPORT.md)). The Store should link to
   `https://repo.hakmicapitalgroup.com/products/<key>/`, and its CORS policy must allow that origin.
   The access check cannot be relied on until the Repo is served over HTTPS.

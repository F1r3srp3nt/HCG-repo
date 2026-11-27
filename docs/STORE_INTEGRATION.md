# HCG Store integration

**Status: links only.** HCG Repo product pages link to HCG Store, and HCG Store product pages can
link back. HCG Store (`https://store.hakmicapitalgroup.com/`) is **not live yet**, so every store
link and the access check fail until it is. Nothing can be installed through the store yet.

Who may download a package, and how protected packages are delivered, is defined in
[ACCESS.md](ACCESS.md). This page covers only the links between HCG Repo and HCG Store.

## Products and mapping

`config/products.toml` is the mapping. The HCG ERP remains the source of truth for products;
update this file to match it.

| Key (Repo page) | Name | Store page | Package IDs | Access ([ACCESS.md](ACCESS.md)) |
|---|---|---|---|---|
| `hcg-terminal-unleashed` | HCG Terminal Unleashed | `apps/hcg-terminal?edition=unleashed` | `com.hakmicapitalgroup.hcgterminal.unleashed` | `connect` (HCG Connect sign-in) |
| `hcg-nexus` | HCG Nexus | `apps/hcg-nexus?edition=jailbreak` | `com.hakmicapitalgroup.hcgnexus` | `paid` (sign-in and HCG Store ownership) |
| `hcg-sentinel` | HCG Sentinel | `apps/hcg-sentinel` | none | none |
| `hcg-animation-center` | HCG Animation Center | `apps/hcg-animation-center` (no Store catalogue entry yet) | `com.hakmicapitalgroup.animationcenter` | `paid`; commercial listing **prepared, not published** ([COMMERCIAL.md](COMMERCIAL.md)) |

HCG Sentinel has no jailbreak package. It is listed so that navigation between the store and the
repository works. It cannot be installed from HCG Repo.

A product page shows the strictest of the product's `access` and its packages' `[access] level`.

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
`<repo base>repo/` and everything under it is unchanged. Each package card, each "Sign-in required"
card and each "In development" card on the landing page links its product page ("Product page").

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
| `not-owned` | `paid` products: **Buy Product**, linking `buy_url` if it is under the store base URL, otherwise the store product page. `connect` products: the primary action stays **Sign in to check access**, with the store's message |
| `owned` | **Owned** badge. **Get from HCG Repo** is disabled with "Downloads are not available yet." unless `downloads_available` is `true`. Even then the page only explains how to add the source and install in Sileo; it never links a `.deb` |
| `not-for-sale` | **Not for sale yet** (disabled) |
| `unavailable`, network or CORS error, timeout, non-JSON | The default links stay, plus "Access could not be checked right now." |

The script uses only URLs from the response that start with the configured store base URL. It puts
no token in any URL, stores nothing, and uses no inline code, so it works under a strict CSP. The
store must allow this site's origin with credentials in its CORS policy for the check to work.

A product page offers **Install in Sileo** only for a package that is in the generated index: a
`public` package, or a protected one indexed as `cydia::commercial` once `[payment_provider] advertise = true`
is set ([ACCESS.md](ACCESS.md)). Otherwise it says "Downloads are not available yet." A product page
never links a `.deb`. `verify` checks all of this on every build.

## Payment provider candidate

[ACCESS.md](ACCESS.md) describes the Sileo Payment Provider API that HCG Connect must provide, and
`[payment_provider] advertise = true` in `config/repo.toml` that enables it. HCG Store's server implements
that API at `https://store.hakmicapitalgroup.com/repo-api/v1/sileo/` (Store repository:
`web-hcg-store/server/src/sileo.ts`, documented in its `docs/HCG-REPO.md`). It is **not enabled**.
It is the candidate value for `[payment_provider] advertise = true`, pending founder approval. The endpoint is
not set here.

## What remains

1. **Protected delivery.** Sign-in, ownership and signed downloads go through the Store's payment provider
   ([ACCESS.md](ACCESS.md)). How approved Store releases reach the index, what is refused, the staging
   results, the blockers and the companion changes are in [STORE_RELEASES.md](STORE_RELEASES.md).
   Until then, protected packages stay out of the public index.
2. **Store go-live.** The Store, its sign-in, its access endpoint and its CORS policy for this site
   must be live before any of the Store links work.
3. **HTTPS.** Done for HCG Repo: the domain serves a valid certificate and Enforce HTTPS is on
   ([TRANSPORT.md](TRANSPORT.md)). The Store must also be served over https, because Sileo refuses
   non-https payment endpoints and download URLs.

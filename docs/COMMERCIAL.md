# Commercial releases (HCG Store products in the main HCG Repo)

**Status (21 Nov 2026): HCG Animation Center is prepared as a commercial prerelease at $1.99 USD. Nothing is
published or activated.** The listing is `prepared` and is not rendered on the public site. Purchases, downloads and
installation are disabled; the commercial release is not approved; HCG Store readiness is pending.

## Architecture this builds on (nothing duplicated)

| Concern | Where it lives | Authority |
|---|---|---|
| Product registry for HCG Repo | `config/products.toml` (one entry per product; `[products.commercial]` holds the commercial settings) | HCG ERP is the source of truth for products; HCG Repo mirrors it ([STORE_INTEGRATION.md](STORE_INTEGRATION.md)) |
| Who may download a package | `packages/<id>/package.toml` `[access] level` ([ACCESS.md](ACCESS.md)) | HCG Repo |
| Paid package files | Never in this repository or on the public site: lock status `protected`, file outside Git | HCG Repo (containment) |
| Index entries for paid builds | Only with `[access] payment_endpoint` set **and** an HCG Store release approved for public delivery ([STORE_RELEASES.md](STORE_RELEASES.md)) **and**, for a commercial product, every commercial gate open | HCG Repo build + verify |
| Identity, sign-in | HCG Connect (central) | HCG |
| Prices actually charged, checkout, payments | HCG Store server: `server/data/offers.json` (server-authoritative, empty), signed provider webhooks | HCG Store |
| Entitlements, licences, refunds, revocation | HCG central entitlements; HCG Store reports verified purchases through its grant outbox | HCG |
| Package delivery | HCG Store's Sileo payment provider: `authorize_download` returns a short-lived signed URL after a server-side ownership check | HCG Store |

HCG Repo is a static site. Its "backend" enforcement is the build and `verify`, which run in **Publish** before and
after every deployment: a state the settings do not allow cannot be built, and a deployed state that breaks them fails
verification. HCG Repo stores no accounts, passwords, payment data or entitlements, and trusts no client-side state.

## The commercial settings

Under the product in `config/products.toml`:

| Setting | Values | Initial | Enforced by |
|---|---|---|---|
| `listing` | `prepared` · `approved` · `withdrawn` | `prepared` | build renders a product page and home-page card only when `approved`; verify fails if a non-approved listing is served |
| `purchases` | `disabled` · `enabled` | `disabled` | product page has a disabled purchase control and **no** HCG Store access check (no script, no Buy link); `store-access.js` refuses to upgrade a disabled page; HCG Store refuses checkout without a server-side offer |
| `downloads` | `disabled` · `enabled` | `disabled` | HCG Store's `authorize_download` (protected downloads off by default); HCG Repo never serves the file |
| `installation` | `disabled` · `enabled` | `disabled` | build indexes the paid build only when enabled (with all other gates); verify fails if the index lists it otherwise |
| `commercial_release` | `not-approved` · `approved` | `not-approved` | required before purchases, downloads or installation |
| `store_readiness` | `pending` · `ready` | `pending` | required before purchases, downloads or installation |
| `approved_version` | exact Debian version | empty | must be a **protected stable** build in `repo-lock.json`, never a free candidate demo; only this version is indexed |
| `price`, `currency`, `price_note` | `1.99`, `USD`, `Introductory Prerelease Price` | as listed | shown on the listing; the Store's offer sets the price charged |
| `release_channel` | `commercial-prerelease` · `commercial-release` | `commercial-prerelease` | shown on the listing |

**Every value other than the initial one needs an owner approval record** next to it:

```toml
[[products.commercial.approvals]]
setting = "listing"
value = "approved"
approved_by = "owner"
date = "2026-11-21"
record = "<the owner's words, or a link to the approving PR comment>"
```

`validate` (run on every push) and the build refuse a missing record (`commercial-unapproved`) and an out-of-order
state (`commercial-order`), for example purchases before the listing, before HCG Store is ready or before the release
is approved; installation without downloads; installation without the payment endpoint. Changing any setting is a
reviewed Git change, so the history of `config/products.toml` is the audit log: who changed what, when, and the approval
it cites. **A successful CI build never changes a setting.**

`bin/hcgrepo commercial [<key>]` prints every setting, its approval, the recorded builds, whether the listing is
shown, whether a version is indexed, and the next owner step. It changes nothing.

## What visitors see

With the listing approved and purchases disabled, `products/<key>/` and a **Coming soon to HCG Store** card on the home
page show the name, the summary, **$1.99 USD · Introductory Prerelease Price**, **Coming Soon**, publisher, category,
release channel, *Purchases: Disabled*, *Installation: Disabled*, and:

> Purchasing and installation will become available once the HCG Store payment and licensing infrastructure is ready.

There is a disabled **Purchase unavailable · Coming Soon** control and a plain link to the future HCG Store product page.
There is no install action, no `.deb` link, no add-source link, and no pointer to the free candidate demo.

To review a prepared listing before approving it, build a preview:

```sh
bin/hcgrepo build --target production --out /tmp/preview --preview-listings
bin/hcgrepo serve --target production --dir /tmp/preview
```

A preview marks each prepared listing as *Preview… awaiting the owner's approval*, sets `noindex`, and writes
`PREVIEW-NOT-FOR-DEPLOYMENT.txt`. `verify` (and so Publish's post-deployment check) fails on a preview build.

## Promotion workflow: Candidate → Validation → Owner approval → Main listing → HCG Store activation → Commercial release

1. **Candidate.** Builds are tested in HCG Repo Candidate. A free candidate demo of a paid product
   (`exception_versions`) stays in the candidate source only (`free-demo-stable`) and can never be the commercial
   build (`commercial-version`).
2. **Validation.** The commercial build is a separate version (for example `0.1.0`, which sorts after every
   `0.1.0~betaN`). It is imported from a pinned CI artifact through **Prepare packages**; as a `paid` package it is
   recorded as `protected`: control data, size and SHA-256 in `repo-lock.json` and provenance, the file **not** stored.
3. **Owner approval of the listing.** `listing = "approved"` with its approval record, merged by PR, then a manual
   **Publish**. The product page and the home-page card appear; nothing can be bought or installed.
4. **HCG Store activation (in HCG Store).** The Store's own owner-controlled steps (below). Then
   `store_readiness = "ready"` here, with its approval.
5. **Commercial release.** HCG Store approves the release for public delivery to HCG Repo; record it with
   `bin/hcgrepo import-store-release`. Then, each with its approval record: `approved_version`,
   `commercial_release = "approved"`, `purchases = "enabled"`, `downloads = "enabled"`, `installation = "enabled"`, and
   `[access] payment_endpoint` in `config/repo.toml`. Merge, run **Publish**. The approved version appears in the main
   index as `Tag: cydia::commercial`, `Filename: protected/…`; Sileo asks the Store's payment provider, which checks
   sign-in and ownership server-side and returns a short-lived signed URL. The file is never on the public site.

Withdrawing: set `installation`, `downloads` or `purchases` back to `disabled` (no approval record is needed to close a
gate), or `listing = "withdrawn"`, and Publish. Version history, checksums, release notes and candidate test records
are never rewritten; `repo-lock.json` is append-only for published versions.

## HCG Store readiness (assessed 21 Nov 2026, store `835ac1a`)

| Capability | State in HCG Store | Needed before activation |
|---|---|---|
| Product pricing | `server/data/offers.json` is **empty on purpose**; an offer can be `approved-for-sale` only for an edition with an approved current release (no preorders) | A `$1.99 USD` offer for HCG Animation Center, added only at activation |
| Payment provider | **Test mode only**; no live provider is implemented or approved | Founder choice of provider (merchant of record), implementation, test-mode acceptance run, founder approval |
| Purchase confirmation | Only a verified, signed provider webhook creates a purchase; return/success URLs grant nothing | (implemented) |
| Entitlements | Central HCG grants through the grant outbox; endpoint is PROPOSED (decision D5) | HCG's central grant endpoint and credentials |
| Licence activation / validation | Central HCG licence scopes per edition (D2) | Central product ID and edition key for Animation Center |
| Refunds, revocation | Refunds and chargebacks revoke exactly the grants of that purchase | (implemented) |
| Authorised delivery | Sileo payment provider (`/repo-api/v1/sileo/`), short-lived session-bound URLs, re-checked ownership; `protectedDownloads` off by default and refused without sign-in, an authority, private storage and founder approval | Private storage holding the exact approved file; founder approval to enable |
| Purchase / install history | Store library and central grants | (implemented) |
| Catalogue | **No `hcg-animation-center` entry yet** | Catalogue entry with the same key (`productId`) as `store_slug` here, and its central ID |

The Store's acceptance and configuration suites pass on `835ac1a` (41 tests). They cover no download without a
session, no download without an entitlement, forged success URLs, client-supplied prices, an HCG outage treated as
neither a grant nor a denial, and "Downloads are not available yet." with the launch switch off.

## Limitations

- **The free candidate demo is public.** `com.hakmicapitalgroup.animationcenter 0.1.0~beta1` is a free public demo in
  HCG Repo Candidate (owner-approved, 18 Nov 2026), downloadable by anyone who adds that source, and its `.deb` is in
  this public Git repository's history. Anyone who installed it keeps it. It is a different version from the
  commercial build; the commercial build itself is never published as a file.
- **Package-manager caches.** Sileo and Zebra keep the last `Packages` they fetched. Closing a gate takes effect for a
  device on its next refresh; a device that already downloaded a commercial `.deb` keeps that file.
- **CDN caching.** GitHub Pages serves with short cache lifetimes; Publish's post-deployment check waits for the new
  revision, but intermediaries can briefly serve the previous one.
- **Mirrors and copies.** A paid file delivered to a legitimate buyer can be copied by that buyer. Ownership checks
  protect delivery, not redistribution.
- **Public source code.** HCG-Jailbreak-Research is public, so anyone can build the tweak from source; payment
  protects the official signed builds and their updates.

## Exact manual approval steps

Each step is a separate owner decision. Nothing happens automatically, and no step implies the next.

1. **Approve the listing text and price.** Review the preview (`--preview-listings`) and the screenshots in the PR.
2. **Publish the listing.** In `config/products.toml` (`hcg-animation-center`): `listing = "approved"` plus its approval
   record; merge the PR; run **Actions → Publish repository** (target `production`, confirm `PUBLISH`). Check
   `products/hcg-animation-center/` shows the price and *Coming Soon* and the main `Packages` has no Animation Center
   entry (Check transport).
3. **HCG Store (in the Store repository and deployment, founder-approved):** catalogue entry `hcg-animation-center`
   with its central ID; approved payment provider; HCG Connect and the central grant endpoint configured; the
   commercial build approved as a release for public delivery to HCG Repo and stored privately; a `$1.99 USD` offer;
   protected downloads enabled.
4. **Record readiness here:** `store_readiness = "ready"`.
5. **Import the commercial build** (a separate version, e.g. `0.1.0`) from its pinned CI artifact via Prepare packages,
   then `bin/hcgrepo import-store-release` with the Store's approved release record.
6. **Approve the release:** `approved_version = "<version>"` and `commercial_release = "approved"`.
7. **Activate:** `purchases`, `downloads`, `installation` = `"enabled"` and `[access] payment_endpoint` =
   `"https://store.hakmicapitalgroup.com/repo-api/v1/sileo/"`. Merge and Publish. Confirm with Check transport that the
   index lists exactly the approved version as `cydia::commercial` and that `protected/` paths return 404.

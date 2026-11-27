# HCG Store handoff: HCG Animation Center commercial prerelease

**For:** the HCG Store repository (`F1r3srp3nt/store`, `web-hcg-store/`), checked against `835ac1a` (21 Nov 2026).
**Status:** a plan. **Nothing here is applied or activated.** HCG Repo shows a *Coming Soon* listing only (owner
approval of 21 Nov 2026, [COMMERCIAL.md](COMMERCIAL.md)). Purchases, downloads, installation and the commercial
release are **not approved**. Each Store step below is a founder decision, made in the Store repository by a reviewed
PR. HCG Connect, HCG's central registry, entitlements and licences stay the only authorities: the Store and HCG Repo
add no identity, payment or licensing system of their own.

## 1. Identifiers

| Item | Value | Where it is defined |
|---|---|---|
| Debian package ID | `com.hakmicapitalgroup.animationcenter` (unchanged since 0.1.0~beta1) | the `.deb`; HCG Repo `config/products.toml` `package_ids` |
| HCG Repo product key | `hcg-animation-center` | HCG Repo `config/products.toml` `key` |
| Store product ID / slug (proposed) | `hcg-animation-center` | HCG Repo `store_slug`; to be created in Store `catalogue/products.json` |
| Store edition | none (`editionId: null`) | HCG Repo has no `store_edition`; `check:repo-mapping` requires both sides to match |
| HCG central product ID | **not assigned** | HCG ERP (`registry.centralId`); required before any offer |
| Central edition key | **not assigned** | HCG ERP (`centralEditionKey`, licence scope `edition:<key>`) |
| Layout | `iphoneos-arm64` (rootless) | the `.deb` |

## 2. Price

**$1.99 USD, introductory prerelease.** HCG Repo only *displays* it. The price charged comes only from the Store
server's `server/data/offers.json` (`currency: "USD"`, `unitAmount: 199`, minor units). Client-supplied prices are
ignored (acceptance test "ignores client-supplied prices").

## 3. Store catalogue entry (required first)

Add to `catalogue/products.json` (schema: Store `docs/CATALOGUE.md`; `npm run validate:catalogue`), stating only what is
true today:

```json
{
  "id": "hcg-animation-center",
  "slug": "hcg-animation-center",
  "name": "HCG Animation Center",
  "monogram": "AC",
  "summary": "Plays your own videos and GIFs as animated backgrounds behind the Home Screen icons of jailbroken iPhones.",
  "description": ["…same facts as the HCG Repo listing; no device compatibility claim…"],
  "category": "unclassified",
  "developmentStatus": "testing",
  "platforms": [{ "platform": "ios", "support": "in-development", "note": "Not verified on a device." }],
  "editions": [{
    "id": "jailbreak", "name": "HCG Animation Center jailbreak package", "platforms": ["ios"],
    "channel": "jailbreak-package", "status": "Free candidate beta 0.1.0~beta1 in HCG Repo Candidate; commercial build not released.",
    "identifiers": [{ "kind": "debian-package", "value": "com.hakmicapitalgroup.animationcenter" }],
    "layouts": [{ "architecture": "iphoneos-arm64", "label": "Rootless", "note": "Not verified on a device." }],
    "centralEditionKey": null
  }],
  "hcgRepo": [{ "key": "hcg-animation-center", "editionId": null }],
  "registry": { "status": "provisional", "centralId": null, "references": [] },
  "currentReleaseId": null
}
```

- **Category:** the Store taxonomy has no customization/jailbreak-tweaks category. Either the founder adds one (`src/domain/taxonomy.ts`)
  or the entry uses `unclassified` ("Category to be confirmed") until then.
- **Mapping:** with this entry, `npm run check:repo-mapping -- <HCG-repo checkout>` stops reporting
  `hcg-animation-center`. (On 21 Nov 2026 it also reports `hcg-vortex`, which has no Store page: pre-existing, unrelated.)
- **Store page:** once the page is live, set HCG Repo `store_page = "live"`; HCG Repo then links it (still no purchase action).

## 4. Payment provider integration (not implemented)

- The Store server accepts only `HCG_STORE_PAYMENT_PROVIDER = "none" | "test"`; **no live provider exists**, and choosing one
  (a merchant of record or HCG's own) is a founder decision (Store `docs/PAYMENTS.md`).
- A live provider must: create checkout sessions priced by the server from an `approved-for-sale` offer; deliver
  **signed** webhooks verified with the provider's own signature check; map its events to `checkout.completed`,
  `checkout.pending`, `checkout.failed`, refund, dispute and expiry. A redirect, success page or client claim never grants
  anything (implemented and tested for the test provider).
- Run the Store acceptance suite in the provider's test mode before any live use.
- No card data ever reaches HCG Repo or the Store database: the provider holds it.

## 5. Central HCG entitlements

- Each verified purchase becomes one central grant per granted edition
  (`store:<provider>:<environment>:<purchase>:<edition>`) through the Store's grant outbox (`grants.ts`), with only the
  features the offer lists. HCG's central grant endpoint is **PROPOSED (decision D5)** and must be configured with
  credentials HCG issues to the Store's environment.
- The offer (draft until activation) needs the central IDs:

```json
{ "id": "hcg-animation-center-intro", "productId": "hcg-animation-center", "centralProductId": "<from HCG ERP>",
  "editionId": "jailbreak", "title": "HCG Animation Center (introductory prerelease)", "kind": "one-time",
  "interval": null, "currency": "USD", "unitAmount": 199,
  "grants": [{ "edition": "jailbreak", "features": ["download"] }], "status": "draft", "testOnly": false }
```

  `status` may become `approved-for-sale` only for an edition with an approved current release; the server refuses to
  start otherwise (no preorders). `download` is used only once HCG registers that feature key (D3).

## 6. Licence validation

Ownership is decided by HCG's central entitlements, read server-side by the Store (`access.ts`, the
`hcg-ecosystem` authority) on every request. A Store session, an HCG account role, a client flag or a cached page is
never proof of purchase (acceptance tests 2 and 7). An HCG outage is neither a grant nor a denial: the Store answers
"could not be checked" and nothing is delivered (acceptance test "does not treat an HCG outage as a denial or a grant").
The tweak itself has no licence check and no network code; protection is at delivery.

## 7. Private package storage

- The commercial `.deb` (a new version, e.g. `0.1.0`, never the free `0.1.0~beta1`) is built by CI from a pinned commit,
  recorded in HCG Repo as `protected` (control data, size, SHA-256; file **not** in Git), and approved in HCG Store as a
  release for public delivery to HCG Repo.
- The file goes to `HCG_STORE_ARTIFACT_STORAGE_DIR` (a private directory, never a URL, never the public site) and is
  listed in `server/data/artifacts.json` with its `sha256`, `storageKey`, `releaseId` and `deliveryApproved` (a separate
  approval for that exact file). `artifacts.json` is empty today.
- Delete the CI artifact after import (HCG Repo's Prepare packages already does).

## 8. Authorised download flow (Sileo)

1. HCG Repo indexes the approved version as `Tag: cydia::commercial`, `Filename: protected/<file>` (never served) and
   publishes `repo/payment_endpoint` = `https://store.hakmicapitalgroup.com/api/sileo/v1/` ([STORE_PROVIDER.md](STORE_PROVIDER.md)).
2. Sileo signs in through the Store's `authenticate` (HCG Connect); the Store issues a revocable, Store-only token.
3. `package/<id>/info` reports price, `purchased`, `available`; `package/<id>/purchase` never grants and returns the
   Store's buy page.
4. `package/<id>/authorize_download` re-checks session, entitlement, release approval and delivery approval and
   returns a **short-lived, session-bound** HTTPS URL to the exact file; Sileo checks the SHA-256 against the index.
5. Downloads are off unless `HCG_STORE_PROTECTED_DOWNLOADS=enabled` **and**, in production,
   `HCG_STORE_LAUNCH_APPROVED=founder-approved`, with sign-in, an authority and private storage configured.
6. Zebra and plain APT are unsupported for paid packages (no payment-provider support in this flow).

## 9. Refunds and revocation

Refunds, chargebacks and subscription expiry revoke exactly the grants of that purchase (other valid purchases are
untouched); revocation applies to the next request, including the next `authorize_download`. HCG administrators can
revoke centrally. A file already downloaded stays on that device; a revoked user gets no further downloads or updates.

## 10. Activation prerequisites (all owner-approved, in this order)

1. Store catalogue entry (§3) and its live page; HCG Repo `store_page = "live"`.
2. HCG central product ID and edition key (HCG ERP).
3. Approved live payment provider (§4), tested in provider test mode against the acceptance suite.
4. HCG Connect production client and the central grant endpoint (§5).
5. Commercial build (new version) imported into HCG Repo as protected; Store release approved for delivery; file in
   private storage with `deliveryApproved` (§7).
6. `$1.99 USD` offer set to `approved-for-sale`; protected downloads enabled with founder launch approval (§8).
7. In HCG Repo, each with an approval record: `store_readiness = "ready"`, `approved_version`,
   `commercial_release = "approved"`, `purchases`, `downloads`, `installation` = `"enabled"`, and
   `[payment_provider] advertise = true`. Then Publish ([COMMERCIAL.md](COMMERCIAL.md), steps 3–7).

# HCG Store handoff: Sileo qualification (from HCG Repo, 28 Nov 2026)

What HCG Store needs to provide for the owner's physical Sileo qualification (HCG-repo
`docs/QUALIFICATION_SILEO.md`). HCG Repo changes nothing on HCG Store; each item is HCG Store's own decision and
review. Live state checked on 28 Nov 2026 is noted.

| # | Item | Exact requirement | Live, 28 Nov |
|---|---|---|---|
| 1 | Provider endpoint | Payment-provider API at `https://store.hakmicapitalgroup.com/api/sileo/v1/`. Patch `0002-provider-api-sileo-v1.patch` (same handlers; `/repo-api/v1/sileo/` stays an alias). Apply `0001-repo-profile-and-ranges.patch` too (profile endpoint, resumable downloads) | `/api/sileo/v1/info` → 404; `/repo-api/v1/sileo/info` → 200 |
| 2 | Provider profile (branding) | `info`: `name` "HCG Store", the official icon (`https://…/brand/icon-512.png`, square PNG), final description, `authentication_banner` {message, button} | name, icon, description and banner present |
| 3 | Catalogue product | Product `hcg-repo-qualification` (TEST; storefront visibility as HCG Store decides, but importable as a catalogue product). Edition `jailbreak`, platform `ios`, identifier `{kind: "debian-package", value: "com.hakmicapitalgroup.qualification.paid"}`, `hcgRepo: [{key: "hcg-qualification", editionId: "jailbreak"}]`. Central product and edition registered with the HCG entitlement authority, so a sandbox purchase creates a real (test) entitlement | release-resolution → 404 "Unknown product" |
| 4 | Release | Ship to Store: version `1.0.0`, build `1`, channel `stable`, distribution `paid`, destinations `["hcg-repo"]`. One artifact: `com.hakmicapitalgroup.qualification.paid_1.0.0_iphoneos-arm64.deb`, SHA-256 `9c5820dc1cc3cdbca88cb312b3d772dc2e9ffd0476d4f4e2a5b20a358c71803b`, 1,082 bytes, architecture `iphoneos-arm64`. Rebuild it with `tools/qualification/build_packages.py` to confirm the bytes. Approved by a second person with eligibility **`internal-testing`**, then published as current on (ios, iphoneos-arm64, stable) | not present |
| 5 | Sandbox offer | One-time offer for edition `jailbreak`, e.g. EUR 0.99, **Stripe test mode through HCG Payments only**, **restricted to the owner's tester HCG account(s)** so nobody else can obtain it with a test card. Webhook signature verification and idempotency as for live; no live keys | payments `disabled` |
| 6 | Delivery | Protected delivery allowed for this product (today `protectedDownloads: free-testing-only`). `authorize_download` must re-check the entitlement and serve only the exact artifact | not allowed |
| 7 | For HCG Repo | The release record JSON (`GET /api/v1/publishing/releases/<id>`, saved unchanged) and the artifact id, so HCG Repo can run `import-store-release` in its qualification source | — |
| 8 | Evidence | During the device test, the purchase, entitlement, `authorize_download` and file-delivery log lines for the tester's account (no tokens), and their absence for a second account | — |

The free qualification package needs nothing from HCG Store. Sign-in uses the production HCG Connect client HCG
Store already has (`hcgConnect: configured`).

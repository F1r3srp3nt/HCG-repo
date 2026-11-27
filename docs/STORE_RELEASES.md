# HCG Store releases → HCG Repo

**Status (16 Nov 2026): implemented and tested in staging only. Nothing is live.**
- HCG Store is not deployed.
- Its "Ship to Store" publishing work is on the unmerged branch `publishing/ship-to-store` of
  `F1r3srp3nt/store`, tested at commit `10b2365`.
- HCG Executable Maker's side is on the unmerged branch `distribution/ship-to-store`.
- Production `[payment_provider] advertise` stays `false`, so the public index lists no protected package ([STORE_PROVIDER.md](STORE_PROVIDER.md)).

## What is connected, and how

HCG Repo reuses the Store's contracts. It does not copy them or keep a second product registry.

| Step | Who | Contract |
|---|---|---|
| 1. Build | HCG Executable Maker (Theos) or HCG-repo's own build recipes | A real `.deb` |
| 2. Ship | Executable Maker | Store publishing API (`server/src/publishing.ts`): draft release → declared artifact → scoped, resumable upload → Store-side verification (SHA-256, format, control fields, catalogue mapping) → submit |
| 3. Approve | A person in HCG Store, not the person who created the release | `POST /api/v1/publishing/releases/<id>/approve` with eligibility `public` |
| 4. Record | An HCG Repo maintainer | `bin/hcgrepo import-store-release --release <record.json> --artifact <art-id> --deb <file>` |
| 5. Publish | An HCG Repo maintainer | The manual **Publish repository** workflow on `main`. Protected entries appear only while the payment provider is advertised |
| 6. Deliver | HCG Store | Sileo payment-provider API (`server/src/sileo.ts`). `authorize_download` re-checks the session, the entitlement, release approval and the launch switch, then returns a 120-second signed URL |

For step 4:
- `<record.json>` is the Store's own response to `GET /api/v1/publishing/releases/<id>`, saved unchanged. It is the record a publisher or approver sees.
- `<file>` is the artifact's bytes, downloaded with the Store's authorised **Download to review**.

**Why it is pinned by SHA-256.** Sileo checks every downloaded `.deb` against the SHA256/SHA512 in
the index (Sileo `DownloadManager`) and refuses a mismatch. So HCG Repo may index a Store-held
package only if it is exactly the file the Store approved.

## What `import-store-release` refuses

Each refusal has an error code.
- **Release state:**
  - draft or submitted: `store-release-not-approved`;
  - withdrawn: `store-release-withdrawn`;
  - approved only for `private` or `internal-testing`: `store-release-not-public`;
  - not shipped to HCG Repo: `store-destination`;
  - a Store draft product: `store-draft-product`.
- **Artifact:**
  - not verified, or rejected: `store-artifact-not-verified`;
  - the Store recorded problems: `store-artifact-problems`;
  - SHA-256 unconfirmed or missing: `store-artifact-sha256`;
  - not an iOS jailbreak `.deb`: `store-artifact-kind`.
- **Identity:**
  - the declared control fields differ from what the Store read: `store-control-mismatch`;
  - an architecture this repository does not serve: `store-architecture`;
  - a Debian version the Store's `authorize_download` would never match: `store-version`. It must equal the release's SemVer, or be `<semver>-<revision>`.
- **Mapping**, against `config/products.toml`, which the Store's `check:repo-mapping` cross-checks:
  - `store-unmapped`;
  - `store-product-mismatch`;
  - `store-edition-mismatch`.
- **Access.** Importing never changes who may download, so a mismatch is refused, not resolved:
  - `connect` (sign-in only) requires Store distribution `free`;
  - `paid` requires `paid`, `subscription` or `entitlement`;
  - a `public` package is never Store-held (`store-access-public`).
- **File:** identity, size or SHA-256 differs from the Store artifact (`store-file-*`).

**What gets recorded.** An accepted artifact is recorded as a **protected** lock entry: control data and
checksums only. The `.deb` is never stored in this repository or on the public site. If the package
was already recorded from its own build, that provenance is kept and the Store link is added as
`hcg_store`.

**Index rule.** With the payment provider advertised, a protected package is indexed (as `cydia::commercial`,
with a `Filename` that is never deployed) **only if it has such a Store link**. Without one, Sileo
would list a package that no one can download.

**Withdrawal.** If the Store withdraws a release, a new record shows `withdrawn` and
`import-store-release` refuses it with `store-release-withdrawn`. Run `bin/hcgrepo withdraw` for that
version. Withdrawal is manual today; see companion change 5.

## Product, edition and package mapping

| HCG Repo product | Store product / edition | Package | HCG Repo access | Store distribution that matches |
|---|---|---|---|---|
| `hcg-nexus` | `hcg-nexus` / `jailbreak` | `com.hakmicapitalgroup.hcgnexus` | `paid` | `paid`, `subscription` or `entitlement` |
| `hcg-terminal-unleashed` | `hcg-terminal` / `unleashed` | `com.hakmicapitalgroup.hcgterminal.unleashed` | `connect` | `free` |
| `hcg-sentinel` | `hcg-sentinel` | none | none | none |

There is **no approved free test-package policy** in HCG Repo, the Store, Executable Maker or
HCG-Server, and none was created. Access levels are unchanged.

## Staging verification (automated, no device)

`tools/staging/store_e2e.py` runs HCG Store's own server code in its TEST mode, with its mock
HCG Connect provider and TEST entitlement ledger. It then:
1. ships a real package the way Executable Maker does;
2. has a second person approve it;
3. records it in a **copy** of this repository;
4. builds a staging source;
5. acts as Sileo, following Sileo's source:
   - refresh;
   - provider `info`;
   - sign-in to `sileo://authentication_success`;
   - `user_info`, package `info` and `authorize_download`;
   - download and hash check;
6. checks each denial:
   - signed out;
   - another account;
   - revocation, including a link issued before it;
   - Store withdrawal;
   - Sileo sign-out;
   - the public `protected/` path.

The real products have no central IDs yet, so the staging catalogue copy uses `staging-only.*` IDs.
Nothing is written back to the Store.

| Package | Result | Report |
|---|---|---|
| HCG Nexus 0.2.0 (`paid`, iphoneos-arm64) | **38 of 38 passed** | [staging-reports/2026-11-16-nexus.md](staging-reports/2026-11-16-nexus.md) |
| HCG Terminal Unleashed 0.1.0-1 (`connect`) | **35 of 38.** The 3 failures are companion change 1 | [staging-reports/2026-11-16-terminal.md](staging-reports/2026-11-16-terminal.md) |

To run it:

```
(cd <store>/web-hcg-store && npm ci)
python3 tools/staging/store_e2e.py --store-dir <store>/web-hcg-store --deb <file.deb> --out <dir>
```

**Not covered:**
- Sileo on a device.
- The real HCG Connect, real entitlements, real payments.
- https. The staging servers are http on 127.0.0.1, and Sileo refuses non-https payment endpoints and download URLs.

**Device compatibility.** HCG Nexus targets the iPhone 13 Pro on iOS 16.5 (Dopamine, rootless), so it
is the package to test on a device. Terminal Unleashed requires iOS 17 or later.

## Blockers before any real download

1. **HCG Store is not deployed.** Ship to Store is not merged in the Store or in Executable Maker.
2. **No central IDs.** HCG has not issued central product IDs for `hcg-nexus` and `hcg-terminal`
   (`centralId: null`), so no one can own them. HCG has also not issued the publisher and approver
   permission IDs.
3. **No HCG Connect clients.** None are registered for the Store or for Executable Maker.
4. **Launch approval.** The founder has not approved the Store's launch conditions (its
   `docs/ACCESS.md`), and protected downloads are off.
5. **Sign-in only does not work through Sileo.** The Store's Sileo path does not support HCG Repo's
   `connect` level (companion change 1).

## Companion changes required

These are proposed for review. None was made in those repositories.

**HCG Store** (`web-hcg-store/server/src`):
1. **Honour sign-in-only packages in the Sileo path.**
   - `decideAccess` (`access.ts`) grants `owned` only from a central entitlement.
   - The Store's own publishing downloads already allow a `public` + `free` release to any signed-in account (`publishing.ts`, basis `public-free`).
   - `sileo.ts` (`user_info`, `package/<id>/info`, `authorize_download`) and `serveFile` should apply the same rule. A signed-in session should count as entitled to an edition that has a public, approved, `free` release. Revocation would still apply through session end and release withdrawal.
   - Evidence: the 3 failing Terminal checks.
2. **Check the release version against the Debian version at upload.**
   - The Store verified a `.deb` whose control says 0.2.0 inside a release numbered 0.2.1 (staging report, second draft).
   - `authorize_download` would then never find it.
   - `completeUpload` should require the Debian version to be the release version, or `<version>-<revision>`.
3. **Add a read-only release listing for HCG Repo.**
   - Today only publishers and approvers can read a release, one at a time (`GET /api/v1/publishing/releases/<id>`).
   - Proposed: `GET /api/v1/repo/releases?destination=hcg-repo`, returning releases that are approved for `public` or withdrawn, with the artifact id, `sha256`, size, architectures and control triple.
   - It should be authorised by a central permission for HCG Repo maintainers.
   - That lets HCG Repo detect new approvals and withdrawals instead of relying on a saved record.
4. **Correct the documentation.**
   - `docs/HCG-REPO.md` refers to HCG Repo's `[payments] endpoint`. The setting is `[payment_provider]` (formerly `[access] payment_endpoint`).
   - Its "remaining work 1" (protected index entries) now exists in HCG Repo.
5. **Withdrawal signal.** Either the listing above or a webhook, so that a Store withdrawal reaches HCG Repo without a manual step.
6. **Deploy on https.** Sileo refuses a provider endpoint or download URL that is not https (`PaymentManager`, `PaymentProvider.downloadURL`).

**HCG Executable Maker** (`distribution/ship-to-store`):

7. Its end-to-end script ships only a Linux `amd64` `.deb`. Add a run with an `iphoneos-arm64`
   `.deb` and `--hcg-repo`. It reads control fields only from gzip control archives; HCG's current
   packages use `control.tar.gz`, but other Theos or dpkg setups can produce `xz` or `zst`.

**HCG central backend:**

8. Issue central product IDs for `hcg-nexus` and `hcg-terminal`. Issue the publisher and approver
   permission IDs. Register the Store's and Executable Maker's HCG Connect clients. These are founder
   decisions listed in the Store's own `docs/PUBLISHING.md`.

The repository `F1r3srp3nt/HCG-Server` was checked and is unrelated. It is a device-pairing and
service agent, with no product registry, entitlements or HCG Connect issuer.

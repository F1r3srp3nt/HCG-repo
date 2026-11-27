# HCG Repo Distribution Infrastructure v2

**Status (25 Nov 2026):** implemented on `main`, nothing published by these changes. Stable and Candidate URLs,
package identifiers, published bytes and checksums are unchanged. Integration round 2 (this page's later sections)
consolidated the two local import routes, added the Executable Maker contract, the six evidence levels, the Release
Passport, the public compatibility API (preview, off) and the local administration dashboard. Its only change to the
generated site is the corrected HCG Sentinel product entry (section 6).

v2 adds what HCG Repo needs to accept packages from more than GitHub Actions, without loosening any existing rule:

| Area | What exists now | Where |
|---|---|---|
| Artifact providers | GitHub Actions (public and private), local HCG Executable Maker builds, HCG Server Build Host, approved developer uploads | `tools/hcgrepo/importer.py`, `submission.py` |
| Local import contract | `hcg.repo.artifact-submission` v1 manifest; adapter for Executable Maker's Unleashify handoff | `submission.py`, [schema](schemas/artifact-submission-v1.schema.json) |
| Candidate Release Manager | `bin/hcgrepo release submit / incoming / show / approve / prepare / withdraw` | `release.py` |
| Validation engine | secrets, maintainer-script behaviour, privileged helpers, simulated dpkg lifecycle | `deepcheck.py`, `bin/hcgrepo inspect --deep` |
| Compatibility registry | evidence-based status per version; public API (off) | `compat.py`, `bin/hcgrepo compat [--public]` |
| Executable Maker contract | submission folder, `submission.json`, CLI, machine-readable result | [EXECUTABLE_MAKER_SUBMISSION.md](EXECUTABLE_MAKER_SUBMISSION.md) |
| Release Passport | `hcg.release-passport` v1 per release | [RELEASE_PASSPORT.md](RELEASE_PASSPORT.md), `bin/hcgrepo passport` |
| Administration dashboard | local page over the same engine | [ADMIN_DASHBOARD.md](ADMIN_DASHBOARD.md), `bin/hcgrepo admin` |
| Inspect artifact | review a pinned CI artifact, report its exact SHA-256, commit nothing | `.github/workflows/inspect-artifact.yml`, `bin/hcgrepo inspect-artifact` |

The separation of **Prepare packages** and **Publish repository** is unchanged. No step uploads, publishes, approves
or promotes anything on its own.

## 1. Artifact providers

Every provider ends in the same importer (`import_deb`) and the same checks; they differ only in how the exact bytes
are obtained. Provenance records the provider; a run ID exists **only** for GitHub Actions builds.

| Provider (`build.provider`) | How the bytes arrive | Import request `kind` |
|---|---|---|
| `github-actions` | workflow-run artifact, public repository | `github-artifact` (unchanged) |
| `github-actions-private` | workflow-run artifact, private repository, read with the `HCG_IMPORT_TOKEN` secret (read-only, `actions:read` + `contents:read`). The source repository stays private; a 404 from GitHub now says which token is missing. | `github-artifact` |
| `executable-maker-local` | the owner's machine: `bin/hcgrepo release submit --handoff <folder>`; private store by default | `local-submission` (imported locally by `release prepare`, the default) or `local-file` (`release prepare --via local-file`: the approved bytes are staged under `imports/local/` and Prepare packages imports them in CI) |
| `hcg-server-build-host` | authorized HTTPS download; the token comes from a CI secret named by `auth_env` (`HCG_…`), never stored in Git, never sent to a redirect | `https-artifact` |
| `developer-upload` | as above, from HCG Server's upload storage, after review | `https-artifact` |

**Both local routes share one validation engine, one approval and one provenance record** (PR #20, merged after
reconciliation). The Release Manager validates in the private store. The owner approves the exact SHA-256 once, and
that approval in `submissions/<id>/review.json` is what `local-submission`, `local-file` and `https-artifact` all
require. `submission.provenance()` writes the same provenance for every route, recording which route was used.
`validate` refuses any package file in `imports/` that is not an approved `local-file` named by its request, and any
package file at all in `submissions/`. So no unapproved package ever enters Git.

Rules enforced in code:

- `local-submission`, `local-file` and `https-artifact` need the approved `sha256`, a `submission` path, and the owner's approval of
  that exact SHA-256 in `submissions/<id>/review.json`. A `run_id` is refused for them, so a local build can never
  carry an invented CI run. Both import to **Candidate only**.
- `https-artifact` needs `https://`, the exact `size` (download stops beyond it), and refuses redirects to plain HTTP.
- In CI, a `local-submission` request whose bytes are only on the owner's machine reports
  `AWAITING-LOCAL-IMPORT` instead of failing. Normally it is already done: `release prepare` imports it locally and the
  request file is the record.
- Every request-driven import (all kinds, including the existing GitHub ones) now also runs the v2 validation engine
  and is rejected on any error. All 20 packages already in the pool pass it.

## 2. Local artifact import contract (manifest v1)

A submission manifest is JSON ([schema](schemas/artifact-submission-v1.schema.json)):

```json
{
  "format": "hcg.repo.artifact-submission", "formatVersion": 1,
  "package":  { "identifier": "com.hakmicapitalgroup.example", "version": "0.1.0~beta1",
                "architecture": "iphoneos-arm64", "name": "HCG Example" },
  "artifact": { "filename": "com.hakmicapitalgroup.example_0.1.0~beta1_iphoneos-arm64.deb",
                "sha256": "<64 hex>", "size": 123456 },
  "compatibility": { "minimumOS": "17.0", "environment": "rootless", "jailbreaks": ["Dopamine"] },
  "source":   { "repository": "F1r3srp3nt/HCG-Example", "commit": "<40 hex>", "dirty": false, "branch": "main" },
  "build":    { "provider": "executable-maker-local", "toolchain": "Xcode 16.2, HCG Executable Maker 1.x",
                "builtAt": "2026-11-25T09:00:00Z" },
  "dependencies": ["firmware", "uikittools"],
  "maintainerScripts": { "declared": ["postinst", "postrm"] },
  "validation": { "tool": "HCG Executable Maker", "checks": [ { "check": "…", "status": "Passed", "detail": "" } ] },
  "channel": "candidate"
}
```

GitHub builds add `build.runId`, `build.runRepository`, `build.artifactName` (and `runUrl`); every other provider must
leave them out. The source must be a full commit SHA with `dirty: false`. `channel` can only be `candidate`.

**Everything in the manifest is a claim.** HCG Repo reads the package itself and compares identifier, version,
architecture, SHA-256, size, minimum iOS (app `MinimumOSVersion`, Mach-O deployment targets, `firmware` dependency),
dependencies and maintainer scripts. A mismatch refuses the submission whatever the provider's own report says.

**Executable Maker handoff.** `release submit --handoff <name>_<version>-candidate-handoff/` converts Unleashify's
`provenance.json`, `SHA256SUMS` and `control.txt` into a v1 manifest. It copies only facts the handoff records:
a handoff with no `ci_run_id` becomes `executable-maker-local` with no run ID. The handoff does not record
maintainer scripts or named jailbreaks, so those stay undeclared (a warning) and HCG Repo reads them from the package.
Coordination for Executable Maker: emitting a native `submission.json` (this format) next to `provenance.json`, with
`maintainerScripts.declared` and tested jailbreaks, removes those warnings. No Maker change is required to use v2.

## 3. Candidate Release Manager

```
bin/hcgrepo release submit --handoff <folder>            # or --deb FILE --manifest FILE
bin/hcgrepo release incoming                             # submissions, plus superseded Candidate versions
bin/hcgrepo release show <id>                            # review, levels, findings, the exact SHA-256
bin/hcgrepo release approve <id> --sha256 <sha> --by "<name>" --record "<approval, verbatim>"
bin/hcgrepo release prepare <id> [--via pool|local-file] [--free-demo-approval "<owner's words>"] [--create-package-metadata]
bin/hcgrepo release status <id>                          # machine-readable result (Executable Maker)
bin/hcgrepo admin                                        # the same operations in a local dashboard
bin/hcgrepo release withdraw <id> --reason "…"           # before prepare; afterwards: bin/hcgrepo withdraw
```

- **Incoming and private storage.** `submit` copies the `.deb`, manifest and review into the private incoming store
  (`$HCG_REPO_INCOMING`, default `~/.local/share/hcg-repo/incoming`, mode 0700). The store is refused if it is inside
  the repository checkout. Until approval, neither the bytes nor the metadata enter Git. `validate` fails if any
  `.deb`/`.ipa` appears under `submissions/`, and `.gitignore` excludes them.
- **Inspection and validation.** `submit` runs the manifest comparison, the existing package validation, the v2
  engine and a dry-run import against the pool (index consistency, the immutable lock, the access policy). The state
  becomes `validated` or `refused`.
- **Exact-checksum approval.** `approve` accepts only a `validated` submission, only its exact SHA-256 (re-hashed
  from the store), and needs who approved and the approval verbatim. It publishes nothing.
- **Candidate preparation.** `prepare` needs the approval, re-checks the bytes, imports to `pool/` for the Candidate
  channel, and writes `repo-lock.json`, provenance (provider, source, toolchain, approval, validation levels),
  `imports/<id>.toml` (`kind = "local-submission"`) and `submissions/<id>/{submission,review}.json`. Commit these on a
  branch for review. It never creates package metadata silently: `packages/<id>/package.toml` must exist, or
  `--create-package-metadata` creates it from the handoff's draft (only when absent, never overwriting).
  A sign-in/paid package needs `--free-demo-approval` for that one version, which adds exactly that version to
  `[access] exception_versions`.
- **Publication approval.** This is still the manual **Publish repository** workflow on `main`, run only after the
  owner's explicit approval. There is no automatic promotion to Stable.
- **Superseded and withdrawn.** `incoming` lists Candidate versions with a newer Candidate version: they stay
  installable by exact version, and package managers offer the newest one. Withdrawal is the existing
  `bin/hcgrepo withdraw`: the lock record stays and the bytes are never reused.

## 4. Validation levels (six, kept apart)

Every review, `inspect --deep`, provenance record, submission result, passport, the compatibility registry and the
dashboard report the same six levels (`tools/hcgrepo/levels.py`), each with its own state (`passed`,
`passed-with-warnings`, `failed`, `partial`, `not-run`, `not-recorded`, `not-applicable`):

| Level | What it proves | Where it comes from |
|---|---|---|
| **Static package inspection** | the package is well formed and holds nothing it must not | Debian structure, control fields, architecture, minimum iOS, rootless paths, dependencies, Mach-O, signatures and entitlements review, setuid, **embedded secrets** (private keys; GitHub, AWS, Slack, Stripe, Anthropic and OpenAI tokens; `.p12`; values never printed), **privileged helpers** (LaunchDaemons/Agents and who they run as, sensitive entitlements), manifest claims, checksum and index consistency |
| **Maintainer script review** | what the scripts would do, **read, not run** | static analysis: network fetches, obfuscation, package-manager calls and writes to system paths outside `/var/jb` are errors; launchd, resprings, kills and changes to the user's own data are review warnings |
| **Script-free dpkg lifecycle** | dpkg installs, reinstalls, removes and purges the payload cleanly (and upgrades from the previous pool version) | real `dpkg` with its own `--instdir`/`--admindir`/`--log` in a throwaway directory, on a **copy with the maintainer scripts removed**. Paths installed through the package's own symlinks are refused first. It runs on the CI runner or the owner's machine, never on production infrastructure. **It is never evidence that the original maintainer scripts work.** Every record of it says so. |
| **Device installation** | a real device installed the package | `device_tests` with `stage = "install"` and evidence (a launch record also shows the install, and says so) |
| **Application launch** | the app or tweak started on a real device | `device_tests` with `stage = "runtime"` |
| **Full runtime verification** | the product's documented checklist passed on a real device | `device_tests` with `stage = "verification"` |

The three device levels are never inferred from the machine levels.

## 5. Compatibility registry and public API

`bin/hcgrepo compat [--channel candidate] [--json | --public]` derives one status per released version from recorded
evidence only:

`Withdrawn` → `Physical launch passed` (a runtime pass) → `Physical installation passed` (an install pass, or a
partial runtime) → `Known incompatible` (a recorded failure to launch, or to install, with no success at that stage,
on the recorded configurations) → `Lifecycle validation passed` (v2 import) → `Static validation passed` (imported)
→ `Untested`.

Current state: Vortex 0.1.0~beta1 and Terminal Unleashed 0.2.0~rc1 are `Known incompatible`. rc1 installed, then
closed at launch (structured from the owner's report of 24 Nov 2026,
[test record](device-tests/hcg-terminal-unleashed-0.2.0-rc1.md)). Halo 0.1.0~beta1 is `Physical installation passed`
(a partial launch, not more). Each version lists its six levels and known failures. The last physically verified
version and the last launched version are reported separately.

**Public API, for review (not active).** `bin/hcgrepo compat --public` produces exactly what `<repo>/compatibility.json`
would contain ([schema](schemas/compatibility-v1.schema.json)). That covers availability and the download file with its
SHA-256 and size; requirements (layout, minimum and maximum iOS, `Depends`); tested jailbreaks; the six level states;
known failures (device model, iOS, jailbreak, date); and the owner's version label. It holds no source repositories or
commits (some are private), CI runs, toolchains, hosts, approval records, evidence paths or device identifiers.
Consumers are HCG Nexus, HCG Store, HCG Executable Maker, HCG System Tester and HCG Sentinel. Publishing it needs the
owner's approval and `[compatibility] publish = true` in `config/repo.toml`, which is still off.

## 6. Upcoming packages and corrected metadata (25 Nov 2026)

| Package | State in HCG Repo | Next step |
|---|---|---|
| Signal Contractor rootless 0.1.0~beta1 | nothing (no product entry, no package metadata, no checksum) | **no validated artifact exists.** Its hosted CI is blocked: the Unleashify run 45544038539 was never assigned a runner and every CI run in that private repository fails. The local Executable Maker build is the route ([EXECUTABLE_MAKER_SUBMISSION.md](EXECUTABLE_MAKER_SUBMISSION.md)), then the acceptance test (section 9) |
| Mail Unleashed 0.1.0~beta1 | **imported to Candidate** by PR #21 (another session), from HCG-Mail run 45580748239 (commit `b0c6fee`), SHA-256 `ac4b31b1…563099a`, 43,663,718 bytes, with the owner's version- and checksum-specific free-demo approval | independently re-inspected here: the only entitlement is its own Keychain group (`keychain-access-groups` = `com.hakmicapitalgroup.hcgmail.unleashed`), matching `[review.entitlements]`; static inspection, script review and the script-free lifecycle pass; no device evidence yet. It has no product entry in `config/products.toml` (as with Halo and Portal) |
| Terminal Unleashed | rc2 latest on Candidate; rc1 now has structured evidence (install pass, launch fail) | rc2 device test |
| Sentinel | **fixed:** the product entry now maps to its existing package `com.hakmicapitalgroup.hcgsentinel` (0.1.0~beta1, Candidate) instead of "no jailbreak package"; nothing duplicated | device test |
| Nexus, Portal, Vortex, Halo, Animation Center | existing packages; Vortex 0.1.0~beta1 now carries a "Known incompatible" label; Halo stays at a partial launch | new versions through any provider |
| Agent Runtime | planned; nothing created | a genuine build |

## 7. Ecosystem integration plan (not activated)

| System | Role | Contract |
|---|---|---|
| HCG Connect | admin identity for Release Manager actions | `--by` today. Later, an HCG Connect identity verified server-side before `approve`/`prepare`. |
| Executive Control Center | who may submit, approve or publish | maps to the three gates: submit, approve, Publish workflow (GitHub environment protection) |
| HCG Store | commercial listings and entitlements | [COMMERCIAL.md](COMMERCIAL.md), [STORE_INTEGRATION.md](STORE_INTEGRATION.md); delivery contracts and TEST results in [COMMERCIAL_DISTRIBUTION.md](COMMERCIAL_DISTRIBUTION.md) |
| Executable Maker | submissions | Unleashify handoff today; a native manifest v1 later (section 2) |
| HCG Server | artifact storage and build host | `https-artifact` with `provider = "hcg-server-build-host"` / `developer-upload` and an `auth_env` CI secret |
| Nexus | discovery | reads the public `Packages` index, and `compatibility.json` once enabled |
| Sentinel | security findings | can consume `review.json` findings (codes and stages are stable) |
| Representation | branding | the existing depiction and branding assets |

Free public packages stay ordinary APT downloads in Sileo and Zebra with no HCG sign-in. Protected commercial
packages keep the existing Sileo payment-provider flow (`payment_endpoint`, `authorize_download`). Nothing in v2
changes access policies.

## 8. Third-party developer marketplace (P2, design only, not active)

Not enabled: no external developer can publish and no payment is taken. When the owner decides:

1. **Registration and identity**: an HCG Connect account plus verified legal identity, held by HCG Connect, not HCG Repo.
2. **Ownership**: a developer owns a package-ID namespace (e.g. `com.<developer>.`). HCG's `com.hakmicapitalgroup.`
   stays reserved, which the existing namespace and reserved-bundle-ID checks enforce.
3. **Submission**: manifest v1 with `provider = "developer-upload"`, through HCG Server's upload storage. Same private
   store, same validation and the same exact-checksum approval by an HCG reviewer.
4. **Review and security**: all static and lifecycle levels; Sentinel findings; an IP declaration (rights to code,
   assets and trademarks) recorded with the submission.
5. **Version approval**: per version and per SHA-256, as today. Candidate first; Stable by separate approval.
6. **Pricing and licensing**: through HCG Store offers and central entitlements only (Store `docs/PAYMENTS.md`).
7. **Revenue sharing**: a configurable commission per developer agreement, computed by HCG Store from settled
   payments, never in HCG Repo.

## 9. Acceptance test: local Signal Contractor DEB

The procedure, once a real Executable Maker build exists:

```
bin/hcgrepo release submit --handoff ~/…/com.hakmicapitalgroup.<id>_0.1.0~beta1-candidate-handoff
bin/hcgrepo release show <id>       # presents the exact SHA-256 and the three validation levels
# owner approves that SHA-256 →
bin/hcgrepo release approve <id> --sha256 <sha> --by "<owner>" --record "<approval>"
bin/hcgrepo release prepare <id> --create-package-metadata --free-demo-approval "<owner's free-demo approval>"
# commit on a branch, review, merge; then, with the owner's explicit approval, run Publish repository.
# Zebra: refresh the HCG Repo Candidate source; the package appears and installs.
```

**Status: blocked on the artifact.** No Signal Contractor `.deb` exists (section 6), so no checksum can be presented
yet. The pipeline itself is tested end to end with real dpkg-built packages (`tests/test_distribution_v2.py`: an
Unleashify-style handoff is submitted, validated, approved, prepared into Candidate and passes repository
validation).

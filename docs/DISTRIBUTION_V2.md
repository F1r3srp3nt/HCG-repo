# HCG Repo Distribution Infrastructure v2

**Status (25 Nov 2026):** implemented on `main`, not deployed. The published repository is unchanged: the production
build is byte-identical before and after this change (88 files, `diff -r` clean), the Stable and Candidate URLs,
package identifiers, `repo-lock.json` and every published checksum are untouched. Nothing was published.

v2 adds what HCG Repo needs to accept packages from more than GitHub Actions, without loosening any existing rule:

| Area | What exists now | Where |
|---|---|---|
| Artifact providers | GitHub Actions (public and private), local HCG Executable Maker builds, HCG Server Build Host, approved developer uploads | `tools/hcgrepo/importer.py`, `submission.py` |
| Local import contract | `hcg.repo.artifact-submission` v1 manifest; adapter for Executable Maker's Unleashify handoff | `submission.py`, [schema](schemas/artifact-submission-v1.schema.json) |
| Candidate Release Manager | `bin/hcgrepo release submit / incoming / show / approve / prepare / withdraw` | `release.py` |
| Validation engine | secrets, maintainer-script behaviour, privileged helpers, simulated dpkg lifecycle | `deepcheck.py`, `bin/hcgrepo inspect --deep` |
| Compatibility registry | evidence-based status per version | `compat.py`, `bin/hcgrepo compat` |

The separation of **Prepare packages** and **Publish repository** is unchanged. No step uploads, publishes, approves
or promotes anything on its own.

## 1. Artifact providers

Every provider ends in the same importer (`import_deb`) and the same checks; they differ only in how the exact bytes
are obtained. Provenance records the provider; a run ID exists **only** for GitHub Actions builds.

| Provider (`build.provider`) | How the bytes arrive | Import request `kind` |
|---|---|---|
| `github-actions` | workflow-run artifact, public repository | `github-artifact` (unchanged) |
| `github-actions-private` | workflow-run artifact, private repository, read with the `HCG_IMPORT_TOKEN` secret (read-only, `actions:read` + `contents:read`). The source repository stays private; a 404 from GitHub now says which token is missing. | `github-artifact` |
| `executable-maker-local` | the owner's machine: `bin/hcgrepo release submit --handoff <folder>` | `local-submission` |
| `hcg-server-build-host` | authorized HTTPS download; the token comes from a CI secret named by `auth_env` (`HCG_…`), never stored in Git, never sent to a redirect | `https-artifact` |
| `developer-upload` | as above, from HCG Server's upload storage, after review | `https-artifact` |

A related proposal, draft PR #20 (`kind = "local-file"`, from the Executable Maker work), commits the approved `.deb` under `imports/local/` so that Prepare packages imports it in CI. It is compatible with v2: the v2 validation engine applies to it too, and `submissions/` stays the only place where package files are refused. The owner decides whether to keep both routes.

Rules enforced in code:

- `local-submission` and `https-artifact` need the approved `sha256`, a `submission` path, and the owner's approval of
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
Executable Maker now writes a native `submission.json` (this format) next to `provenance.json`, declaring the
maintainer scripts it packaged; it names no jailbreaks until a device test records one. When a handoff has one,
`release submit --handoff` uses it, after checking that it agrees with the handoff's `provenance.json` on package,
version, architecture, file, SHA-256, size, source repository and commit, provider and run ID; any disagreement
refuses the handoff. Without one, the adapter above is used as before.

## 3. Candidate Release Manager

```
bin/hcgrepo release submit --handoff <folder>            # or --deb FILE --manifest FILE
bin/hcgrepo release incoming                             # submissions, plus superseded Candidate versions
bin/hcgrepo release show <id>                            # review, levels, findings, the exact SHA-256
bin/hcgrepo release approve <id> --sha256 <sha> --by "<name>" --record "<approval, verbatim>"
bin/hcgrepo release prepare <id> [--free-demo-approval "<owner's words>"] [--create-package-metadata]
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

## 4. Validation levels

These three levels are reported separately everywhere: `release show`, `inspect --deep`, provenance and the
compatibility registry.

| Level | What it proves | How |
|---|---|---|
| **Static** | the package is well formed and contains nothing it must not | Debian structure, control fields, architecture, minimum iOS, rootless paths, dependencies, Mach-O, signatures and entitlements review, setuid, **embedded secrets** (private keys; GitHub, AWS, Slack, Stripe, Anthropic and OpenAI tokens; `.p12`; values never printed), **maintainer-script behaviour** (network fetches, obfuscation, package-manager calls, writes to system paths outside `/var/jb` are errors; launchd, resprings, kills and changes to the user's own data are review warnings), **privileged helpers** (LaunchDaemons/Agents and who they run as, sensitive entitlements), checksum and index consistency |
| **Simulated lifecycle** | dpkg can install, reinstall, remove and purge the payload cleanly (and upgrade from the previous version in the pool) | real `dpkg` with its own `--instdir`/`--admindir`/`--log` in a throwaway directory, on a **copy whose maintainer scripts are removed**: no submitted script is ever executed. Paths installed through the package's own symlinks are refused before extraction. Runs on the CI runner or the owner's machine, never on production infrastructure. |
| **Physical device** | it installs or launches on a real phone | only from a device report recorded in `package.toml` `device_tests` with evidence. Never inferred. |

`bin/hcgrepo inspect --deep <file.deb>` runs the static and lifecycle levels on any package.

## 5. Compatibility registry

`bin/hcgrepo compat [--channel candidate] [--json]` derives one status per released version from recorded evidence
only:

`Withdrawn` → `Known incompatible` (failing device tests only) → `Physical launch passed` (runtime pass) →
`Physical installation passed` (install pass, or a partial runtime) → `Lifecycle validation passed` (v2 import) →
`Static validation passed` (imported) → `Untested`.

Each entry also shows layout (rootless/rootful), minimum and maximum iOS, dependencies, device evidence, the owner's
version label, known issues and the last physically verified version. Publishing `repo/compatibility.json` is
**off** (`[compatibility] publish = false` by default) until the owner turns it on in `config/repo.toml`. Depiction
pages already show device tests, labels, known issues and the Candidate/Stable status. Example today: Vortex
0.1.0~beta1 is `Known incompatible` (the recorded install failure), and Halo 0.1.0~beta1 is
`Physical installation passed` (a partial runtime record). Terminal Unleashed 0.2.0~rc1's launch failure is recorded
as a version label, not a `device_tests` entry, so it shows `Static validation passed` with that label. Recording it
as a `device_tests` entry needs the device report as evidence.

## 6. Upcoming packages (inspected 25 Nov 2026)

| Package | In HCG Repo today | What v2 needs before an import |
|---|---|---|
| Signal Contractor rootless 0.1.0~beta1 | nothing (no product entry, no package metadata) | a real `.deb`. The Unleashify CI workflow in HCG-Signal-Contractor (`hcg/unleashify-0.1.0-beta1`, run 45544038539) failed and uploaded no artifact; earlier CI runs there failed too. A local Unleashify handoff from the owner works equally well (`release submit --handoff`). |
| Mail Unleashed | nothing | a jailbreak packaging of HCG-Mail; none exists yet |
| Terminal Unleashed updates | `com.hakmicapitalgroup.hcgterminal.unleashed`, rc2 latest on Candidate | a new version (never new bytes for rc2); existing `build` recipe or any provider |
| Nexus, Portal, Vortex, Halo, Animation Center, Sentinel | existing packages and metadata | new versions through any provider; Sentinel's product entry has empty `package_ids` (pre-existing, unchanged) |
| Agent Runtime | nothing | design only; it gets an identifier when it has a build |

No placeholder metadata was created. `release prepare --create-package-metadata` creates a package's metadata from
its handoff draft when the real artifact arrives.

## 7. Ecosystem integration plan (not activated)

| System | Role | Contract |
|---|---|---|
| HCG Connect | admin identity for Release Manager actions | `--by` today. Later, an HCG Connect identity verified server-side before `approve`/`prepare`. |
| Executive Control Center | who may submit, approve or publish | maps to the three gates: submit, approve, Publish workflow (GitHub environment protection) |
| HCG Store | commercial listings and entitlements | unchanged: [COMMERCIAL.md](COMMERCIAL.md), [STORE_INTEGRATION.md](STORE_INTEGRATION.md) |
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

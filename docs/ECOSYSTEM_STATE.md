# HCG Repo ecosystem state

Reconciled on 27 Nov 2026 against `main` at `b0228df` (PR #26, HCG Portal 0.1.0~beta5) and every remote branch.
Nothing was imported, published, withdrawn or relabelled while preparing this file. Identities are the exact
SHA-256 values in `repo-lock.json`; verdicts are those of the compatibility registry (`bin/hcgrepo compat`),
copied, not re-judged. Regenerate the tables with `bin/hcgrepo compat` and `bin/hcgrepo admin` before relying on
them later.

States used here:

| State | Meaning |
|---|---|
| **Published** | In `repo-lock.json` on `main` and served from GitHub Pages |
| **Protected** | Recorded in `repo-lock.json` (identity only), never stored or served by HCG Repo |
| **Approved, held** | Owner-approved and imported on a branch; publication deliberately held |
| **Development** | Work exists in a product repository; no package submitted to HCG Repo |
| **Planned** | Named in the roadmap; no package |

Repository signing: **not active** (`signing_fingerprint = ""`). Payment endpoint: **not advertised**
(`payment_endpoint = ""`). Commercial delivery: **off**.

## Published: HCG Repo Candidate (`/repo/candidate/`, free public betas)

| Package | Version | SHA-256 (first 16) | Registry verdict | Note |
|---|---|---|---|---|
| HCG Portal `com.hakmicapitalgroup.portal` | 0.1.0~beta5 | `4e00a61a3333917f` | Lifecycle validation passed | **Diagnostic candidate**, awaiting physical-device validation |
| | 0.1.0~beta4 | `8a186d99a63d38c2` | Static validation passed | Rollback; superseded (not shown on a physical iPhone) |
| | 0.1.0~beta3 | `9af1575a368d5353` | Static validation passed | Rollback; superseded (Lock Screen taps) |
| | 0.1.0~beta1 | `9ccfeb9b6b832edb` | Static validation passed | Rollback; superseded (Safe Mode reported, cause unconfirmed) |
| HCG Nexus `com.hakmicapitalgroup.hcgnexus` | 0.6.0~beta1 | `587b2fbd7548b0c9` | Lifecycle validation passed | Current free demo; device test pending |
| | 0.5.0~beta1, 0.4.0~beta1 | `279902e728aac293`, `86a0ed3cbd09a162` | Lifecycle validation passed | Superseded by 0.6.0~beta1, kept for rollback |
| | 0.3.3~beta1, 0.3.2~beta1, 0.3.1~beta1, 0.2.2~beta1, 0.2.1~beta1 | see lock | Static validation passed | Superseded, kept for rollback |
| HCG Halo `com.hakmicapitalgroup.halo` | 0.3.1~beta1, 0.3.0~beta1, 0.2.0~beta1 | `6a2117b381cb2a56`, `bd8a926667b8ebec`, `391a4421de982251` | Static validation passed | |
| | 0.1.0~beta1 | `c0a3301c9b38cdac` | Physical installation passed | Partial runtime, iPhone 11 Pro Max iOS 17.4 Dopamine 2.x |
| HCG Mail Unleashed `com.hakmicapitalgroup.hcgmail.unleashed` | 0.1.0~beta1 | `ac4b31b10dbdf9f0` | **Known incompatible** | Installs (Zebra), closes at launch (25 Nov 2026) |
| HCG Sentinel `com.hakmicapitalgroup.hcgsentinel` | 0.1.0~beta1 | `f1111fef7ab0b5da` | Static validation passed | |
| HCG Terminal Unleashed `com.hakmicapitalgroup.hcgterminal.unleashed` | 0.2.0~rc2 | `597eb8f11519c9a9` | Static validation passed | Startup recovery + Safe Mode; device verification pending |
| | 0.2.0~rc1 | `02626804763200f9` | **Known incompatible** | Closes at launch, iPhone 11 Pro Max iOS 17.4 |
| Vortex `com.hakmicapitalgroup.vortex` | 0.3.1~beta1, 0.3.0~beta1, 0.1.0~beta2 | `ff1d1b5f78ec79b1`, `0711465af25edbef`, `a326759a697899ee` | Static validation passed | 0.3.0: 3 of 6 effects seen in a recording |
| | 0.1.0~beta1 | `bb0af72745a36158` | **Known incompatible** | Failed to unpack (Zebra 1.1.x, 17 Nov 2026) |
| Animation Center `com.hakmicapitalgroup.animationcenter` | 0.1.0~beta1 | `27a161b3cc323f09` | Static validation passed | Simulator-tested |

All rollback versions stay published, with their bytes unchanged. Superseded is a label, never a removal.

## Protected: stable source (`/repo/`), recorded but not served

| Package | Version | Access | SHA-256 (first 16) | State |
|---|---|---|---|---|
| HCG Nexus | 0.2.0 | paid (HCG Store) | `1987156996c2e76f` | Protected. No approved Store offer, so no `cydia::commercial` listing |
| HCG Terminal Unleashed | 0.1.0-1 | connect (account required) | `7fdb735e74e551fa` | Protected; HCG Store protected delivery is disabled in production |

The stable source currently serves no installable package: everything public is in Candidate.

## Approved, held (branches, not on `main`)

| Branch | Content | Identity | Why held |
|---|---|---|---|
| `claude/candidate-nexus-0.7.0` (`636e187`, `dd585d5`) | HCG Nexus 0.7.0~beta1, Candidate, temporarily free beta | sha256 `004e6a9ea2db3428e821249546c5baef01e113c2396ab944e232001bbad3e611`, 2,131,624 bytes, HCG-Nexus `2b521fb`, CI run 45872511908 | Owner: hold until the published 0.6.0~beta1 is physically tested |
| `maker/native-submission-manifest` (`dd4b81d`) | Release Manager: use a handoff's native `submission.json` | code only | Another session's work, not reviewed into `main` here |

## Development (no new package submitted)

| Product | Where it stands |
|---|---|
| HCG Mail Unleashed 0.1.0~beta2 (diagnostic) | In development in the HCG Mail repository (branch head `e298b2f`; its CI run was still in progress when last checked); not submitted. When it is, it goes through `bin/hcgrepo release submit` like any other build. 0.1.0~beta1 stays published as Known incompatible |
| HCG Portal after beta5 | Awaiting the owner's device result for 0.1.0~beta5 |
| HCG Terminal Unleashed 0.2.0 final | Awaiting a device result for rc2 |
| Executable Maker Unleashed | No package. Its "Ship to Repo" contract is in `docs/COMMERCIAL_DISTRIBUTION.md` |

## Planned (no package)

HCG Agent Runtime, HCG System Tester, HCG UEM. UEM readiness requirements are in `docs/COMMERCIAL_DISTRIBUTION.md`;
nothing in HCG Repo depends on UEM.

## HCG Store (verified read-only, 27 Nov 2026)

`https://store.hakmicapitalgroup.com/api/v1/health` → `mode: production`, `hcgConnect: configured`,
`entitlementAuthority: hcg-ecosystem`, `payments: disabled`, `protectedDownloads: disabled`, `publishing: uploads`.
Offers: none. Store `main` is `57e442c` (PR #28 Distribution Studio API, PR #27 Control Center). The Store's own
`CLAUDE.md` still says the server is not deployed; it is deployed, with payments and protected downloads off.

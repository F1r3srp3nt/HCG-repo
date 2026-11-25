# HCG Release Passport (`hcg.release-passport` v1, final)

Format version 1 is **final** (frozen 25 Nov 2026). New fields require format version 2.

A standard release record for every HCG product, so HCG Executable Maker, HCG Repo, HCG Store and HCG Server describe
the same build the same way. It builds on the submission manifest (`hcg.repo.artifact-submission` v1), whose
provenance fields it reuses. Schema: [schemas/release-passport-v1.schema.json](schemas/release-passport-v1.schema.json).

```
bin/hcgrepo passport                                   # every release in repo-lock.json
bin/hcgrepo passport <package> [<version>]             # one package or one release
bin/hcgrepo passport --out passports/                  # one JSON file per release
```

| Field | Meaning | HCG Repo's source |
|---|---|---|
| `product` {key, name, store_slug, central_id} | product identity | `config/products.toml`; `central_id` from HCG ERP once assigned (null today) |
| `edition` | edition within the product | `store_edition` (null when none) |
| `platform`, `architecture` | e.g. `ios-jailbreak-rootless`, `iphoneos-arm64` | the package architecture |
| `package`, `version` | Debian identity | the lock |
| `source` {repository, commit, branch} | source provenance | import provenance |
| `build` {provider, toolchain, built_at, run_id, artifact} | build provider; `run_id` only for GitHub Actions | import provenance / submission manifest |
| `artifact` {filename, package, version, architecture, sha256, sha512, size} | the exact artifact identity and immutable bytes | the lock and provenance |
| `validation.levels` | the six evidence levels ([DISTRIBUTION_V2.md](DISTRIBUTION_V2.md) §4) | provenance + `device_tests` |
| `device_evidence[]` | physical-device records with evidence | `package.toml` `device_tests` |
| `compatibility_status`, `runtime_compatibility` | the registry status; overall runtime compatibility (`passed`, `launch-only`, `partial`, `failed`, `not-established`), separate from installation | `compat.py` |
| `approvals[]` | checksum approval, free-demo approval, Release Manager history, and `signed-artifact-approval` with its verification result | provenance, `[access]`, `submissions/<id>/review.json`, `approvals/` ([RELEASE_APPROVALS.md](RELEASE_APPROVALS.md)) |
| `distribution` {channel, access} | Candidate or Stable; public, connect or paid | the lock, `[access]` |
| `repository_signing` {scope, configured, authenticated, fingerprint, status} | **repository metadata** signing (OpenPGP over `Release`) of the channel serving this release; the fingerprint users compare | `config/repo.toml [signing]`; verified per deploy ([SIGNING.md](SIGNING.md)) |
| `code_signing` {scope, method_claimed, binaries, all_signed, entitlements} | **application code** signing of the Mach-O inside the package (ldid or Apple); unrelated to the repository key | import inspection; the manifest's `signing` claim |
| `publication` {record_status, in_index_builds, deployed} | published, protected or withdrawn. `deployed` is checked with `bin/hcgrepo verify`, never assumed | the lock |

Passports are derived and are not stored: regenerating one from the same records gives the same result. Other
systems fill their own fields in the same format: Executable Maker the build half (from `submission.json`), HCG Store
`edition`, `central_id` and its release approval, HCG Server artifact storage. Passports contain the owner's approval
wording (already public in `package.toml`) and source repository names. Use the public compatibility API
([DISTRIBUTION_V2.md](DISTRIBUTION_V2.md) §5), not passports, for anything published.

## Coordination with HCG Executable Maker and HCG Store

The passport reuses the submission manifest's names, so each system fills its half without translation:

| Passport field | HCG Executable Maker | HCG Store | HCG Repo |
|---|---|---|---|
| `artifact`, `source`, `build`, `code_signing` | from `submission.json` (`artifact`, `source`, `build`, `signing`) | reads | verifies against the package |
| `product.central_id`, `edition` | — | provides (`centralId`, `editionId`) | copies |
| `approvals` | — | adds its release approval (`approved-for-sale`, delivery approval) | adds checksum, free-demo and signed approvals |
| `repository_signing`, `distribution`, `publication` | — | reads | provides |
| `validation.levels`, `device_evidence`, `runtime_compatibility` | provides its machine levels as claims | reads | provides the verified levels |

**Never conflate:** `code_signing` is the application's Mach-O signature (ldid or Apple). `repository_signing` is HCG
Repo's OpenPGP signature over the index. `repository_signing.authenticated` is `false` while the metadata is unsigned.
Coordination status: the formats and schemas are ready in this repository. Executable Maker and HCG Store must adopt
them in their own repositories, which this work has not changed.

# HCG Release Passport (`hcg.release-passport` v1)

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
| `artifact` {filename, sha256, size} | the immutable bytes | the lock |
| `validation.levels` | the six evidence levels ([DISTRIBUTION_V2.md](DISTRIBUTION_V2.md) §4) | provenance + `device_tests` |
| `device_evidence[]` | physical-device records with evidence | `package.toml` `device_tests` |
| `compatibility_status` | the registry status | `compat.py` |
| `approvals[]` | checksum approval, free-demo approval, Release Manager history | provenance, `[access]`, `submissions/<id>/review.json` |
| `distribution` {channel, access} | Candidate or Stable; public, connect or paid | the lock, `[access]` |
| `publication` {record_status, in_index_builds, deployed} | published, protected or withdrawn. `deployed` is checked with `bin/hcgrepo verify`, never assumed | the lock |

Passports are derived and are not stored: regenerating one from the same records gives the same result. Other
systems fill their own fields in the same format: Executable Maker the build half (from `submission.json`), HCG Store
`edition`, `central_id` and its release approval, HCG Server artifact storage. Passports contain the owner's approval
wording (already public in `package.toml`) and source repository names. Use the public compatibility API
([DISTRIBUTION_V2.md](DISTRIBUTION_V2.md) §5), not passports, for anything published.

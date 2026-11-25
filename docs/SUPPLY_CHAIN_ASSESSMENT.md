# HCG Repo software supply chain: authorized assessment plan (HCG Offensive Frontier)

**Scope: isolated test environments only.** No testing against production infrastructure is authorized by this
document: not `repo.hakmicapitalgroup.com`, not the production GitHub environment `repo-signing` or its secret, and not
the owner's devices. Each assessment run needs a written scope approved by the owner (dates, tester, environment,
test cases).

## Environments

| Environment | What it is | How |
|---|---|---|
| Local build + server | a full HCG Repo build served on 127.0.0.1 | `bin/hcgrepo build`, `bin/hcgrepo serve`, or the test fixtures (`tests/support.py`, `BackgroundServer`) |
| Throw-away keys | temporary OpenPGP keys, never production | `tools/signing/founder-keygen.sh <dir> --test`, or the keys the tests generate |
| Real APT client | `apt-get update` with an isolated state directory | `tests/test_signing_clients.py` (`SigningClientTests.apt`) |
| Fork / scratch repository | a copy of HCG-repo with its own Pages site and secrets | owner-created; never the production repository |
| Private incoming store | a temporary `$HCG_REPO_INCOMING` | Release Manager tests |

## Test cases and current coverage

| Area | Test | Expected | Automated today |
|---|---|---|---|
| Artifact integrity | modify a `.deb` after import, or substitute bytes for a published version | refused (`immutable-version`, `checksum-mismatch`, guard `unexpected-change`) | `test_distribution_v2`, `test_signing_clients` |
| Artifact integrity | CI artifact substitution (other run, other commit, expired artifact, zip digest mismatch) | refused by `fetch_workflow_artifact` | `test_github_import` |
| Tampered metadata | same-size change to `Packages` after signing | APT: hash mismatch; HCG verify: refused | `test_tampered_metadata_is_refused` |
| Tampered metadata | change `InRelease` / `Release` after signing | APT: `BADSIG`; gpgv: refused | same, `test_signing` |
| Wrong key | valid signature by another key | refused (fingerprint mismatch) | `test_signature_by_another_key_is_rejected_by_hcg_checks` |
| Downgrade | drop signatures once the live source is signed | guard `signing-downgrade`; verify error once a fingerprint is configured | `test_guard_blocks_unexpected_changes_and_downgrades` |
| Key rotation abuse | silently switch the signing key | guard `signing-key-change` unless acknowledged | same |
| Release approval enforcement | import a local or HTTPS artifact without the owner's approval of its exact SHA-256 | refused (`no owner approval`) | `test_local_import`, `test_distribution_v2` |
| Release approval enforcement | commit an unapproved `.deb` under `imports/` or `submissions/` | `validate` fails (`artifact-unapproved`, `artifact-outside-pool`) | same |
| Access control | free download of a `connect`/`paid` version; free demo in Stable | refused (protected path, free-demo policy) | `test_access`, `test_access_versions`, `test_commercial` |
| Access control | the admin dashboard from another origin, a foreign Host header, or without a token | 403 | `AdminDashboardTests` |
| Malicious packages | embedded secrets, network-fetching or obfuscated maintainer scripts, writes outside `/var/jb`, privileged helpers | refused or flagged | `DeepCheckTests` |
| Lifecycle isolation | maintainer scripts that try to run during validation | never executed | `test_lifecycle_never_runs_scripts` |
| Secrets in Git | private key material in `keys/` | `validate` fails (`secret-key-committed`) | `test_signing_clients` (procedure) and `validate` |

## Not yet covered (for a future assessment)

- **Replay of old signed metadata.** `Valid-Until` is not set (see [SIGNING.md](SIGNING.md) §6). Test against
  Sileo before enabling it.
- **Approval authenticity.** Approval records are protected by Git review, not cryptographically. Assess forging a
  `review.json` in a pull request; mitigations are required reviewers, HCG Connect identity and signed commits.
- **GitHub workflow hardening.** Pinned actions are used. Review permissions and environment protection in a fork.

## Rules of engagement

- Use only the environments above, with throw-away keys and fixture packages.
- Run no denial-of-service and no load testing against GitHub Pages, the CDN or GitHub APIs.
- Report findings to the owner. Fixes go through normal reviewed changes.

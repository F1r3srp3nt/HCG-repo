# Cryptographic release approvals (`hcg.release-approval` v1)

**Status:** implemented and tested with temporary keys. Enforcement is **off**: `[approvals] required_for_import`
and `required_for_publish` default to `false`, and no approver key is configured. Approvals today are the recorded,
Git-reviewed owner statements (Release Manager `review.json`, `[access] exception`).

## Separate keys, separate approvals

- **Founder approval key:** a dedicated OpenPGP key, e.g. on a hardware token, used only to approve. Its public key
  goes in `keys/approvers/`. Verification refuses any approver key that is, or contains, the repository signing
  key, and refuses statements signed with it. The repository signing key signs only `Release` files and is never an
  administrative credential.
- **Artifact approval** (`approvals/artifact/<package>_<version>_<arch>_<channel>.json` plus `.json.asc`) binds:
  package, version, architecture, SHA-256, distribution channel, source repository and commit, the approved access
  policy, and the approval time. It authorizes those bytes for that channel, and nothing else.
- **Publication approval** (`approvals/publication/<target>_<commit12>.json` plus `.json.asc`) binds the HCG-repo
  commit to deploy, the target, the channels and the time. It approves no artifact.

Statements are canonical JSON (sorted keys, compact), so the signature covers exactly one byte sequence. A reformatted
file fails as `approval-not-canonical`. Approvals are never overwritten.

## Commands

```bash
bin/hcgrepo approval draft-artifact --package <id> --version <v> --architecture iphoneos-arm64 --channel candidate \
    --approver <APPROVER-FPR> [--sha256 <sha> --repository OWNER/NAME --commit <40hex>]   # values from the lock if imported
bin/hcgrepo approval draft-publication --commit <HCG-repo 40hex> --target production --approver <APPROVER-FPR>
# offline, with the founder approval key (never the repository signing key):
gpg --local-user <APPROVER-FPR> --armor --detach-sign approvals/<kind>/<file>.json
bin/hcgrepo approval verify                    # every statement under approvals/
bin/hcgrepo approval check-publication --commit <sha> --target production   # what Publish runs
```

## Enforcement (when the owner enables it)

```toml
[approvals]
approver_keys = ["keys/approvers/founder.asc"]
required_for_import = true     # Release Manager prepare, every import request, and validate for entries since `since`
required_for_publish = true    # the Publish workflow refuses a commit without a verified publication approval
since = "2026-12-01T00:00:00Z"
```

Checked against the repository's own records:
- the exact SHA-256 of the bytes;
- the channel requested;
- the source commit and repository in provenance;
- the access policy of that version in `package.toml`;
- for publication, `GITHUB_SHA` and the target.

Tests (`tests/test_approvals.py`) cover:
- every bound field, including tampering and a non-canonical file;
- signatures by an unknown key and by the repository signing key;
- no overwriting;
- publication bound to a commit and target;
- opt-in enforcement in `validate`.

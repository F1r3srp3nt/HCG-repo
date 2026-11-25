# Repository metadata signing

**Status (25 Nov 2026): HCG Repo is published UNSIGNED.** No production archive key exists. `config/repo.toml` has an
empty `[signing] fingerprint`, and the live `build-info.json` says `"signed": false`. Everything needed to sign is
implemented and tested with **temporary keys only**. Production signing starts when the owner completes the founder
procedure (section 5). Nothing in this repository creates, holds or activates a production key.

## 1. Two different kinds of signing

| | Repository metadata signing (this document) | Application code signing |
|---|---|---|
| What is signed | `Release` (detached `Release.gpg`, clearsigned `InRelease`), which lists the SHA-256/SHA-512 of `Packages*`, which list each `.deb`'s SHA-256 | each Mach-O inside a package |
| With | the HCG Repo OpenPGP archive key (a signing subkey) | `ldid` pseudo-signature with an entitlements plist, or an Apple certificate |
| When | at every publication, by the Publish workflow | when the package is built, in the product's own repository or Executable Maker |
| Checked by | APT and Sileo, against keys in `<jbroot>/etc/apt/trusted.gpg.d/` | the jailbreak's code-signing bypass or trust cache; HCG Repo's static inspection reads it |
| In the Release Passport | `repository_signing` | `code_signing` |

HCG Repo never re-signs code. In the submission manifest, `signing` describes **code** signing.

## 2. What each client does (evidence)

| Client | Unsigned source (today) | Signed, key NOT installed | Signed, key installed | Evidence |
|---|---|---|---|---|
| **Sileo** | accepted | accepted; the signature is ignored, so the source behaves as unsigned | verified with `apt-key verify` on `Release` + `Release.gpg` (it does not read `InRelease`). Once verified, a later bad signature stops updates of that source | Sileo source, commit `c9f7015` (earlier review in this repository) |
| **Zebra** | accepted | accepted: unaffected | unaffected: Zebra never downloads `Release.gpg` or `InRelease` and installs its own downloads with `apt install` and `Dir::State::lists=/var/empty`, so no signature is checked. No code found that checks package bytes against the index SHA-256 either, so Zebra relies on HTTPS | Zebra `v1` branch, commit `e2f3c27` (`ZBBaseSource.m`, `ZBDownloadManager.m`, `ZBQueue.m`) |
| **APT** (command line, e.g. Procursus `apt`) | **refused** ("is not signed") unless the source is marked trusted | refused (`NO_PUBKEY`) | accepted. Tampered `Packages` → hash mismatch; tampered `InRelease` → `BADSIG` | real `apt-get` against locally served builds: `tests/test_signing_clients.py` |

**Consequence:** turning signing on breaks no current Sileo or Zebra user and needs no URL change. It only adds
protection for users who install the HCG key (Sileo, APT). Nothing here ever tells users to disable verification.

## 3. What the code enforces

- **Signing:** `Release.gpg` and `InRelease` for **Stable (`/repo/`) and Candidate (`/repo/candidate/`)**, SHA-512 digest.
  The public key is published at `<source>/hcg-repo-archive-key.asc` in both sources.
- **Independent verification at build time:** `gpgv` against **only** the committed public key. Both signatures must
  be good and use a strong digest, gpgv's `VALIDSIG` must name the **configured primary fingerprint** (another key, even
  a valid one, is refused), and `InRelease` must carry exactly `Release`.
- **Key check before signing:** an expired or revoked key, a wrong fingerprint, no usable signing subkey, or a signing
  ability that ends within 14 days refuses to sign. `bin/hcgrepo signing-status` warns from 60 days.
- **Fail-closed:** once `[signing] fingerprint` is set, the build refuses unsigned output whatever the workflow input
  says. The Publish workflow stops if the secret is missing, or if a secret exists without a configured fingerprint.
  Live verification (`bin/hcgrepo verify`) treats a missing signature as an error. There is no silent fallback.
- **Publication guard, before deploy** (`bin/hcgrepo guard --against-live`):
  - every indexed package has exactly the lock's SHA-256 and the file has those bytes;
  - nothing published is missing;
  - no live version changes bytes;
  - nothing disappears without a withdrawal record;
  - no signing downgrade (live signed, new unsigned);
  - no key change unless the run acknowledges a rotation (`allow_key_change`).
- **After deploy** (`guard --deployed`): every live metadata file of both channels is byte-identical to the build, in
  addition to the existing HTTPS verification with signature checks.
- **Monitoring** (`bin/hcgrepo monitor`): read-only, for HCG Sentinel ([SENTINEL_MONITORING.md](SENTINEL_MONITORING.md)).

## 4. Migration plan for the unsigned repository (tested with temporary keys)

What stays identical (tested: `test_migration_changes_no_package`): package identifiers, versions, `.deb` bytes,
SHA-256 checksums, the `Packages` files of both channels, the Stable and Candidate URLs, and access policies. Only
`Release.gpg`, `InRelease` and the public key file are added next to `Release`.

Steps, in order:

1. **Founder creates the key offline** (section 5). Nothing changes for users.
2. **First signed publication.** One reviewed PR:
   - add `keys/hcg-repo-archive-key.asc`;
   - set `[signing] fingerprint` (`required = true` is then implied);
   - the founder adds the CI secret.

   Publish runs with `require_signature` on. The guard sees an unsigned to signed transition, which is allowed.
   `verify-deployed` checks the signatures and bytes live. Sileo and Zebra users notice nothing.
3. **Keyring package.** Build `com.hakmicapitalgroup.repo-keyring` (`bin/hcgrepo keyring-deb`, rootless and rootful)
   and import it through the normal reviewed flow. It installs only `<jbroot>/etc/apt/trusted.gpg.d/hcg-repo.gpg`.
4. **Announce** the fingerprint on the website and in this file. Users compare it before trusting.
5. **Monitor** with `bin/hcgrepo monitor` / HCG Sentinel. From then on an unsigned or wrongly signed source is an alert.

**What existing users need to do:**

| User | Action needed | Optional, for verification |
|---|---|---|
| Sileo | **none**: the source keeps working | install **HCG Repo Keyring** from HCG Repo, then refresh sources. Sileo then verifies every refresh, and a tampered or wrongly signed `Release` stops updates instead of being accepted |
| Zebra | **none** | none: Zebra does not check repository signatures |
| APT on the command line | today the source only works if it is marked `[trusted=yes]` | install HCG Repo Keyring, then remove `trusted=yes` from the source line |

Nobody needs to remove or re-add the source, or change its URL.

## 5. Founder procedure (production key; owner only)

The owner runs it on a trusted **offline** machine. It is not run in CI and not in an assistant session; no private
key is ever pasted into a chat, an issue or a log.

1. **Generate** (prompts for a strong passphrase):
   ```bash
   tools/signing/founder-keygen.sh ~/hcg-repo-key \
     --uid "HCG Repo Archive Key <repo@hakmicapitalgroup.com>" --primary-expire 5y --subkey-expire 2y
   ```
   This creates a certify-only RSA 4096 primary key and a dedicated RSA 4096 signing subkey (RSA for the widest
   client compatibility). It also writes:
   - the public key and its fingerprint;
   - the subkey-only CI secret, with the primary key absent (`sec#`);
   - a passphrase-protected full backup;
   - the revocation certificate.

   It ends with a self-test: the subkey signs, `gpgv` verifies against the public key only, and the signer is the
   primary. It refuses to write inside a Git checkout and prints no secret material. (Tested with `--test` keys in
   `tests/test_signing_clients.py`.)
2. **Verify the fingerprint:** read `public/FINGERPRINT` aloud against the output and against what will be published.
3. **Back up** `offline/` to two encrypted offline media (e.g. two encrypted USB drives in different places). Keep
   `revocation-certificate.rev` separately, and optionally on paper. Record the passphrase in a password manager or
   sealed paper, not with the backups.
4. **GitHub:** Settings → Environments → `repo-signing`:
   - add yourself as a **required reviewer**, so every publication waits for your approval;
   - add the secret `HCG_REPO_GPG_PRIVATE_KEY` with the content of `ci/hcg-repo-signing-subkey.asc`;
   - then shred that file (`shred -u`).
5. **Repository PR:**
   - commit `public/hcg-repo-archive-key.asc` as `keys/hcg-repo-archive-key.asc`;
   - set `config/repo.toml` `[signing] fingerprint = "<40 hex>"`;
   - run `bin/hcgrepo signing-status`, which must show the fingerprint, the subkey and its expiry, and no errors;
   - merge after review.
6. **Publish** with `require_signature` on, after the explicit publication approval. Confirm that `verify-deployed`
   reports both channels signed and verified, and that `bin/hcgrepo monitor` shows `signature.*: ok`.
7. **Keyring package and announcement** (section 4, steps 3 and 4).

### Expiry, rotation, revocation, recovery

- **Expiry:** the signing subkey lasts 2 years and the primary 5. `signing-status` warns from 60 days and the build
  refuses to sign within 14 days. To extend, without changing the fingerprint users trust:
  1. offline, run `gpg --quick-set-expire <FPR> 2y <SUBKEY-FPR>`;
  2. re-export the public key and commit it;
  3. re-export the subkey-only secret into the environment.
- **Subkey rotation** (scheduled, or after suspected CI exposure):
  1. offline, add a new signing subkey and revoke the old one (`gpg --edit-key <FPR>` → `revkey`);
  2. commit the new public key and replace the secret;
  3. publish with `allow_key_change`.

  The primary fingerprint, the keyring package and users' trust stay the same.
- **Primary key rotation** (compromise or algorithm change):
  1. publish a keyring package version holding **both** keys and wait for adoption, since Sileo stops updating a
     verified source whose signature fails;
  2. switch the fingerprint, public key and secret, and publish with `allow_key_change`;
  3. remove the old key in a later keyring version;
  4. publish the old key's revocation certificate.

  Tested: a client holding only the old key refuses the new signature, and the transition keyring accepts it.
- **Revocation:** import `revocation-certificate.rev` into the offline keyring, then publish the updated public key
  (commit plus keyring package).
- **Recovery:**
  1. restore `offline/` from a backup (`gpg --import primary-secret-backup.asc`);
  2. verify the fingerprint;
  3. re-export the CI subkey with `tools/signing/founder-keygen.sh`'s step 5 commands, or create a new subkey.

  Losing both backups means a primary key rotation.

### Never

- never commit or paste a private key (`.gitignore` excludes `*.key`, `*secret*.asc` and `*private*.asc`; the
  validator rejects packages containing private keys);
- never store the primary key in CI;
- never give a user instructions to disable signature checks;
- never publish unsigned once a fingerprint is configured.

## 6. Future hardening (not enabled)

- `Valid-Until` in `Release` limits replay of old metadata, but forces regular republication. It is enabled only
  after testing Sileo's behaviour with an expired `Valid-Until`.
- Authenticated approvals: approval records are protected today by Git review. HCG Connect identities or signed
  commits would make them cryptographic ([SUPPLY_CHAIN_ASSESSMENT.md](SUPPLY_CHAIN_ASSESSMENT.md)).

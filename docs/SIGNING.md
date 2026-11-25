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

The owner runs it. It is not run in CI and not in an assistant session; no private key is ever pasted into a chat, an
issue or a log. Nothing below has been done for production: no key exists until the owner creates it.

### 5.1 Prepare a protected workspace (macOS / APFS)

Applies to the founder's Mac. These commands were reviewed, not executed in this session (Linux container).

- **Offline while generating:** turn off Wi-Fi and Bluetooth, or use a machine that is never online.
- **Encrypted container for all key files:** an encrypted APFS disk image, not a plain folder:
  ```bash
  mkdir -p ~/HCGKeys   # NOT in Desktop/Documents if "Desktop & Documents" syncs to iCloud Drive
  hdiutil create -size 200m -fs APFS -encryption AES-256 -volname HCGRepoKey ~/HCGKeys/hcg-repo-key.dmg
  hdiutil attach ~/HCGKeys/hcg-repo-key.dmg            # mounts /Volumes/HCGRepoKey (asks for its passphrase)
  sudo mdutil -i off /Volumes/HCGRepoKey               # no Spotlight index of key files
  tmutil addexclusion ~/HCGKeys/hcg-repo-key.dmg       # keep it out of Time Machine; backups are made on purpose
  ```
  Keep FileVault on for the internal disk. Do not let the disk-image passphrase be saved in the Keychain.
- **GnuPG on macOS** (Homebrew `gnupg` or GPG Suite): when pinentry offers "Save in Keychain", leave it off. Run the
  procedure with `GNUPGHOME` inside the mounted volume, which the script does by writing everything under its output
  directory.
- **No clipboard:** never copy key material. Universal Clipboard can send it to other Apple devices, and clipboard
  managers keep history. Do not open key files in editors: autosave and Versions keep copies.
- **Deletion on SSD/APFS is not secure overwriting.** `shred`, `rm -P` and similar tools cannot guarantee that the old
  blocks are gone on an SSD: wear levelling and APFS copy-on-write keep old data elsewhere, and snapshots can keep
  whole copies. The protection is **encryption**. Secret files only ever exist inside an encrypted container, and a
  container whose passphrase is not kept is unreadable after it is deleted. Detach (`hdiutil detach
  /Volumes/HCGRepoKey`) when finished.

### 5.2 Generate the key

```bash
tools/signing/founder-keygen.sh /Volumes/HCGRepoKey/hcg-repo-key \
  --uid "HCG Repo Archive Key <repo@hakmicapitalgroup.com>" --primary-expire 5y --subkey-expire 2y
```

This creates a certify-only RSA 4096 primary key and a dedicated RSA 4096 signing subkey (RSA for the widest client
compatibility). It also writes:
- the public key and its fingerprint;
- the subkey-only CI secret, with the primary key absent (`sec#`);
- a passphrase-protected full backup;
- the revocation certificate.

It ends with a self-test: the subkey signs, `gpgv` verifies against the public key only, and the signer is the
primary. It refuses to write inside a Git checkout and prints no secret material. (Tested with `--test` keys in
`tests/test_signing_clients.py`.)

### 5.3 Verify, back up, protect

1. **Fingerprint:** read `public/FINGERPRINT` against the script's output, then against what will be published
   (section 5.5).
2. **Backups:**
   - copy the whole `offline/` folder into two further encrypted containers on two external drives, kept in
     different places;
   - keep `revocation-certificate.rev` separately, and optionally printed and sealed;
   - keep the passphrases in a password manager or on sealed paper, never next to the backups.
3. **Check the backups:** on another day, attach each backup and run
   `gpg --show-keys offline/primary-secret-backup.asc`. The fingerprint must match.

### 5.4 GitHub environment protection (checked for this repository)

`F1r3srp3nt/HCG-repo` is a **public** repository owned by a personal account. GitHub's own documentation (source
`github/docs` at `9207aae`, `deployments-and-environments.md`) states:
- environments, environment secrets and deployment protection rules are available in public repositories on all
  current plans;
- required reviewers: up to six; one approval lets the job proceed;
- a job cannot read environment secrets until a reviewer approves it;
- "prevent self-reviews": the person who starts a deployment cannot approve it;
- deployment branch rules exist;
- administrators can bypass protection rules unless that is disabled.

**Today `repo-signing` has no required reviewer:** the 25 Nov Publish run started its build job 3 seconds after
creation, with no waiting state. Recommended settings (Settings → Environments → `repo-signing`):

| Setting | Value | Why |
|---|---|---|
| Required reviewers | the founder (and a trusted second person if one exists) | the signing secret is released only after an explicit approval |
| Prevent self-review | **off** while the founder is the only reviewer; on once there are two | with it on, a sole reviewer could never approve their own Publish run |
| Allow administrators to bypass | **off** | the founder's own account cannot skip the approval by mistake |
| Deployment branches and tags | **Selected: `main` only** | the secret can only reach runs from `main` (the workflow also refuses other refs) |
| Wait timer | optional, e.g. 5 minutes | a last chance to cancel |
| Secret | `HCG_REPO_GPG_PRIVATE_KEY` | the subkey only |

**Entering the secret without the clipboard.** On the online Mac, copy only `ci/hcg-repo-signing-subkey.asc` into a
new encrypted disk image, never the `offline/` folder. Then:
```bash
gh secret set HCG_REPO_GPG_PRIVATE_KEY --env repo-signing --repo F1r3srp3nt/HCG-repo < /Volumes/HCGCI/hcg-repo-signing-subkey.asc
hdiutil detach /Volumes/HCGCI && rm ~/HCGKeys/hcg-ci.dmg      # see 5.1: deletion relies on the encryption
```
Then settings → Environments → `repo-signing` shows the secret (never its value).

### 5.5 Activate (one PR, then one approved publication)

1. Commit `public/hcg-repo-archive-key.asc` as `keys/hcg-repo-archive-key.asc` and set `config/repo.toml`
   `[signing] fingerprint = "<40 hex>"`. `bin/hcgrepo signing-status` must show the fingerprint, the subkey, its
   expiry and no errors. Merge after review. From this commit on, every build is fail-closed.
2. Build the keyring package from the committed key (`bin/hcgrepo keyring-deb --layout rootless --layout rootful
   --version 1.0`), and import it through the normal reviewed flow ([KEYRING.md](KEYRING.md)).
3. Publish with `require_signature` on, after the explicit publication approval; approve the environment.
   `verify-deployed` must report both channels signed and verified, and `bin/hcgrepo monitor` must show
   `signature.*: ok`.
4. Announce the fingerprint in at least two independent places: GitHub `docs/KEYRING.md` and the HCG Repo website.

### Expiry, rotation, revocation

- **Expiry:** the signing subkey lasts 2 years and the primary 5. `signing-status` and the monitor warn from 60 days,
  and the build refuses to sign within 14 days. To extend without changing the fingerprint users trust:
  1. offline, run `gpg --quick-set-expire <FPR> 2y <SUBKEY-FPR>`;
  2. commit the re-exported public key;
  3. replace the secret.
- **Subkey rotation** (scheduled, or after any suspected exposure):
  1. offline, add a new signing subkey and revoke the old one (`gpg --edit-key <FPR>` → `revkey`);
  2. commit the public key and replace the secret;
  3. publish with `allow_key_change`.

  The primary fingerprint, the keyring and users' trust stay the same.
- **Primary key rotation:**
  1. publish a keyring version holding **both** keys, and wait for adoption (Sileo stops updating a verified
     source whose signature fails);
  2. switch the fingerprint, public key and secret, and publish with `allow_key_change`;
  3. remove the old key in a later keyring version;
  4. publish the old key's revocation.

  Tested end to end with APT and the keyring package.
- **Revocation:** import `revocation-certificate.rev` into the offline keyring, then publish the updated public key
  (commit plus keyring version).

### Emergency recovery

| Event | Immediate action | Then |
|---|---|---|
| CI secret exposed or suspected (log, fork, GitHub account) | delete the environment secret; do not publish | offline: revoke the subkey and add a new one; commit the public key; new secret; publish with `allow_key_change`. Users keep trusting the same primary key |
| Primary key or its passphrase compromised | stop publishing; revoke the primary with `revocation-certificate.rev` | new key (section 5.2); keyring version with the new key only; publish; announce on independent channels. Users must update the keyring: until then a verified Sileo source stops updating, which is the safe failure |
| Signing ability expired (monitor alert, refused builds) | none needed: nothing unsigned is published | extend the expiry offline (above) |
| Backups or passphrase lost | the current subkey keeps working until it expires | plan a primary key rotation before that date |
| Unknown or unauthorized publication (monitor `publication-origin`, `unknown-packages`) | do not deploy over it blindly | investigate; publish a known `main` state with the approval process; rotate the subkey if the secret could be involved |

### Never

- never commit or paste a private key (`.gitignore` excludes the procedure's secret files, and `validate` refuses
  private key material under `keys/`);
- never store the primary key in CI;
- never use the repository signing key to approve releases ([RELEASE_APPROVALS.md](RELEASE_APPROVALS.md));
- never give a user instructions to disable signature checks;
- never publish unsigned once a fingerprint is configured.

## 6. Replay protection (`Valid-Until`): investigated, not enabled

Implemented, off: `[signing] valid_days = N` adds `Valid-Until` (build time + N days) to both channels' `Release`.
It needs a signed build. `Date` is set one hour back, because APT refuses dates in the future. Tested with real
APT 2.8 (`tests/test_signing_clients.py`, `ReplayProtectionTests`):

| Case | APT |
|---|---|
| expired `Valid-Until` | **refused** ("expired… updates will not be applied") |
| `Date` 10 seconds ahead of the client clock | accepted |
| `Date` 5 minutes or 2 hours ahead | **refused** ("not valid yet") |
| an older `Release`, still within its validity, served after a newer one | **accepted**: APT does not detect a replay inside the window |

Sileo checks only the signature (`apt-key verify`) and installs with `apt-get --allow-unauthenticated`. It has no
`Valid-Until` handling, so it is neither protected nor broken by it. Zebra checks no signatures.

**Decision: not enabled.** `Valid-Until` would protect only command-line APT users, and only by bounding the replay
window. It also requires re-signing more often than N days. That means scheduled publication, which is automatic
publication and needs the owner's separate approval. If it is enabled later, use `valid_days = 14` with a weekly
re-sign. The monitor already reports metadata freshness (`freshness.*`).

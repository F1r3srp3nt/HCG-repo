# HCG Repo Keyring (`com.hakmicapitalgroup.repo-keyring`)

**Status:** implemented and tested with **temporary keys only**. No keyring package is published or importable until
the owner approves the production fingerprint. Test builds carry `X-HCG-Test-Key: yes`, and the importer refuses them.

## Trusted fingerprint

| Key | Fingerprint |
|---|---|
| HCG Repo Archive Key (production) | **not created yet**: this table is updated in the activation PR ([SIGNING.md](SIGNING.md) §5.5) |

## What the package installs

| Path (rootless; rootful without `var/jb/`) | Content |
|---|---|
| `/var/jb/etc/apt/trusted.gpg.d/hcg-repo.gpg` | the dearmored public key(s), one binary keyring: what Sileo and APT trust |
| `/var/jb/usr/share/hcg-repo-keyring/FINGERPRINTS` | fingerprint, user ID and expiry of every included key and subkey |
| `/var/jb/usr/share/hcg-repo-keyring/hcg-repo-archive-key.asc` | an armored copy, for inspection |

- `iphoneos-arm64` for rootless and `iphoneos-arm` for rootful, gzip-compressed.
- No maintainer scripts and no conffiles, so it never touches other APT keys.
- **Updates:** a newer version replaces the keyring file. A *transition* version carries the old and the new key
  together, during a primary key rotation.
- **Removal:** `dpkg -r` (or Remove in Sileo/Zebra) deletes the files. The source keeps working unverified in Sileo
  and Zebra.
- The builder refuses a key that is expired, revoked or cannot sign, and a production build whose key does not match
  `config/repo.toml [signing] fingerprint`.

Build: `bin/hcgrepo keyring-deb --layout rootless --layout rootful --version 1.0 [--transition-key old.asc]`.
Development: add `--test-key <public.asc>`.

## Client compatibility (source evidence and tests)

| Client | Installing the keyring | Effect |
|---|---|---|
| Sileo (`c9f7015`) | ordinary package install | Sileo runs `apt-key verify` on `Release` + `Release.gpg` against `<prefix>/etc/apt/trusted.gpg.d/`, the directory this package fills. After a verified refresh, a later bad signature stops updates of the source |
| Zebra (`v1`, `e2f3c27`) | ordinary package install | none: Zebra does not fetch or check repository signatures |
| APT command line | ordinary package install | `apt update` verifies against the installed key. Tested: before the keyring, `NO_PUBKEY`; after it, accepted; after a transition update, the new key is accepted; after removal, `NO_PUBKEY` again |

The tests install the keyring with real dpkg into an isolated root, then point real APT at the trusted directory the
package created (`tests/test_signing_clients.py`, `KeyringBootstrapTests`).

## Initial trust bootstrap

The keyring is downloaded from HCG Repo itself, so its first installation is trust on first use. Before relying on it:

1. Get the fingerprint from **two independent places**:
   - this file on GitHub (`github.com/F1r3srp3nt/HCG-repo`, `docs/KEYRING.md`);
   - the HCG Repo website's install section.

   Both are served by GitHub, so the founder's other channels (e.g. HCG Store or HCG Connect pages, or a signed
   announcement) add independence.
2. After installing, compare with the device's own copy, in Filza or a terminal:
   `cat /var/jb/usr/share/hcg-repo-keyring/FINGERPRINTS`, or with GnuPG installed,
   `gpg --show-keys /var/jb/usr/share/hcg-repo-keyring/hcg-repo-archive-key.asc`.
3. On a computer: `bin/hcgrepo verify-artifact <downloaded keyring .deb>` checks that file against the index, and
   `gpg --show-keys` on the extracted `.asc` shows the fingerprint.

If any fingerprint differs, remove the keyring and report it. Do not trust the source.

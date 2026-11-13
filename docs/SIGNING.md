# Repository metadata signing

## Two different kinds of signing

| | Repository metadata signing (this document) | iOS code signing |
|---|---|---|
| What is signed | `Release`, which lists the checksums of `Packages*`, which in turn list each `.deb`'s SHA-256 | Each Mach-O inside a package |
| With | OpenPGP key (`gpg`) | `ldid` pseudo-signature, with an entitlements plist |
| When | At publish time, by this repository's workflow | When the package is built, in the product's own repository (e.g. HCG-Terminal) |
| Checked by | APT/Sileo against keys in `/var/jb/etc/apt/trusted.gpg.d/` (rootless) | The jailbreak's code-signing bypass / trust cache |

This repository never re-signs Mach-O binaries. It only *inspects* their signatures and entitlements (see `docs/PUBLISHING.md`).

## Current status

**No archive key exists yet. The repository is published unsigned.** `config/repo.toml` has `[signing] required = false` and an empty `fingerprint`. The website says "Not signed yet" because no signature is produced.

## What Sileo actually does (checked in its source, commit `c9f7015`)

- It downloads **`Release` and `Release.gpg`**. It does not read `InRelease`. This repository produces both files, for APT and for Sileo.
- It verifies the signature with `apt-key verify` against `<jbroot>/etc/apt/trusted.gpg.d/`.
- **Unsigned source:** accepted on iOS.
- **Signed, but the key is not installed on the device:** the source still loads. Sileo discards the signature and the source behaves as unsigned. Signing therefore only protects users who have installed the HCG key.
- **Signed with a trusted key:** marked valid, and the signature is cached. If a later `Release` then fails verification, Sileo refuses to update that source. **Rotating the key** therefore needs a planned transition (below).
- Package downloads are always checked against the `SHA256`/`SHA512` in the index. Sileo refuses packages that have neither.

Nothing in this repository tells users to disable signature verification. Do not suggest it as a fix.

## Creating the key (offline, once)

Do this on a trusted machine, not in CI. Use an RSA 4096 primary key with a separate signing subkey for CI:

```bash
export GNUPGHOME=$(mktemp -d)   # or your offline keyring
gpg --quick-gen-key "HCG Repo Archive Key <repo@hakmicapitalgroup.com>" rsa4096 cert 3y
FPR=$(gpg --with-colons --list-keys "HCG Repo Archive Key" | awk -F: '/^fpr/{print $10; exit}')
gpg --quick-add-key "$FPR" rsa4096 sign 2y
gpg --armor --export "$FPR" > keys/hcg-repo-archive-key.asc        # public: commit this
SUB=$(gpg --with-colons --list-keys "$FPR" | awk -F: '/^sub/{getline; print $10}' | tail -1)
gpg --armor --export-secret-subkeys "${SUB}!" > /tmp/subkey-protected.asc

# CI cannot type a passphrase: strip it from a throw-away copy that holds ONLY the subkey.
CI_HOME=$(mktemp -d /tmp/gk.XXXX)            # short path: gpg-agent sockets have a length limit
GNUPGHOME=$CI_HOME gpg --import /tmp/subkey-protected.asc
GNUPGHOME=$CI_HOME gpg --passwd "$FPR"       # enter the old passphrase, then an empty one
GNUPGHOME=$CI_HOME gpg --armor --export-secret-subkeys "${SUB}!" > /secure/location/hcg-repo-signing-subkey.asc
GNUPGHOME=$CI_HOME gpg -K --with-colons | cut -d: -f1,15   # expect "sec:#" (primary absent) and "ssb:+"
GNUPGHOME=$CI_HOME gpgconf --kill all; rm -rf "$CI_HOME" /tmp/subkey-protected.asc
```

- The file in `/secure/location/` is the CI secret. Never commit it.
- The primary key stays offline and protected by a passphrase. Back it up to encrypted storage, e.g. `gpg --export-secret-keys --armor "$FPR"`.
- If the CI subkey leaks, revoke it with the primary key and create a new one; the primary key, and the fingerprint users trust, stay the same.

Tested in this session with smaller keys: the subkey-only keyring signed a file, and `gpgv` reported a good signature from the primary identity using only the exported public key.

## Wiring it up

1. Commit `keys/hcg-repo-archive-key.asc`, and set `config/repo.toml`:
   ```toml
   [signing]
   required = true            # once you want unsigned publishing to fail
   fingerprint = "<PRIMARY KEY FINGERPRINT, 40 hex>"
   ```
2. In GitHub, open **Settings → Environments** and create or open `repo-signing`, which the publish workflow uses. Add a required reviewer if you want manual approval. Add the secret `HCG_REPO_GPG_PRIVATE_KEY` containing the armored signing **subkey**.
3. Publish. The build signs `Release` (detached `Release.gpg` and clearsigned `InRelease`, SHA-512 digest). It then verifies both with `gpgv` against **only** the committed public key, and checks that `InRelease`'s content equals `Release`. The build fails if any of that does not hold. The deployed check repeats the verification over HTTPS. The website and `build-info.json` say "verified" only when that check passed.
4. Publish the fingerprint on the website and in this file, so users can compare it.

## Getting the key onto devices

On iOS, APT and Sileo trust keys in `/var/jb/etc/apt/trusted.gpg.d/` (rootless) or `/etc/apt/trusted.gpg.d/` (rootful). The usual way to distribute a key is a small keyring package, as Procursus does for other repositories (e.g. `chariz-keyring`). It installs `<jbroot>/etc/apt/trusted.gpg.d/hcg-repo.gpg`, the dearmored public key, and nothing else. Build one `iphoneos-arm64` and one `iphoneos-arm` package with `dpkg-deb --root-owner-group`, then import them like any other package. Do not ship a keyring package before the key exists, and do not ship a maintainer script that modifies other APT keys. The validator rejects scripts that touch `trusted.gpg`.

## Rotation

1. Add the new public key to the keyring package, publish a new keyring version that contains **both** keys, and wait for adoption.
2. Switch `fingerprint`, the committed public key and the CI secret to the new key.
3. Remove the old key from the keyring package in a later version.

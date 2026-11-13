# Validation: what is checked, and the latest results

There are three layers of checks. Any **error** blocks import, build and publication. **Warnings** are printed, and they do not block.

## 1. Each package (`inspect`, `import-*`, `validate`, `build`)

| Area | Rejected (error code) |
|---|---|
| Not a package | Git LFS pointer, HTML/JSON error page, empty file, wrong `ar` magic or members, truncated `ar` member, `debian-binary` ≠ `2.0`, unreadable or corrupt compressed `control`/`data` stream (checked end-to-end through `dpkg-deb`) → `deb-unreadable` |
| Control file | missing Package/Version/Architecture/Maintainer/Description; invalid name, version or epoch; architecture not in `config/repo.toml`; unparseable relationship fields; repository-only fields (`Filename`, `Size`, checksums) inside the `.deb` |
| Layout | rootless (`iphoneos-arm64`) file outside `/var/jb`; rootful or roothide file inside `/var/jb`; `all` package with `/var/jb` paths; absolute or `..` paths; device nodes/FIFOs |
| Permissions | setuid/setgid without an explicit `allow_setuid` review; world-writable files. Not root-owned is a warning |
| Mach-O | corrupt; wrong CPU type for the architecture; unsigned (no code directory); FairPlay-encrypted; native code in `all`; rootless binary linking rootful paths (e.g. CydiaSubstrate.framework); rootful binary linking `/var/jb` |
| Entitlements | entitlements present but not recorded in `packages/<id>/package.toml`; differ from the recorded set |
| Apps | missing/unreadable `Info.plist`, bundle id or executable; executable missing, not executable or not a Mach-O executable; bundle id reserved for the App Store edition; `MinimumOSVersion` not enforced by a `firmware (>= …)` dependency |
| Maintainer scripts | not executable; no `#!`; scripts that edit sudoers, passwords, NVRAM, sshd config, APT sources or trusted keys, set setuid bits, or recursively delete system/user roots |
| Metadata | missing icon/header file; `device-tested` without a complete, passing device-test entry |

## 2. The whole repository (`validate`, `import-*`, `build`, CI)

- **Duplicates:** two pool files with the same package/version/architecture (`duplicate-package`), and non-canonical pool file names.
- **Lock:** every pool file must be locked with the same SHA-256 and size, and every published lock entry must have its file. Withdrawn entries must not be present, a missing `provenance.json` is an error, and one version may not have conflicting hashes.
- **Lock history (pull requests, `--base-ref`):** entries on `main` cannot be removed or changed, or republished after withdrawal.
- **Dependencies:** each `Depends`/`Pre-Depends` group must be satisfiable, either by a package in this repo with a compatible architecture and version (including `Provides`), or by `config/external-dependencies.toml`. Dependencies inside the `com.hakmicapitalgroup.` namespace must be satisfied by this repository.

## 3. The published tree, as a client sees it (`verify`, `smoke`, end of every `build`, after deployment)

- **`Release`:** it must parse and contain Origin, Suite, Architectures (including every configured architecture), Components, Date and SHA256.
- **Indexes:**
  - The verifier requests indexes in the order Sileo does (`.zst`, `.xz`, `.lzma`, `.bz2`, `.gz`, plain).
  - Every index that is served must be listed in `Release`, with matching size and MD5/SHA1/SHA256/SHA512.
  - Every compressed index must decompress to exactly `Packages`.
  - An unlisted stale index is an error, because Sileo would pick it first and reject it.
- **Signatures:** `Release.gpg` and `InRelease` are verified with `gpgv` against the given public key. `InRelease` without `Release.gpg` is an error, because Sileo reads only `Release.gpg`.
- **`Packages` entries:**
  - Required fields; no duplicates; the architecture must be announced in `Release`.
  - `Filename` must be a clean relative path that resolves under the source URL. Absolute paths, `..`, and a repeated `repo/` prefix are rejected.
  - **Every `.deb` is downloaded.** Its size and all checksums are checked, it is re-validated as a package, and every control field must equal its index field.
- **Presentation URLs** (`SileoDepiction`, `Depiction`, `Icon`, `Header`): each must be under the public base URL and return 200. Native depictions must be a `DepictionTabView` whose tabs all have `tabname` and `class`. Images must be PNG/JPEG.
- **Website and repository icon:**
  - `CydiaIcon.png` must be a PNG.
  - The website root must link `sileo://source/<exact source URL>`.
  - The website must offer each indexed package.

`smoke` and the CI build for each configured base URL, serve the result **under that URL's path prefix** (`/HCG-repo/` for project Pages, `/` for the custom domain), and verify it over HTTP. Requests outside the prefix return 404, as they do on GitHub Pages.

## Regression tests (`python3 -m unittest discover -s tests`)

78 tests in `tests/`, covering among others:

- **Damaged or fake packages:** truncated archives, corrupted xz payloads, LFS pointers, HTML error pages.
- **Layout mistakes:** rootless/rootful/all path errors, setuid and world-writable files.
- **Entitlements and Mach-O:** unreviewed or mismatched entitlements, unsigned and wrong-architecture binaries.
- **Apps:** reserved bundle ids, missing firmware dependencies.
- **Maintainer scripts:** forbidden script content.
- **Pool and lock:** immutability and lock history, conflicting duplicates, dependency resolution by version and architecture.
- **Path-prefix mistakes:**
  - a site built for the custom domain but served under `/HCG-repo/`;
  - a missing prefix;
  - a double `repo/` prefix;
  - an absolute `Filename`.
- **Index and download integrity:** tampered `.deb`, an inconsistent compressed index, an index changed after `Release`, a stale unlisted `Packages.zst`, a `Release` missing an architecture, and an atomic build that keeps the previous snapshot.
- **GitHub imports:** `latest`, a moved tag, a digest mismatch, a failed run, ambiguous artifacts, and the token not being forwarded to redirected downloads.
- **GPG:** signing with a throw-away key, `gpgv` verification, tamper detection, and refusal of the wrong key.

## Latest results (this session, 13 Nov 2026, Linux, Python 3.13, dpkg 1.22.6, GnuPG 2.4.4)

| Check | Result |
|---|---|
| `python3 -m unittest discover -s tests` | **78 tests, OK** |
| `bin/hcgrepo validate` | OK: 0 packages, repository valid |
| `bin/hcgrepo smoke` | OK for `https://f1r3srp3nt.github.io/HCG-repo/` (served at `/HCG-repo/`) and `https://repo.hakmicapitalgroup.com/` (served at `/`): 11 URLs each, 0 errors, 0 warnings |
| Rehearsal: Terminal-shaped `.deb` (real script's control/scripts/layout, stand-in binary) → import → build → serve at `/HCG-repo/` → verify | OK after the entitlements were recorded: 16 URLs, 1 package, 0 errors. The rehearsal ran in a scratch copy and nothing was committed |
| Website rendering (Chromium, 390 px and 1100 px) | No horizontal overflow, no failed requests |
| Deployed HTTPS endpoints | **Not tested.** Pages is not enabled yet, and `*.github.io` is not reachable from the build sandbox. The publish workflow's `verify-deployed` job performs this check |
| Sileo on a device | **Not tested.** See `DEVICE_CHECKLIST.md` |
| Metadata signature | **None.** No key exists yet. The signing path is tested with throw-away keys only |

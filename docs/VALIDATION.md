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
| Access | package of a product whose `access` is not `public` (`access-not-public`); package in the `com.hakmicapitalgroup.` namespace that is neither listed by a product in `config/products.toml` nor in `public_packages` in `config/repo.toml` (`access-unmapped`). See [STORE_INTEGRATION.md](STORE_INTEGRATION.md) |

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
  - The website must offer each indexed package and link every product page.
- **Product pages (`products/<key>/`):** each must exist and link the HCG Store sign-in and product pages, with no inline script. Pages of non-public products must say "Downloads are not available yet." and must not contain a `sileo://package/` or `.deb` link.
- **Access:** an index entry for a package that is not `public` is an error (`index-access-*`).
- **`payment_endpoint`:** must contain `[payments] endpoint` when it is set, and must not be served when it is empty.

`smoke` and the CI build for each configured base URL, serve the result **under that URL's path prefix** (`/HCG-repo/` for project Pages, `/` for the custom domain), and verify it over HTTP. Requests outside the prefix return 404, as they do on GitHub Pages.

## Regression tests (`python3 -m unittest discover -s tests`)

87 tests in `tests/`. They cover:

- **Damaged or fake packages:** truncated archives, corrupted xz payloads, LFS pointers, HTML error pages.
- **Layout and permissions:** rootless/rootful/all path errors, setuid and world-writable files, ownership judged by name the way dpkg judges it.
- **Entitlements and Mach-O:** unreviewed or mismatched entitlements, unsigned and wrong-architecture binaries, the binary minimum iOS against the declared firmware range, mixed certificate and ldid signatures inside an app.
- **Apps:** reserved bundle IDs, missing firmware dependencies.
- **Maintainer scripts:** forbidden script content.
- **Pool and lock:** immutability and lock history, conflicting duplicates, dependency resolution by version and architecture.
- **Import requests:** pinned identity (package, version, architecture, SHA-256); `latest`, short SHAs, unknown recipes and path escapes refused; build provenance recorded.
- **Path-prefix mistakes:** a site built for the custom domain but served under `/HCG-repo/`, a missing prefix, a double `repo/`, an absolute `Filename`.
- **Index integrity:** tampered `.deb`, inconsistent compressed index, an index changed after `Release`, a stale `Packages.zst`, `Release` missing an architecture.
- **Build safety:** the build is atomic and keeps the previous snapshot.
- **Site rules:**
  - install actions appear only for indexed packages, never in "In development";
  - detail pages include compatibility, screenshots, installation, limitations, changelog, removal and checksums;
  - the native depiction includes screenshots;
  - brand assets are present.
- **GitHub imports:** `latest`, a moved tag, a digest mismatch, a failed run, ambiguous artifacts, the token not being forwarded to redirected downloads.
- **GPG:** signing with a throw-away key, `gpgv` verification, tamper detection, the wrong key refused, and keyring packages for both layouts (refused without a key).
- **HCG Store:** product mapping and URLs, missing `access` treated as `login-required`, refusal of login-required, paid and unmapped packages by import, `validate`, `build` and `verify`, the `public_packages` allowance, product page content and verification, `payment_endpoint` emission and validation.

## Latest results (15 Nov 2026)

After the two packages were withdrawn and the HCG Store integration was merged (Linux, Python 3.13, dpkg 1.22.6, GnuPG 2.4.4):

| Check | Result |
|---|---|
| Unit and regression tests | **102 tests, OK** |
| `bin/hcgrepo validate --base-ref origin/main` | OK: 0 packages in pool; lock history valid (both entries kept, status `withdrawn`) |
| `bin/hcgrepo smoke` | OK for `https://f1r3srp3nt.github.io/HCG-repo/` (at `/HCG-repo/`) and `https://repo.hakmicapitalgroup.com/` (at `/`): 15 URLs each, including the three product pages; 0 packages, 0 errors, 0 warnings |

Earlier the same day, before the withdrawal:

| Check | Result |
|---|---|
| Unit and regression tests (Linux, Python 3.13, dpkg 1.22.6, GnuPG 2.4.4) | **87 tests, OK** |
| `bin/hcgrepo validate` | OK: 2 packages, repository valid. 3 warnings, all shown on the package pages: Nexus declares no firmware range; Terminal does not declare `uikittools`; Terminal embeds an Apple-signed dylib |
| Production build `bin/hcgrepo build --target production` | OK: 2 packages, verified offline before replacing `_site` |
| `bin/hcgrepo smoke` (production at `/`, old project URL at `/HCG-repo/`) | OK for both: 20 URLs each, including both `.deb` downloads with size, SHA-256 and SHA-512. 0 errors |
| Package imports in CI (Prepare packages, [run 43673491962](https://github.com/F1r3srp3nt/HCG-repo/actions/runs/43673491962)) | Nexus 0.2.0 fetched with a matching pinned SHA-256; Terminal 0.1.0-1 built on macOS from the pinned commit. Both imported, then re-inspected locally with identical hashes |
| Website QA (Chromium: 1280 px, iPhone 13, iPhone SE) | No horizontal overflow; banner aspect 2.999 against 3.000, so not cropped; no failed requests; keyboard order and visible focus; reduced motion disables the ambient effect; Copy URL writes the exact URL; contrast: body 11.5:1, muted 6.6:1, primary button 10.6:1 |
| Live HTTPS (Check transport, [run 43673491995](https://github.com/F1r3srp3nt/HCG-repo/actions/runs/43673491995)) | **Fails.** DNS is correct, but GitHub serves `*.github.io` and has no certificate for the domain. Details and fix: `TRANSPORT.md` |
| Sileo on a device | **Not tested.** See `DEVICE_CHECKLIST.md` |
| Metadata signature | **None.** No archive key yet. The signing path is tested with throw-away keys only |

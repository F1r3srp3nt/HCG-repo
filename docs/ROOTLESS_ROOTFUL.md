# Rootless, rootful and the single `/repo/` source

## What a package must look like

| Layout | `Architecture:` | Files installed under | Example jailbreaks |
|---|---|---|---|
| **Rootless (primary)** | `iphoneos-arm64` | `/var/jb/…` only. `/var/jb` is the jailbreak root; it may be a symlink to a relocated root such as `/private/preboot/<UUID>/jb-<random>/procursus` | Dopamine, palera1n rootless |
| Rootful | `iphoneos-arm` | `/` (e.g. `/Applications`, `/usr/bin`); never `/var/jb` | palera1n rootful (`-f`), older checkra1n/unc0ver-era setups |
| Roothide | `iphoneos-arm64e` | jbroot-relative, randomised | **not supported here** |
| `all` | `all` | data-only packages with no jailbreak-root paths and no native code | rarely appropriate |

Sources:

- The Theos rootless guide says `THEOS_PACKAGE_SCHEME=rootless` sets `Architecture: iphoneos-arm64` and the install prefix `/var/jb`.
- Procursus's `iphoneos-arm64-rootless` target uses `MEMO_PREFIX=/var/jb`.

### Build guidance

- **Theos:** set `THEOS_PACKAGE_SCHEME = rootless` (rootless) or leave it unset (rootful), build each scheme separately, and produce one `.deb` per scheme.
- **Paths in code:** never hard-code `/var/jb`. Use [libroot](https://github.com/opa334/libroot) (`JBROOT_PATH_CSTRING(...)`, `JBROOT_PATH_NSSTRING(...)`), which resolves the jailbreak root at runtime. A relocated or symlinked `/var/jb` then works without assumptions.
- **App bundles:** `/var/jb/Applications/<Name>.app` (rootless) or `/Applications/<Name>.app` (rootful). `postinst` should call `uicache -p <path>`, and `Depends` should include `uikittools`, which provides `uicache`.
- **Mach-O:** arm64 (arm64e is accepted too). Each Mach-O must be signed: an ldid pseudo-signature is acceptable, and its entitlements are reviewed per package.

The validator enforces the path, architecture, signing and library rules. It rejects:

- a rootless package that installs anything outside `/var/jb`;
- a rootful or `all` package that installs into `/var/jb`;
- native code in an `all` package;
- rootless binaries that link rootful-only locations such as `/Library/Frameworks/CydiaSubstrate.framework`;
- rootful binaries that link `/var/jb/...`.

## How clients pick candidates (why one source is enough)

Sileo was checked against its source code (Sileo/Sileo, commit `c9f7015`):

- It keeps only packages whose `Architecture` is `all`, the device's dpkg architecture, or one of its foreign architectures (`DPKGArchitecture.swift`, `PackageListManager.swift`). A rootless device therefore sees only `iphoneos-arm64` builds, and a rootful device sees only `iphoneos-arm`.
- It refuses a source unless the `Release` file's `Architectures:` lists the device's architecture. This repository's `Release` always lists `iphoneos-arm64 iphoneos-arm`, so the same `/repo/` source works on both layouts, even while it holds only rootless packages.
- Official Sileo does not know `iphoneos-arm64e`. That is one reason roothide is not accepted (`config/repo.toml` → `architectures`).

So **`/repo/` is the single primary source**, and rootless packages come first. Rootful builds of the same package use the same package id with `iphoneos-arm`, and the website shows both builds on one card.

## If separate sources are ever needed

Some clients may not filter correctly, and a package may need a different identity per layout. In that case, use a separate source directory for rootful packages, built from the same tool:

1. Copy this repository's configuration into a second checkout, or add a second `repo_subdir` build, with `architectures = ["iphoneos-arm"]` and `repo_subdir = "rootful"`.
2. Publish it next to the primary source:
   - `https://f1r3srp3nt.github.io/HCG-repo/rootful/` (test);
   - `https://repo.hakmicapitalgroup.com/rootful/` (production).
3. Keep `https://…/repo/` as the documented primary (rootless) source. Do not move existing packages, because users' configured URLs must keep working.

Nothing needs to be split today.

## Claims

The website shows each package's layout and the firmware range **declared by the package** (`firmware (>= x)` in `Depends`). A version is shown as **device-tested** only with recorded evidence. A successful compile or validation run is not evidence that a package works on an iOS version or jailbreak, and this repository never presents it as such.

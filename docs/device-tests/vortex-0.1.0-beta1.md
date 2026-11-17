# HCG Vortex 0.1.0~beta1: test record

**Package under test:** `com.hakmicapitalgroup.vortex_0.1.0~beta1_iphoneos-arm64.deb`, 81,650 bytes,
SHA-256 `bb0af72745a36158d4022c1b97ebc9800847cf4b1fb42ca14d05b9fc07673eea`.
- Built from HCG-Jailbreak-Research commit `7d5e5b9153e9e7246d87e6f3bf4f7c6927d42ad4`, run 43970942708, artifact 14278312698.
- This failing artifact's provenance stays in `pool/…/com.hakmicapitalgroup.vortex_0.1.0~beta1_iphoneos-arm64.deb.provenance.json`.

This record holds only what was observed. A step that was not done stays **not done**.

## Installation attempt, 17 Nov 2026: FAILED

| Field | Value |
|---|---|
| Device | iPhone 11 Pro Max. The recording does not show the model; it was confirmed by the owner |
| iOS | 17.4, confirmed by the owner (build not recorded) |
| Jailbreak | Dopamine 2.x, rootless (the log shows `/var/jb/usr/bin/dpkg`) |
| Package manager | Zebra 1.1.x, confirmed by the owner |
| Source | The old diagnostic source `https://repo.hakmicapitalgroup.com/diag/d8-candidate/` ("HCG Repo Candidate"), now replaced by `https://repo.hakmicapitalgroup.com/repo/candidate/` |
| Evidence | A screen recording (25.8 s, SHA-256 `b1b537b6…38d4db41`, kept by the owner). Frames: [install log](evidence/2026-11-17-vortex-beta1-zebra-install-log.jpg), [listing](evidence/2026-11-17-vortex-beta1-zebra-listing.jpg) |

**What the recording shows:**

| Step | Observed |
|---|---|
| Source refresh and listing | Worked. Zebra showed the source's banner, "All Packages 1" and "Tweaks 1", and **HCG Vortex (Beta)** with the spiral icon, version 0.1.0~beta1, size 82 KB (installed size 578 KB) and source "HCG Repo Candidate" |
| Web depiction in Zebra | It loaded, but **without styling**: plain browser-default text and unstyled links. The cause was the depiction's stylesheet path, which was wrong for a source two directories deep. Fixed for every source depth, and `verify` now checks that every web depiction's stylesheet loads |
| Download | Worked ("Done HCG Vortex (Beta)") |
| Unpack | **Failed**, with the error below. Nothing was installed |
| Runtime (Engine Status, effects, respring, uninstall) | **Not tested**: the package never installed |

The install log, as shown:

```
Unpacking com.hakmicapitalgroup.vortex (0.1.0~beta1) ...
dpkg-deb: error: <decompress> subprocess was killed by signal (Bus error: 10)
dpkg: error processing archive /private/var/mobile/Library/Application Support/xyz.willy.Zebra/debs/com.hakmicapitalgroup.vortex_0.1.0~beta1.deb (--unpack):
 dpkg-deb --fsys-tarfile subprocess returned error exit status 2
Errors were encountered while processing: ...
```

**Recorded in `package.toml`:** `stage = "install"`, `result = "fail"`. The listing says "Installation failed in testing"
and does not offer it as a normal install.

## Server-side analysis (not a device result)

- **The file is valid.** `dpkg-deb` 1.22.6 on Linux and Python's liblzma both unpack it, to 560,640 bytes. The checksums
  match the index.
- **It decompresses in the step that failed.** `control.tar.gz` unpacked on the device; the crash is in decompressing
  `data.tar.lzma`.
- **Its data member is unusual.** It uses the **legacy LZMA ("lzma-alone") format** with a **64 MiB dictionary**
  (properties 0x5d) and no stored size. A decoder for this format allocates the full declared dictionary, so
  unpacking an 81 KB package asks for about 64 MiB. This is a **hypothesis, not a confirmed cause**.
- **Same format elsewhere.** HCG Nexus 0.2.0 has the same format (`data.tar.lzma`), so it would be exposed to the same
  failure. HCG Terminal Unleashed uses `data.tar.gz`.
- **Where it comes from.** `HCGVortex/Makefile` sets no compression, so the format is Theos's packaging default.

The diagnosis handoff to the Vortex project is in [../vortex/INSTALL-FAILURE-0.1.0-beta1.md](../vortex/INSTALL-FAILURE-0.1.0-beta1.md).

## Runtime smoke test: not started

It can only run once a build installs. The procedure is HCG-Jailbreak-Research
`HCGVortex/docs/DEVICE_TEST_HANDOFF.md`, steps 0 and A–G, with recovery in §7.

## 0.1.0~beta2: not yet attempted

**Packaging compatibility candidate — installation and runtime not yet device-verified.**

This is a packaging-only rebuild with gzip members (`control.tar.gz`, `data.tar.gz`).
- Commit `b212cdbdcda98fdda0d7a924cb8acd388a2183ed`, Xcode run 44012915117, artifact 14297382250 (`hcg-vortex-rootless-deb`).
- `com.hakmicapitalgroup.vortex_0.1.0~beta2_iphoneos-arm64.deb`, 136,634 bytes, SHA-256
  `a326759a697899ee938b3db3ccc62521994c01084880b43802c0769f90179395`.

**Checked before import (server side):**
- Size, SHA-256, run → commit and artifact digest all match.
- Control: `0.1.0~beta2`, `iphoneos-arm64`, with the same `Depends` as beta1.
- Both members pass `gzip -t` and decompress fully: `data.tar.gz` gives 560,640 bytes, and all 9 files are read to the end.
- `dpkg-deb --fsys-tarfile` exits 0.
- Against beta1, the payload has the same 19 paths; 16 files are byte-identical. In the dylib and the Settings
  binary, the only differences are:
  - the embedded version string (`0.1.0-beta1` → `0.1.0-beta2`, as ASCII and UTF-16);
  - each slice's Mach-O UUID;
  - the code signatures.

  `Info.plist` differs only in its version. File sizes are unchanged.

| Step | Observed |
|---|---|
| Install | not done |
| Runtime | not done |

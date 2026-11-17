# HCG Nexus 0.2.1~beta1: test record

**Package under test:** `com.hakmicapitalgroup.hcgnexus_0.2.1~beta1_iphoneos-arm64.deb`, 450,270 bytes,
SHA-256 `948465eaa30fd8cf1bc9fd98141362fe08c4e24ab0b23d788a39fa65d8ac17af`.
- Built from HCG-Nexus commit `58e2dbd81e6cca1bf3589d864403b05ebb8d966e` (branch `claude/nexus-0.2.1-beta1`), run 44019802883
  (#13), artifact 14300601854 (`hcg-nexus-rootless-deb`, zip `sha256:d3ac321b…2d5235`).
- The tweak and Settings code is that of 0.2.0 run #10 (HCG-Nexus `1341669`); only the version strings and the packaging
  (gzip instead of lzma) differ. Build ID shown on the phone: `58e2dbd81e`.
- Members: `debian-binary`, `control.tar.gz`, `data.tar.gz`. No maintainer scripts. Depends:
  `mobilesubstrate | ellekit, preferenceloader`.

This record holds only what was observed. A step that was not done stays **not done**.

## Status: NOT YET DEVICE-TESTED

| Field | Value |
|---|---|
| Intended device | iPhone 13 Pro (`iPhone14,2`), iOS 16.5, Dopamine (rootless), ElleKit, Zebra (owner-confirmed setup, 16–17 Nov 2026) |
| Source | HCG Repo Candidate, `https://repo.hakmicapitalgroup.com/repo/candidate/` |
| Install | not done |
| Runtime (Status, handle, panel, lock, rotation, disable, kill switch, uninstall) | not done |

The step-by-step checklist is HCG-Nexus `docs/DEVICE-CHECKLIST.md` (branch `claude/nexus-0.2.1-beta1`).

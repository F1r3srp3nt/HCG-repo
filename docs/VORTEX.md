# HCG Vortex: free experimental beta

**Status:** 0.1.0~beta1 is imported on the review branch by **Prepare packages** (run 43975978906, commit `b523c16`).
- The pool file's SHA-256 is `bb0af727…07673eea`, matching the pin.
- Provenance records run 43970942708, commit `7d5e5b9`, artifact 14278312698 and GitHub's digest of the artifact zip.
- `inspect` and `validate` pass: entitlements are null on both binaries; it links system frameworks and CydiaSubstrate only; `postrm` only.

Nothing is published. No compatibility claim is made: the listing says **Not yet device-tested** until device
evidence is recorded. The iOS line says 15.0 is the declared install minimum, not tested compatibility.

**Source project:** `F1r3srp3nt/HCG-Jailbreak-Research`, directory `HCGVortex/`, on branch
`claude/optimistic-sagan-xsa3b2`. The repository's `main` has only an initial commit. That is why a code search,
which covers default branches only, found nothing earlier.

## Builds

| | 0.1.0~beta1 (to import) | 0.1.0~alpha (historical only) |
|---|---|---|
| Commit | `7d5e5b9153e9e7246d87e6f3bf4f7c6927d42ad4` "Release candidate: HCG Vortex 0.1.0~beta1" | `9a5316b0682b36a94d8145f9e2240ffe423ce563` |
| CI run | 43970942708, macOS, Xcode 16.4 | 43931303552 |
| Artifact | `hcg-vortex-rootless-deb` (kept until 2027-02-14, per the project) | — |
| `.deb` | `com.hakmicapitalgroup.vortex_0.1.0~beta1_iphoneos-arm64.deb` | `com.hakmicapitalgroup.vortex_0.1.0~alpha_iphoneos-arm64.deb` |
| SHA-256 | `bb0af72745a36158d4022c1b97ebc9800847cf4b1fb42ca14d05b9fc07673eea`, as reported by the project's CI and handoff | `3933e2b3c9c47a50c5eb3dc0f09168cca31faf3f8fe6f4be440df7c4d2053379` |
| Use | `imports/hcg-vortex-0.1.0-beta1-rootless.toml` | **Never imported, never relabelled as beta1** |

`control` at 7d5e5b9 says: Package `com.hakmicapitalgroup.vortex`, Name "HCG Vortex (Beta)", Version
`0.1.0~beta1`, Architecture `iphoneos-arm64`, Depends `mobilesubstrate (>= 0.9.5000), preferenceloader, firmware (>= 15.0)`.
The listing takes all of these from the imported file, not from this table.

**How the import is verified.** This session could not read that repository's GitHub API: attaching it with
credentials was not permitted. So the artifact is fetched by **Prepare packages** with the workflow token, the
same way HCG Nexus was imported from its public repository. Before anything enters the pool, it checks:
- the run built exactly that commit and succeeded;
- the artifact is the only one with that name, and its zip matches GitHub's digest;
- the `.deb` matches the pinned SHA-256;
- the control file says that package, version and architecture;
- the package passes the full review: entitlements, Mach-O, layout, dependencies, and the access exception below.

Any mismatch refuses the import. The result is committed to the review branch only.

## Access and commercial policy

- **Free beta.** This beta is **proprietary freeware, free with no sign-in**: `[access] level = "public"`.
- **Scoped exception.** It is an explicit exception to the rule that HCG packages need HCG Connect sign-in, and it
  covers **only the versions in `exception_versions`** (now `["0.1.0~beta1"]`). Any other Vortex version is
  refused by `import` and `validate` (`access-exception-version`) until someone decides its access and lists it.
  That includes a later beta, a release, or a build with premium features. A future paid or premium artifact can
  therefore never become public automatically.
- **HCG Store.** There is no Store product page yet, so `config/products.toml` has no `store_slug` and the
  product page shows no Store actions. Future versions may offer a free core plus optional premium features
  through HCG Store; add the `store_slug` when a real Store page exists.
- **Other products are unchanged:** HCG Nexus is `paid`, Terminal Unleashed is `connect`.

## Listing

- **Text.** Adapted from the Vortex project's own proposal (`HCGVortex/distribution/hcg-repo/`, commit `29d8565`):
  notes, known issues, tested and untested setups, install, removal and recovery, and the changelog.
- **Identity.** Description, name, version, dependencies and checksums come from the `.deb`.
- **Badges and status.** **Beta**, **Experimental**, **Free · no sign-in**. Device testing reads
  **Not yet device-tested** until a device test is recorded. Every recorded test, including failures, is shown
  with its exact device, iOS version and jailbreak.
- **Known-issue rule.** A failed or partial test must be described under Known issues, naming that device; the
  validator enforces this.
- **Artwork.** `icon.png` is the official spiral artwork, confirmed on 17 Nov 2026. It is a 180×180 export of the project's
  `HCGVortex/distribution/icon-512.png`, and the same image as the Settings icon inside the `.deb`.

## Status (18 Nov 2026)

- **0.3.1~beta1 is the offered candidate.** Label: "Experimental beta — fixes the visual bugs seen in the 0.3.0
  recording; not yet installed on a device".
  - Imported from the Vortex handoff's pinned request: commit `e3b66044916c4b40ca5d8f3c0f12f4df93970c3f`, run
    44220895787, artifact 14389878167 (zip digest `4d0baf96…916f628f`), 227,306 bytes, SHA-256 `ff1d1b5f…f62f19e42`.
  - Checked: gzip members pass `gzip -t`, the data tar reads fully, `dpkg-deb --fsys-tarfile` exits 0, control equals
    the source commit's `control` (apart from Installed-Size), `inspect` passes.
  - The `.deb`'s own Description says "Partially tested on one device"; that refers to the recording of the previous
    build. The listing says exactly what was and was not seen.
- **0.3.0~beta1 is unchanged** (SHA-256 `0711465a…e55d6`). A camera recording on 18 Nov 2026 points to it but does
  not show the installed version, so it is described, not recorded as a device test
  (`docs/device-tests/2026-11-18-owner-device-evidence.md`).

## Earlier status (17 Nov 2026)

- **0.1.0~beta2 is the offered candidate.** Label: "Packaging compatibility candidate — installation and runtime
  not yet device-verified."
  - The free public candidate demo was approved by the owner on 17 Nov 2026 (`exception_versions`).
  - It is the same tweak repackaged with gzip.
  - Imported from the Vortex handoff's pinned request: commit `b212cdbdcda98fdda0d7a924cb8acd388a2183ed`, run
    44012915117, artifact 14297382250, 136,634 bytes, SHA-256 `a326759a…90179395`. The handoff run was 44013411517.
  - Verification before import is recorded in `docs/device-tests/vortex-0.1.0-beta1.md`.
- **0.1.0~beta1 is kept unchanged**, with its provenance, as an older candidate version.
  - Its **installation failed** on iPhone 11 Pro Max, iOS 17.4, Dopamine 2.x, Zebra 1.1.x (dpkg-deb Bus error 10).
    That is recorded in `package.toml` (`stage = "install"`, `result = "fail"`).
  - Diagnosis: `docs/vortex/INSTALL-FAILURE-0.1.0-beta1.md`.
- **Never in the stable source.**

## Two separate tests (for the next build)

1. **Source and depiction check, without installing.** Add `https://repo.hakmicapitalgroup.com/repo/candidate/`,
   refresh, open the source, then open HCG Vortex's page.
2. **Runtime smoke test, after a successful installation.** Follow HCG-Jailbreak-Research
   `HCGVortex/docs/DEVICE_TEST_HANDOFF.md`, A–G with recovery in §7, and record the observed results.

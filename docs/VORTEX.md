# HCG Vortex: free experimental beta

**Status:** listing prepared; the 0.1.0~beta1 import is pinned and runs through **Prepare packages** on the review
branch. Nothing is published. No compatibility claim is made: the listing says **Not yet device-tested** until
device evidence is recorded.

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
- **Artwork.** `icon.png` is still a **stand-in**. The official square artwork was mentioned but has not been
  received. When it arrives, export it at 180×180 PNG as `packages/com.hakmicapitalgroup.vortex/icon.png`.
  The project's own spiral icon and BETA header are not used until the official artwork is confirmed.

## Two separate tests

1. **Sileo source and depiction test, without installing.** This belongs to the suspected Sileo crash
   (`docs/SILEO_CRASH.md`). With the import committed on the review branch:
   - run **Publish candidate (diagnostics only)** for that branch;
   - in Sileo, add `https://repo.hakmicapitalgroup.com/diag/d8-candidate/`, refresh, open the source, then open
     HCG Vortex's page;
   - **do not install.** Record each step's result.
2. **Vortex runtime test, after installation.** Separately, follow the project's
   `HCGVortex/docs/DEVICE_TEST_HANDOFF.md`, starting with the kill switch on. Record the result under
   `[versions."0.1.0~beta1"]` in `package.toml`, pass or fail, with evidence. A failure also goes into
   `known_issues`.

## Prerequisite: publishing `main` (dbb3e01) over live (3a8fe2e)

**Publish candidate** refuses to run unless production already serves `main`. Publishing `main` as it is now,
before this branch is merged, is **not** a no-change operation. Both commits were built with their own
`SOURCE_DATE_EPOCH`. Rebuilding 3a8fe2e reproduces the live `Release` byte for byte. Against it, `dbb3e01`
changes exactly three files:

| File | Change | Effect |
|---|---|---|
| `/repo/Release` | `Date:` 15 Nov 2026 05:34:33 → 16 Nov 2026 14:31:54 GMT; nothing else | Sileo downloads a `Release` with a new hash on its next refresh. Every `Packages*` file and every checksum listed in `Release` is unchanged, so the index is not re-read as different |
| `/build-info.json` | revision 3a8fe2e → dbb3e01, date as above | Identifies the new build |
| `/index.html` | the "Built … from" line | Website only |

Unchanged: `Packages`, `.xz`, `.gz`, `.bz2`, the icons, `sileo-featured.json`, the banner, `payment_endpoint`
(still absent), the product pages and the 404 page. Commits after 3a8fe2e changed only tooling, tests and
documentation: the Sileo-style verifier, Store release import, staging tools and docs. Publishing the review
branch instead would also add the Vortex product page. After the import it would add the Vortex package to
`/repo/`, which must wait for both tests above.

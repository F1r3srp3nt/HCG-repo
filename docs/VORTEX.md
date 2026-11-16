# HCG Vortex: free experimental beta

**Status (16 Nov 2026): listing prepared, nothing released or published.** No Vortex `.deb`, release or
import manifest exists in any repository this project can see. The product page says "Experimental beta, not yet
released" and offers nothing to install. No placeholder package exists, and none will be made.

## Decisions recorded

| | Value | Where |
|---|---|---|
| Access | **Public, no sign-in.** An explicit exception to the rule that HCG packages need HCG Connect sign-in. It applies to this package only | `packages/com.hakmicapitalgroup.vortex/package.toml` `[access] exception` |
| Package ID | `com.hakmicapitalgroup.vortex` | `config/products.toml` (`hcg-vortex`) |
| Kind | Jailbreak tweak | `config/products.toml` |
| Layout | Rootless only, `iphoneos-arm64` | `config/products.toml`; the `.deb` decides the real architecture |
| Channel | Beta | `[release] channel = "beta"` |
| HCG Store | Not sold or licensed there, so there is no `store_slug` and the page shows no Store actions | `config/products.toml` |

Every other product keeps its access level: HCG Nexus `paid`, Terminal Unleashed `connect`, Sentinel none. A test
checks this.

## What the listing shows

It was prepared now and is filled in from the `.deb` at import.
- **Identity.** Package ID, name, version, architecture, dependencies, size, MD5/SHA1/SHA256/SHA512 and the
  description come **only from the imported `.deb`**. `package.toml` cannot override them, and its `summary` is left
  empty on purpose.
- **Badges.** **Beta**, **Experimental**, and **Free · no sign-in**.
  - It becomes **Device-tested** only with a passing device test that has evidence; the validator enforces this.
  - A failed test never upgrades it.
- **Tested devices.** A table of every recorded test for that version: device, iOS, jailbreak, result (*Works*,
  *Partly works*, *Does not work*) and date. With none, it says "**Not yet tested on any device.** No device, iOS
  version or jailbreak is known to work with this build." The same appears in the Sileo depiction.
- **Known issues.** These are required for a beta. A failed or partial device test must be described there; the
  validator refuses "None recorded yet" next to one.
- **Icon.** A **proposed** 180×180 icon in the HCG Repo style (`packages/com.hakmicapitalgroup.vortex/icon.png`).
  Replace it with official Vortex art if the project supplies some, keeping the same name, PNG format and size.
- **Header.** None: Sileo shows the HCG Repo banner.

## Inputs still required

1. **The verified release artifact.** The `.deb` for `com.hakmicapitalgroup.vortex`, `iphoneos-arm64`, from a
   pinned source: a GitHub release asset at an exact tag and commit, a workflow-run artifact, or a build recipe.
2. **The import manifest.** Its fields go into `imports/hcg-vortex-<version>.toml` in this repository's request
   format:

   ```toml
   [request]
   package = "com.hakmicapitalgroup.vortex"
   version = "<exact Version in the .deb>"
   architecture = "iphoneos-arm64"

   [source]
   kind = "github-release"                    # or "github-artifact" (run_id + artifact)
   repository = "<owner>/<vortex repository>"
   tag = "<exact tag, never latest>"
   asset = "<file name>.deb"
   commit = "<40-character commit the tag points to>"
   sha256 = "<SHA-256 of the verified .deb>"
   ```

   If the manifest gives a SHA-256, the import refuses any other file. It also refuses a different package ID,
   version or architecture.
3. **A changelog entry** for that version: `packages/com.hakmicapitalgroup.vortex/CHANGELOG.md`, `## <version>`.
4. **Device-test evidence** for any compatibility claim, recorded as described in `docs/DEVICE_CHECKLIST.md`. It
   is added to `package.toml` under `[versions."<version>"]`, with `device_tests` entries:
   - fields: device, ios, jailbreak, date, result, evidence;
   - status stays `experimental` until a test passes;
   - real problems found go into `known_issues`.

With these four inputs, nothing else in this repository needs to change.

## Steps once the inputs exist

1. **Import.** Commit the request file on a branch. **Prepare packages** fetches the pinned artifact, validates it
   and commits it to the pool for review.
   - Locally the equivalent is:
     `bin/hcgrepo import-deb <file.deb> --built-from <owner/repo>@<commit>`.
   - The checks: identity, architecture, Mach-O and entitlement review, setuid, layout, dependencies, and the
     public-access exception.
2. **Source-level check before publication (the suspected Sileo crash).** This is kept separate from Vortex's own
   runtime test. Vortex makes HCG Repo's index non-empty and adds a depiction and an icon. These are exactly the
   components the crash ladder isolates (d4, d5).
   - Run **Publish candidate (diagnostics only)** with the review branch.
     - It serves the next production source at `https://repo.hakmicapitalgroup.com/diag/d8-candidate/`
       (named "HCG Repo Candidate").
     - It also serves the ladder d1 to d7.
     - It refuses to run unless `/repo/` stays byte-identical.
   - On the device: add the candidate source, refresh it, open it, and open the HCG Vortex entry.
   - Record each step's result as in `docs/SILEO_CRASH.md`. Do not install yet.
3. **Vortex runtime test, separately.** Install from the candidate source on a rootless device and record the
   result in `package.toml`, pass or fail, with evidence. Any problem goes into `known_issues`.
4. **Review and publish.** Review the branch. Merge. Run **Publish** with diagnostics unticked to replace `/diag/`.
   Then run **Check transport**.

## Not covered

- What HCG Vortex does. The listing will use the `.deb`'s own description; nothing about its function was written
  in advance.
- Whether Vortex works on any device. Nothing is claimed until device evidence exists.

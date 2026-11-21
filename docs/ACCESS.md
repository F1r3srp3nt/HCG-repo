# Access levels and protected delivery

## Policy

| Level (`[access] level` in `packages/<id>/package.toml`) | Who can install | Where the `.deb` lives |
|---|---|---|
| `public` | Anyone | `pool/` in this repository, served from `/repo/debs/` |
| `connect` | Users signed in with **HCG Connect** | Protected storage behind HCG Connect. **Never** in this repository or the public site |
| `paid` | HCG Connect sign-in **and HCG Store ownership** | Same as `connect` |

Repository metadata and branding are public. That covers `Release`, `Packages`, the icons, the featured banner and depictions, and Sileo must be able to read them without signing in. There is no HTML login page in front of APT metadata, and no session token ever goes into a source URL.

Every package must declare its level (`config/repo.toml` → `[access] require_explicit = true`).

**Free-demo policy for candidate builds (owner, 17 Nov 2026).** Every HCG candidate build the owner approves is a
**free public demo**: no HCG Connect sign-in and no payment, installable by anyone from HCG Repo Candidate
(`/repo/candidate/`). The rules:

- **Approval is per version.** Record it as that version in the package's `exception_versions`. No other version
  becomes free.
- **Candidate only.** A free demo of a `connect` or `paid` package can never be in the stable (production) source.
  - An import into stable is rejected (`free-demo-stable`).
  - So is a promotion with `hcgrepo channel … stable`, or a hand-edited lock.
  - The product's releases keep their own access level.
- **Not a compatibility claim.** Device compatibility stays **unverified** until the owner records a device test.

**Per-version exceptions.** `[access] exception` (who approved it, when, why) plus `exception_versions` makes exactly
those versions public; every other version keeps the package's level (`PackageMeta.access_for`). A paid package can
therefore have one approved free candidate beta while its releases stay paid and protected. An unlisted version is
never public by accident, a protected record cannot be made public (`protected-but-public`), and withdrawing an
approval while the file is in the pool is an error (`protected-in-public-pool`).

Current decisions (18 Nov 2026):

| Package | Level |
|---|---|
| HCG Nexus (`com.hakmicapitalgroup.hcgnexus`) | `paid`. **Exception (owner, 17 and 18 Nov 2026):** 0.2.1~beta1, 0.2.2~beta1 and 0.3.1~beta1 are free in the candidate channel (0.3.0~beta1 is approved but never imported); every other version stays paid |
| HCG Terminal Unleashed (`com.hakmicapitalgroup.hcgterminal.unleashed`) | `connect` |
| HCG Halo (`com.hakmicapitalgroup.halo`) | `connect` (HCG default; production access not yet decided). **Exception (17 Nov 2026, owner):** HCG Halo candidate builds are free public demos; 0.1.0~beta1, 0.2.0~beta1 and 0.3.0~beta1 (approved 18 Nov 2026) are listed, candidate channel only |
| HCG Animation Center (`com.hakmicapitalgroup.animationcenter`) | `connect` (HCG default; production access not decided). **Exception (owner, 18 Nov 2026):** 0.1.0~beta1 only is a free public beta demo, candidate channel only |
| HCG Portal (`com.hakmicapitalgroup.portal`) | `connect` (HCG default; production access not decided). **Exception (owner, 18 Nov 2026):** 0.1.0~beta1 is a free public beta demo, candidate channel only. 0.1.0~beta3 is staged in `exception_versions` **pending the owner's per-version approval** (requested 21 Nov 2026) |
| HCG Vortex (`com.hakmicapitalgroup.vortex`) | `public` by a scoped exception (owner, 16–18 Nov 2026): 0.1.0~beta1, 0.1.0~beta2, 0.3.0~beta1 and 0.3.1~beta1, candidate channel only |

## What the repository enforces

- **The pool stays public-only.** A `connect`/`paid` package in `pool/` is a validation error (`protected-in-public-pool`).
- **Imports of protected packages:** importing one (`import-deb`, `import-request`, **Prepare packages**) validates it fully and records its control data, size and hashes in `pool/<arch>/<file>.provenance.json`. The lock records it as status `protected`. The file itself is **not** stored.
- **Moving an existing package out:** `bin/hcgrepo protect --package … --version … --arch … --move-to <dir outside the repo>` moves an already-imported package out of the public pool and keeps its metadata.
- **Builds:**
  - Protected files are never written to the output, and an install link for a protected package fails the build unless a payment endpoint is configured.
  - Without a payment endpoint, protected packages are **left out of the index**. The website lists them under **Sign-in required** as *Not yet available*, with no install action.
  - With a payment endpoint (`[access] payment_endpoint = "https://…"`), they are indexed with `Tag: cydia::commercial` and `Filename: protected/<file>`. That path is never deployed. The build also publishes `repo/payment_endpoint`.
- **Verification:**
  - Every `protected/` path must **not** return 200 (`protected-exposed`).
  - Protected entries must be tagged commercial.
  - A `payment_endpoint` must be HTTPS.
- **CI artifacts:** **Prepare packages** deletes its build artifacts after importing, because artifacts of a public repository can be downloaded by any GitHub user.

## Status: protected delivery is NOT live

HCG Connect is not configured (HCG-Server reports `not-configured`), and no payment-provider endpoint exists. As a result:

- Neither package can be installed yet, and the public index is empty.
- This is a **separate blocker** from the HTTPS problem (`TRANSPORT.md`). Fixing HTTPS makes the source load with its name and icon, but it will still show 0 packages until protected delivery is live, or until a package is published as `public`.

## What HCG Connect must implement (Sileo Payment Provider API)

Checked against Sileo's source (`Sileo/Sileo` at `c9f7015`, `Backend/Payment Manager/`).

1. **Repository side (done here when configured):** `https://repo.hakmicapitalgroup.com/repo/payment_endpoint` contains one HTTPS URL, the provider base. Sileo refuses non-HTTPS providers.
2. **`GET {base}info`** returns JSON with `name`, `description` and optionally `icon`, shown in Sileo's payment-provider list.
3. **Sign-in:** Sileo opens `{base}authenticate?udid=…&model=…` in `ASWebAuthenticationSession`. The provider runs the HCG Connect sign-in there and finishes by redirecting to `sileo://authentication_success?token=<token>&payment_secret=<secret>`. Sileo keeps the token in the Keychain.
4. **`POST {base}user_info`** (body: `token`, `udid`, `device`) returns `{ "items": [<owned package ids>], "user": { "name": …, "email": … } }`.
5. **`POST {base}package/{id}/info`** returns `{ "price": "<display string>", "purchased": 0|1, "available": 0|1 }`.
   - For `connect` packages: `purchased: 1` once the user is signed in.
   - For `paid` packages: `purchased` reflects HCG Store ownership.
6. **`POST {base}package/{id}/purchase`** returns `{ "status": 0|1|-1, "url": <optional action URL> }`. This is the HCG Store checkout. `0` means immediate success, `1` means action required (Sileo opens `url`), and `-1` means failed.
7. **`POST {base}package/{id}/authorize_download`** (body: `token`, `version`, `repo`, `architecture`, `udid`, `device`):
   - For an authorised user, it returns `{ "url": "https://…" }`: a short-lived signed URL to the **exact** file whose SHA-256 is in the index. Sileo rejects non-HTTPS URLs and checks the hash.
   - Otherwise it returns `{ "error": "…", "recovery_url": … }`.
8. **`POST {base}sign_out`.**

Protected files must come from storage that only the provider can sign URLs for. The files are pinned by `repo-lock.json`:

| File | SHA-256 |
|---|---|
| `com.hakmicapitalgroup.hcgnexus_0.2.0_iphoneos-arm64.deb` | `1987156996c2e76fb005bb66590aff76b77c474c4387ede6e015f19a081616a5` |
| `com.hakmicapitalgroup.hcgterminal.unleashed_0.1.0-1_iphoneos-arm64.deb` | `7fdb735e74e551fa05a23a81714ff537f97556a67d600f49f090b543ef1d42bf` |

When the endpoint answers `info` correctly:
1. Set `payment_endpoint` in `config/repo.toml`.
2. Rebuild.
3. Confirm with `bin/hcgrepo verify` that the `protected/` paths return 404.

Zebra's v1 client implements the same payment-provider routes. Test it separately once the endpoint exists.

## Exposure that this repository cannot undo by itself

- **Git history:** both `.deb` files were committed in commits `c36521d`/`137b4bb`, so they remain reachable in this **public** repository's history. Removing them requires rewriting `main`'s history (force-push) and asking GitHub support to purge cached views. That is your decision.
- **Live site:** until a build from this change is published, the live site still serves both files over http at `/repo/debs/`.
- **Source and CI:** HCG-Nexus and HCG-Terminal are public repositories, and their source and CI artifacts are visible. Anyone can build these packages from source, so a paid or sign-in requirement only protects the official binaries and their updates.
- **Old CI artifact:** artifact `built-hcg-terminal-unleashed-0.1.0-1-rootless` of HCG-repo run 43673491962 (kept until about 29 Nov 2026) holds the Terminal `.deb`. Delete it under **Actions → that run → Artifacts**, or let it expire.

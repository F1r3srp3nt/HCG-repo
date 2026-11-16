# Sileo stability report (16 Nov 2026)

**Report.** Sileo worked again after tweaks were removed. It stopped working after HCG Repo was added.

This is treated as a lead to test, not a confirmed cause. The question here: can anything HCG Repo
serves break Sileo?

## What Sileo does with a source

This is from Sileo's source, commit `c9f7015`.

- **Refresh** (`RepoManager`):
  - `Release`: needs `Architectures` that include the device's, and `Components`.
  - `Packages.zst`, `.xz`, `.lzma`, `.bz2`, `.gz`, then plain. The first 2xx response wins, and its hash must match `Release`.
  - `Release.gpg`: a missing file is accepted unless an earlier one was cached.
  - The `Packages` file and `Release` are then written into APT's lists.
- **Sources list:**
  - `CydiaIcon@<scale>x.png`, then lower scales, then `CydiaIcon.png`. This happens once per launch.
  - The source page reads `sileo-featured.json`. A wrong shape means "no banner", not a crash.
- **`payment_endpoint`** (`PaymentManager`): Sileo reads the body *whatever the HTTP status*. It
  trims it and accepts it only if it parses as an https URL.
- **Decompression** (`decompression.c`): an empty but valid `.xz`, `.gz` or `.bz2` stream decodes to
  an empty file. Sileo's `Packages` parser returns no packages for an empty file.

## What the live source serves

[Check transport run 43884850030](https://github.com/F1r3srp3nt/HCG-repo/actions/runs/43884850030)
made 19 requests to `https://repo.hakmicapitalgroup.com/` with Sileo's User-Agent shape. The raw
responses are in that run's artifact **sileo-evidence**, kept for 30 days.

| URL (under `/repo/`) | Status | Content-Type | Redirects | Note |
|---|---|---|---|---|
| `Release` | 200 | application/octet-stream | 0 | Origin/Label `HCG Repo`; architectures `iphoneos-arm64 iphoneos-arm`; component `main` |
| `Packages.zst`, `Packages.lzma` | 404 | text/html | 0 | Not listed in `Release`; Sileo moves to the next format |
| `Packages.xz` / `.bz2` / `.gz` / `Packages` | 200 | x-xz / x-bzip2 / gzip / octet-stream | 0 | 32 / 14 / 20 / 0 bytes. Every hash matches `Release`, and each decodes to the empty index |
| `Release.gpg`, `InRelease` | 404 | text/html | 0 | The repository is unsigned. Sileo accepts that |
| `CydiaIcon.png`, `@2x`, `@3x` | 200 | image/png | 0 | 60, 120 and 180 px squares, identical to `branding/` |
| `sileo-featured.json` | 200 | application/json | 0 | A valid `FeaturedBannersView`; the banner JPEG returns 200 |
| `payment_endpoint` | 404 | text/html | 0 | The 404 page is not a URL, so Sileo registers no provider. That is correct while protected delivery is off |

There are no redirects, redirect loops, http downgrades, HTML answers to APT requests, or wrong
content types. Every file is byte-identical to a fresh local build. `Release` differs only in its
`Date`.

## Conclusion

- **On the repository side, no defect was found.** Every response is well-formed by Sileo's own code
  paths. The empty index (0 packages, by design while Nexus and Terminal Unleashed are protected) is
  handled without error. Nothing was changed to "fix" Sileo, because no confirmed issue exists.
- **What changed: checks only.** Repeated detection is now automatic, and evidence is kept.
  - `bin/hcgrepo verify` follows Sileo's semantics, including `payment_endpoint` and redirect
    handling.
  - `--evidence <dir>` saves every response.
  - **Check transport** runs it against production and uploads the evidence.
- **To go further, a crash log is needed.** It's in Settings → Privacy & Security → Analytics &
  Improvements → Analytics Data: entries starting with `Sileo`, from the time it stopped working.
  Also needed: the Sileo version, the iOS version, the jailbreak, and what "stopped working" means:
  - a crash at launch;
  - a crash or hang during refresh;
  - an error message, and its text.

## Least-disruptive checks on the phone

None of these requires removing tweaks.
1. Force-quit Sileo and open it again. Pull to refresh **Sources**. Note any error per source.
2. If Sileo crashes at launch, collect the crash log above before changing anything else.
3. If Sileo opens but refresh fails, open `https://repo.hakmicapitalgroup.com/repo/Release` in Safari.
   It should show plain text starting `Origin: HCG Repo`.
4. Only if the crash log points at HCG Repo's data: remove the HCG Repo source and confirm Sileo is
   stable again. Then add it back and see whether the crash returns. This is the direct test of the
   lead.

**Not claimed:** that Sileo works, recovers or installs anything on a device. No device evidence
exists for that.

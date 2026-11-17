# Sileo crash investigation

**Status: not reproduced, no confirmed defect, nothing claimed fixed.**

**What was reported:**
- Sileo worked again after tweaks were removed.
- Adding HCG Repo seems to make it fail again.
- Zebra uses the same source without crashing.

This is a suspected association, not a root cause. Zebra accepting a response does not show that
Sileo will.

Server-side validation (this document) and device reproduction (the diagnostic ladder below) are kept
separate. The crash is not considered fixed until the operation that failed on the device succeeds.

## 1. What is still needed from the device

1. The **Sileo version**: Settings tab → bottom of the page, or Sileo's About screen.
2. The **iOS version**, the **jailbreak** and its version, and rootless or rootful.
3. **The crash report.**
   - Where: Settings → Privacy & Security → Analytics & Improvements → Analytics Data.
   - Which: the newest entries named `Sileo-…` or `Sileo…ips` from the time of the failure.
   - How: open one, tap Share, and send the text. It contains the device model and iOS version, not personal data, but check it before sending.
4. **The stage** at which it fails:
   - (a) right after adding the source, during refresh;
   - (b) when opening the HCG Repo source page;
   - (c) when opening a package;
   - (d) when opening account or payment screens: Settings → Payment Providers, or a "Sign in" button;
   - (e) at every launch, before you can tap anything.

   Also say whether it is a crash (the app closes), a hang, or an error message, and give the
   message text.

## 2. Server-side validation of the deployed source

Checked against the deployed responses, not repository files: the **Check transport** evidence
artifact `sileo-evidence` from run 43884850030, taken with Sileo's User-Agent shape. Each row was then
read against **Sileo 2.5.1** (tag `2.5.1`, commit `c9f7015`, the latest release).

| Stage | What Sileo fetches | Deployed response | Sileo 2.5.1 code path, and why it does not fail |
|---|---|---|---|
| Refresh | `Release` | 200, `application/octet-stream`, 1,827 bytes, ASCII, LF, ends with a newline | `ControlFileParserFast.c`: the `MD5Sum:`/`SHA1:`/`SHA256:`/`SHA512:` headers have empty values and indented continuation lines, which is the multi-line form it supports. `Architectures` contains `iphoneos-arm64` and `iphoneos-arm`. `Components: main` is present (required) |
| Refresh | `Packages.zst`, `.lzma` | 404 (HTML 404 page) | `fetch(withExtensionsUntilSuccess:)` moves to the next extension on a failed status |
| Refresh | `Packages.xz` | 200, 32 bytes. A valid xz stream (CRC64) that decodes to 0 bytes | `decompressXz` (liblzma, concatenated mode) returns `STREAM_END`. The hash matches `Release`'s SHA256 and SHA512 |
| Refresh | `Packages.gz` / `.bz2` / `Packages` | 200, 20 / 14 / 0 bytes | Only used if `.xz` fails. All decode to 0 bytes and all hashes match |
| Refresh | (empty index) | 0 stanzas | `PackageListManager.readPackages`: no newline in empty data returns `[:]`. The empty file is then written to `/var/jb/var/lib/apt/lists` |
| Refresh | `Release.gpg` | 404 | Accepted unless a previously cached `Release.gpg` exists |
| Sources list | `CydiaIcon@3x.png` (then `@2x`, then 1x) | 200, `image/png`, 180×180, 8-bit RGB, valid CRCs, ICC v4.3 sRGB-class profile (456 bytes), no trailing bytes | Fetched once per launch with `Data(contentsOf:)` and decoded with `UIImage(data:)`. A bad image would give the generic icon, not a crash |
| Open source | `sileo-featured.json` | 200, `application/json`, 286 bytes | `FeaturedBannersView`: `itemSize` "{330, 110}" (non-zero), `itemCornerRadius` 10, one banner with string `url` and `title`; optional keys are cast with `as?` |
| Open source | banner JPEG | 200, `image/jpeg`, 78,725 bytes, progressive, 1200×400, 3 components | Loaded by `EvanderNetworking.image` and scaled down to the item size |
| Open source | (category list, 0 packages) | — | `CategoryViewController`: rows = buffer + 0 categories; no index into an empty array |
| Open package | depictions | none deployed | 0 packages, so no depiction can be opened from this source today |
| Payment / account | `payment_endpoint` | 404, `text/html`, 2,360-byte page | `PaymentManager.getPaymentProvider` ignores the status and reads the body. The trimmed HTML is not an https URL, so there is no provider. That is correct while protected delivery is off |

There are no redirects, redirect loops, http downgrades or `text/html` APT answers on any of these. Every
file is byte-identical to a fresh build; `Release` differs only in its `Date`.

**Traits of HCG Repo that are uncommon:**
- An **empty** package index.
- An **unsigned** `Release`.
- A **featured banner** on a source with no packages.

Each is handled by the code above. They are still the first things the ladder below isolates.

## 3. Isolated diagnostic sources

`bin/hcgrepo build --diagnostics` writes seven separate flat sources under `/diag/`. Each adds **one**
component to a minimal source, using the same build functions as production.
- **Never in production by default.** They exist only on the site when the Publish workflow is run
  with **diagnostics** ticked, which defaults to off.
- **Production is unchanged.** `/repo/` (metadata, package IDs, branding) is identical with or without
  them.
- **No package files.** No `.deb` is ever under `/diag/`.
- **Not discoverable.** `diag/index.html` carries `noindex` and nothing links to it.

Each variant has its own name (`HCG Repo Diagnostic N - …`), except the control.

| Variant | Source URL | Adds to the minimal source | Stage it can reach |
|---|---|---|---|
| d1-minimal | `https://repo.hakmicapitalgroup.com/diag/d1-minimal/` | Nothing: `Release` and an empty index (plain, `.xz`, `.gz`, `.bz2`) | Refresh |
| d2-icon | `…/diag/d2-icon/` | The source icon (3 sizes) | Sources list |
| d3-featured | `…/diag/d3-featured/` | `sileo-featured.json` and the HR banner | Opening the source |
| d4-package | `…/diag/d4-package/` | One real package entry: HCG Nexus 0.2.0 control data, `Tag: cydia::commercial`, the file never served. No depiction | Non-empty index, package list |
| d5-depiction | `…/diag/d5-depiction/` | d4 plus HCG Nexus's Sileo and web depiction, icon and header | Opening a package |
| d6-payment | `…/diag/d6-payment/` | d4 plus `payment_endpoint`, and a static provider that answers only `info`. Every other provider request fails | Account and payment |
| d7-production-copy | `…/diag/d7-production-copy/` | Production's `/repo/` files, byte for byte, at another URL (name "HCG Repo") | The control |

**Notes:**
- Production `/repo/` serves exactly d1 + d2 + d3 today.
- d4–d6 are the only way to exercise packages, depictions and a provider, because production lists none yet.

**Device protocol.** No tweak removal, jailbreak reset or package-database deletion is needed.
1. **Start from a working state.** If HCG Repo is added and Sileo can still open, remove the HCG Repo
   source in Sileo first and confirm Sileo is stable over two launches.
2. **Add d1 alone.** Use **Add to Sileo** on `https://repo.hakmicapitalgroup.com/diag/`, or Sources → +
   with the URL. Refresh, open the source, then relaunch Sileo. Record the result and the stage (a)–(e).
3. **Remove that source, then do the same for d2, d3, d4, d5 and d6, one at a time.** In d5, open the
   package's page. In d6, open the package and Settings → Payment Providers. Don't tap **Sign in** or
   **Purchase**; the static provider cannot complete them.
4. **Finally add d7** (the control), then production `https://repo.hakmicapitalgroup.com/repo/`.
5. **Recovery:** if a variant makes Sileo crash at every launch, remove only that source's entry.
   - Use Filza: in `/var/jb/etc/apt/sources.list.d/sileo.sources`, delete the block (`Types:` …
     `Components:` and the blank line after it) whose `URIs:` line is that variant's URL.
   - Then reopen Sileo.
   - Nothing else needs to change.
6. **Save** a crash report for every failing step.

**Reading the result:**

| First variant that fails | Points to | Next step |
|---|---|---|
| d1 | `Release` or an empty index, or Sileo's handling of a new unsigned source | Compare the d1 crash report frames (`RepoManager`, `ControlFileParser`, `XZ`) |
| d2 only | Icon decoding | Re-encode the icons, for example without an ICC profile, as a new variant |
| d3 only | Featured banner / `FeaturedBannersView` | Try a variant without `hideShadow`/`displayText`, or with a baseline JPEG |
| d4 or d5 | Package entry or depiction | Not in production today; fix before any package is listed |
| d6 | Payment provider discovery | Not in production today; fix in the Store's provider before enabling it |
| only d7 or production | Something tied to the production URL or state cached for it | Compare with d7: same bytes, different URL |
| none | Not HCG Repo's data | Use the crash report to find the actual frame |

## 4. Regression checks already in place

- **`bin/hcgrepo verify`.** Every build and every **Check transport** run walks the source the way
  Sileo does:
  - redirect loops and http downgrades;
  - `text/html` answers;
  - `payment_endpoint` read whatever the status;
  - icon sizes, squareness and byte identity;
  - featured shape and banner image;
  - index hashes and decompression.
- **Diagnostic sources.** `tests/test_diagnostics.py` checks that each variant adds exactly one
  component, `Release` stays ASCII, `/repo/` is unchanged, no `.deb` appears, and every variant
  verifies.

When the crash report or ladder confirms a defect, a regression check for that exact input is added
next to the fix. Nothing is changed before then.

**Testing channel.** The former d8 candidate source is replaced by the permanent testing channel at
`https://repo.hakmicapitalgroup.com/repo/candidate/`, which is built by every Publish (`docs/CANDIDATE.md`).

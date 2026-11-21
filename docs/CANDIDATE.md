# HCG Repo Candidate (testing channel)

| | Stable | Candidate |
|---|---|---|
| Source URL | `https://repo.hakmicapitalgroup.com/repo/` | `https://repo.hakmicapitalgroup.com/repo/candidate/` |
| Name in Sileo and Zebra | HCG Repo | **HCG Repo Candidate** |
| Contents | Released builds | Betas of HCG tweaks, jailbreak apps and iOS command-line tools that have a real, installable `.deb` |
| Icon and banner | The official HCG Repo icon and HR banner | The same official files, served under `/repo/candidate/` |

**An independent source.** The candidate is a complete flat APT source with its own `Release`, `Packages` (plain,
`.xz`, `.gz`, `.bz2`), `debs/`, depictions, icons, `sileo-featured.json` and an `index.html` explaining the channel.
**It is built by every ordinary build and Publish**, so it never disappears when diagnostics are off.

## Isolation from the stable source

- **One channel per build.** Each lock entry has `channel = "candidate"`, or none (stable). The stable index and
  stable `debs/` are built only from stable entries, and the candidate only from candidate entries.
- **Enforced at build time.** The build stops if the same build would be in both channels, or if a candidate file
  appears in the stable `debs/`.
- **The stable source does not see the candidate.** Package managers read only the `Packages` index of the source
  URL. Nesting `/repo/candidate/` under `/repo/` exposes nothing: `/repo/Packages` never names a candidate file.
- **Free public demos.** Owner-approved candidate builds are free, with no sign-in and no payment
  ([ACCESS.md](ACCESS.md), free-demo policy). Free demos of paid or sign-in products are refused in stable.
- **Public packages only.** The candidate has no payment provider, so protected (`connect` or `paid`) packages can
  never be put in it: `channel` refuses, and `validate` reports `candidate-protected`.
  - Each free exception is scoped to that package's `exception_versions` (Vortex: `0.1.0~beta1`, `0.1.0~beta2`,
    `0.3.0~beta1`, `0.3.1~beta1`; Halo: `0.1.0~beta1`, `0.2.0~beta1`, `0.3.0~beta1`; Animation Center: `0.1.0~beta1`; Portal: `0.1.0~beta1`, `0.1.0~beta3` (staged, approval pending); Nexus: `0.2.1~beta1`, `0.2.2~beta1`, `0.3.0~beta1` (approved, never imported),
    `0.3.1~beta1`).
  - It cannot make any other package, or any other version, public.
- **Verified on every Publish.** Both sources are verified offline before deployment and online afterwards.
  **Check transport** walks both sources and downloads every candidate `.deb` to check its checksum.

## Versions: candidate and stable together

- **Package IDs are the same in both channels.** Never rename a package for testing.
- **Betas use Debian pre-release versions:** `0.1.0~beta1 < 0.1.0~beta2 < 0.1.0~rc1 < 0.1.0 < 0.1.0-1 < 0.1.1`.
- **Testers move to stable automatically.** A tester with both sources gets the highest version. When `0.1.0` is
  released to stable, testers are upgraded to it, because `~` sorts before the release.
- **A user with only the stable source never sees a candidate build.**
- **A failed beta is not replaced under the same version.** Publish a new pre-release, such as `~beta2`.
  Recorded versions are immutable: same version, same SHA-256.
- **Promotion.** Promoting a build means `bin/hcgrepo channel <pkg> <version> <arch> stable`. A beta version
  such as `0.1.0~beta1` should not be promoted. Release `0.1.0` as its own build instead.
- **No demotion.** A stable build cannot move back to candidate (`lock-history-channel`); withdraw it instead.

## Adding a candidate build

1. Write an import request, `imports/<name>.toml`, pinned to an immutable source: a commit, a run and artifact ID or
   a tag, and the SHA-256. Add `channel = "candidate"` under `[request]`.
2. Push it on a branch. **Prepare packages** fetches it, checks the source, the checksum and the package identity,
   runs the full review, and commits the `.deb`, provenance (with the exact source commit) and lock entry for review.
3. Merge, then run Publish.

Products with no installable build appear only in the website's **Pending builds** list, never in `Packages`.

## Inventory (18 Nov 2026)

Checked against each project's branches, handoffs and its own CI runs and artifacts (read through GitHub's API by a
temporary HCG-repo workflow, run 44225029311). Every build below is the exact CI artifact, fetched and checked by
**Prepare packages**. Device evidence: `docs/device-tests/2026-11-18-owner-device-evidence.md`.

| Product | Offered (highest) / also listed | Source of the offered build | Device testing | Candidate |
|---|---|---|---|---|
| HCG Vortex (Beta) | **0.3.1~beta1** / 0.3.0~beta1, 0.1.0~beta2, 0.1.0~beta1 | HCG-Jailbreak-Research `e3b6604`, run 44220895787, artifact 14389878167, SHA-256 `ff1d1b5f…f62f19e42`, 227,306 bytes (gzip) | 0.3.1~beta1: not yet installed. A recording of the previous build (version not confirmed) showed 3 of 6 effects with visual bugs F1–F3, fixed in 0.3.1. 0.1.0~beta1: install failed (iPhone 11 Pro Max, iOS 17.4). Performance and battery unmeasured | **Included** |
| HCG Halo (Beta) | **0.3.0~beta1** / 0.2.0~beta1, 0.1.0~beta1 | HCG-Jailbreak-Research `e22527d`, run 44246501375, artifact 14402121490, SHA-256 `bd8a9266…381defc5`, 431,720 bytes (gzip). Show Halo on + Display Zoom geometry; 0.2.0~beta1: `778c21a`, run 44216299888, SHA-256 `391a4421…85ae6e54` | 0.3.0~beta1: simulator-tested (446 runtime checks; Nexus panel preview 17/17; Settings preview 0 failures; Show Halo on surfaces 51/51); real-device testing pending. 0.1.0~beta1: partial runtime observation on iPhone 11 Pro Max | **Included**: install with Nexus 0.3.1~beta1 for the panel border |
| HCG Nexus (Beta) | **0.3.1~beta1** / 0.2.2~beta1 (superseded), 0.2.1~beta1 | HCG-Nexus `82412dd`, run 44225979728 (#24), artifact 14393735142, SHA-256 `b6750cf1…adaf6741c3`, 1,143,570 bytes (gzip). The redesign + Command Mode + `HCGNexusBorderInterface` and `HaloBorder.plist` (schema 1, interface 1, `nexusVersion` 0.3.1~beta1) | 0.3.1~beta1: simulator-tested (22 app XCTests, panel + Halo preview 17/17); real-device testing pending. Owner reports an earlier Nexus running on iPhone 13 Pro, iOS 16.5 (version not stated): not a recorded test | **Included**: the companion of Halo 0.2.0~beta1 |
| HCG Nexus 0.3.0~beta1 | — | HCG-Nexus `8aa0b04` | — | **Not published**: replaced by 0.3.1~beta1, which adds the HCG Halo border interface and Command Mode |
| HCG Animation Center (Beta) | **0.1.0~beta1** | HCG-Jailbreak-Research `2d642bd`, run 44242604467, artifact 14399579139, SHA-256 `27a161b3…873218697b49c995812f04e579f97429f60`, 360,342 bytes (gzip). Later builds reused this version with different bytes; only this one is imported | Simulator-tested (103 runtime checks); not yet device-tested. Lock Screen playback experimental and off by default; Live Photos unsupported; AI generation not connected | **Included** |
| HCG Portal (Beta) | **0.1.0~beta3** (staged; publication pending owner approval) / 0.1.0~beta1 | HCG-Jailbreak-Research `579213e`, run 44861951914, artifact 14690321654, SHA-256 `9af1575a…de945abe9a8dcb34`, 284,190 bytes (gzip). 0.1.0~beta1: `4bfed33`, run 44395826216, SHA-256 `9ccfeb9b…bb139220dae`. 0.1.0~beta2 (run 44663876626) was a test build and is not imported | 0.1.0~beta3: Simulator validated (67/67 runtime; preview 115/115 with lock / wake / menu / unlock cycles); physical iPhone verification pending. 0.1.0~beta1: SpringBoard Safe Mode reported during installation through Sileo (phone not confirmed; cause not confirmed; no crash report reviewed) | **Included** (beta1); beta3 staged |
| HCG Terminal Unleashed | — (0.1.0-1 recorded as protected) | HCG-Terminal CI uploads logs only, no `.deb` | — | **Blocked**: connect access with no free-demo approval, and no CI-built `.deb` |
| HCG Sentinel | — | HCG-Sentinel CI produces archives, simulator apps and screenshots | — | **Pending**: no jailbreak `.deb` exists; the privileged helper is a specification |

**Not eligible:**
- **IPAs:** HCG Terminal (standard edition, and the unsigned Unleashed IPA), the HCG Nexus standalone app, HCG News,
  HCG Mail.
- **macOS:** HCG ONE Unleashed, HCG RePrompter, HCG Clarity, Oslo Sprint, The Last Sprint.
- **Desktop tooling:** HCG Executable Maker; its `control` files are test fixtures.
- **Linux packaging:** only planned, in HCG Server and HCG Mail.

**Access limitations.** HCG-One, HCG-LifeOS and hcg-cardx could not be cloned: they are private to this session, not
missing. Run and artifact numbers for HCG-Nexus and HCG-Terminal come from those projects' files. This session
could not query their CI directly.

## Moving from the old diagnostic source

The diagnostic source `https://repo.hakmicapitalgroup.com/diag/d8-candidate/` ("HCG Repo Candidate") is retired.
It disappears with the next Publish.
1. In Sileo or Zebra, delete the source whose URL is `…/diag/d8-candidate/`, and any other `…/diag/…` source.
2. Add `https://repo.hakmicapitalgroup.com/repo/candidate/`, or tap **Add to Sileo** or **Add to Zebra** on
   https://repo.hakmicapitalgroup.com/repo/candidate/, and refresh.
3. Nothing installed needs to change: the package IDs and versions are the same.

The isolated crash-diagnosis sources d1–d7 (`docs/SILEO_CRASH.md`) still exist. They are published only with
Publish's **diagnostics** input.

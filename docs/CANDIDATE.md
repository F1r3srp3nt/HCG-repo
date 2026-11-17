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
  - Vortex's free exception is scoped to `exception_versions` (now `0.1.0~beta1` and `0.1.0~beta2`).
  - It cannot make any other package, or any other Vortex version, public.
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

## Inventory (17 Nov 2026)

The inventory searched every accessible HCG repository and all its branches. **Three** HCG projects produce an
iOS jailbreak `.deb`:

| Product | Package / version | Source of the build | Access | Candidate | Why |
|---|---|---|---|---|---|
| HCG Vortex (Beta) | `com.hakmicapitalgroup.vortex` **0.1.0~beta2** (offered) and 0.1.0~beta1, iphoneos-arm64 | beta2: HCG-Jailbreak-Research `b212cdb`, run 44012915117, artifact 14297382250, SHA-256 `a326759a…90179395` (gzip). beta1: `7d5e5b9`, run 43970942708, SHA-256 `bb0af727…07673eea` (legacy lzma) | public: free exception for beta1 and beta2 (owner, 16 and 17 Nov 2026) | **Included** | beta2 is a packaging compatibility candidate: installation and runtime are not yet device-verified. beta1 failed to unpack on iPhone 11 Pro Max, iOS 17.4 (dpkg-deb Bus error 10). Its record and provenance are kept, and it stays listed as an older version. Package managers offer beta2, the highest version |
| HCG Nexus (Beta) | `com.hakmicapitalgroup.hcgnexus` **0.2.1~beta1**, iphoneos-arm64 | HCG-Nexus `58e2dbd`, run 44019802883 (#13), artifact `hcg-nexus-rootless-deb`, SHA-256 `948465ea…8ac17af`: the 0.2.0 code of run #10 with gzip packaging | paid, with a free exception for 0.2.1~beta1 only (owner, 17 Nov 2026) | **Included** | Repackaged as a new version because 0.2.0 uses the same `data.tar.lzma` (64 MiB dictionary) as the failing Vortex build, and 0.2.0 is already recorded (run #9). Not yet device-tested. 0.2.0 stays paid, protected and out of both indexes |
| HCG Terminal Unleashed | `com.hakmicapitalgroup.hcgterminal.unleashed` 0.1.0-1, iphoneos-arm64 | Built from HCG-Terminal with the `hcg-terminal-unleashed` recipe. HCG-Terminal's own CI does not upload its `.deb` | connect | **Blocked** | Needs HCG Connect sign-in, and no sign-in delivery is live. Requires iOS 17 |
| HCG Sentinel | — | Not applicable | — | **Pending** | No jailbreak package exists. The app is an IPA, and the privileged helper is only a specification |

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

# HCG Vortex 0.1.0~beta1: test record

Package under test: `com.hakmicapitalgroup.vortex_0.1.0~beta1_iphoneos-arm64.deb`, 81,650 bytes,
SHA-256 `bb0af72745a36158d4022c1b97ebc9800847cf4b1fb42ca14d05b9fc07673eea`. It is built from
HCG-Jailbreak-Research `7d5e5b9` (run 43970942708, artifact 14278312698).

Write down only what was observed. A step that was not done stays **not done**.

## 1. Sileo source and depiction check (no installation)

Candidate source: `https://repo.hakmicapitalgroup.com/diag/d8-candidate/`. It was deployed by
**Publish candidate** run 44002870177 from commit `371cd1c`; production stayed on `3a8fe2e`.

| Field | Value |
|---|---|
| Date | not done |
| Device, iOS, jailbreak | not done |
| Sileo version | not done |

| Step | Result (worked / failed / crashed / hang + message) |
|---|---|
| Add the source | not done |
| Refresh | not done |
| Source shows "HCG Repo Candidate" with the HCG icon | not done |
| Open the source (banner, 1 package) | not done |
| Open HCG Vortex (Beta): spiral icon, Beta, Experimental, Not yet device-tested, Known issues | not done |
| Relaunch Sileo; it still opens | not done |
| Crash report, if any (Analytics Data `Sileo-…`) | not done |

## 2. Vortex runtime smoke test (after installation, separate)

Procedure: HCG-Jailbreak-Research `HCGVortex/docs/DEVICE_TEST_HANDOFF.md`, steps 0 and A–G, with recovery in §7.
The package is installed with `dpkg` over SSH, not through Sileo.

| Field | Value |
|---|---|
| Device report (`device-info.sh`) | not done |
| SHA-256 checked on the phone | not done |

| Step | Expected | Observed |
|---|---|---|
| 0 Device confirmed | arm64e, iOS ≥ 15, rootless, ElleKit, PreferenceLoader | not done |
| A Install with kill switch | Engine Status *Kill switch on*, Home Screen stock | not done |
| B Disabled baseline | *Loaded, disabled* (or *Incompatible — stock*: stop, record as fail) | not done |
| C Reverse Slide / Vertical Rise / Vertical Fall | effect in both directions, correct page dots, correct icon taps, Today View/App Library/folders stock | not done |
| D Preferences persist after respring | same values | not done |
| E Enable / disable / restore | immediate | not done |
| F Three resprings, 30 s apart | *Active* each time, no SpringBoard crash logs naming HCGVortex | not done |
| G Uninstall | not installed, dylib gone, Home Screen stock | not done |
| Stop condition hit, and the recovery used | — | not done |

Result for `package.toml`: not decided until the steps above are done. The handoff's pass criteria are:
*Engine Status: Active*, one effect working in both directions, correct taps, stock behaviour after disabling and
after a respring, and a clean uninstall. *Incompatible*, *Fault*, *Auto-disabled* or a crash is a `fail`, or a
`partial` if effects otherwise worked. Any of these must be described in `known_issues`, naming the device.

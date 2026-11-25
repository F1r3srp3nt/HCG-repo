# HCG Terminal Unleashed 0.2.0~rc1: test record

**Package under test:** `com.hakmicapitalgroup.hcgterminal.unleashed_0.2.0~rc1_iphoneos-arm64.deb`, 7,020,986 bytes,
SHA-256 `02626804763200f9e29d651a213bdb92330351d3052073e6a52cb86d62fc5a28`.
- Built from HCG-Terminal commit `1cc76a7` by CI run 45051075163.
- The build's provenance stays in `pool/iphoneos-arm64/com.hakmicapitalgroup.hcgterminal.unleashed_0.2.0~rc1_iphoneos-arm64.deb.provenance.json`.

This record holds only what was reported. A step that was not done stays **not done**.

## Device report, 24 Nov 2026: installed, closed at launch

| Field | Value |
|---|---|
| Source of the report | The owner, 24 Nov 2026. HCG Repo recorded it in that day's rc2 publication: [CHANGELOG](../../packages/com.hakmicapitalgroup.hcgterminal.unleashed/CHANGELOG.md), [CANDIDATE.md](../CANDIDATE.md), and the rc1 version label in `package.toml` |
| Device | iPhone 11 Pro Max |
| iOS | 17.4 (build not recorded) |
| Jailbreak | Dopamine, rootless (version not recorded) |
| Package manager | not recorded |
| Crash report | **not available**: the cause of the exit is unknown |

| Step | Reported |
|---|---|
| Installation | **Passed**: the package installed and the app was registered (its icon appeared) |
| Launch | **Failed**: the app closed about a second after launch |
| Runtime (shell, diagnostics) | **Not tested**: the app did not stay open |

## Consequence

0.2.0~rc1 is superseded by 0.2.0~rc2, which adds a startup journal, Startup Recovery and Safe Mode to diagnose this
exit. rc1 stays installable by exact version and is never rebuilt. Its bytes and checksum are unchanged.

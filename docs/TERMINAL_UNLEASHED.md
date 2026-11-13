# HCG Terminal Unleashed: packaging review and what is needed to publish it

**Status: not published.** No built `.deb` is available:

- HCG-Terminal's CI builds a rootless and a rootful `.deb` in `.github/workflows/apple-build.yml` (step "Jailbreak package"). It uploads only `logs/`, so the packages are discarded when the job ends.
- The repository has no GitHub releases.
- The app needs Xcode on macOS, so it cannot be built in this repository's Linux CI.

This review is based on the source at `F1r3srp3nt/HCG-Terminal@100a8d08f5495a42061ac471bab10da7105fa760`. That source was read; it was not modified.

## The problem it solves

The sandboxed install (Xcode, TestFlight, an IPA) finds installed shells, but iOS refuses `posix_spawn` with `EPERM`. That is the sandbox, and HCG Terminal already reports it accurately. The jailbreak edition is a **separately packaged copy of the app**:

- It is installed by the package manager into `/var/jb/Applications` (rootless) or `/Applications` (rootful).
- It is signed with `ldid` and entitlements that let it run outside the app sandbox, *where the jailbreak honours them*.

Installing a `.deb` does not remove the sandbox for anything else. Nothing is patched globally.

## What `Scripts/package-ios.sh --deb rootless` produces

| Item | Value | Review |
|---|---|---|
| Package | `com.hakmicapitalgroup.hcgterminal.unleashed` | OK. In this repo's namespace; the same as the Unleashed app's bundle id |
| Version | `<MARKETING_VERSION>-<CURRENT_PROJECT_VERSION>`, currently `0.1.0-1` | ⚠ `CURRENT_PROJECT_VERSION` is fixed at `1`. Every rebuild produces the same version, and this repository refuses different bytes under an already-published version. **Bump the build number for each published build.** |
| Architecture | `iphoneos-arm64` (rootless), `iphoneos-arm` (rootful) | OK |
| Install path | `/var/jb/Applications/HCGTerminalUnleashed.app` (rootless), `/Applications/…` (rootful) | OK. Matches the layout rules |
| Depends | `firmware (>= 17.0)` | OK for the iOS 17.0 deployment target. ⚠ `postinst` calls `uicache`, which `uikittools` provides, but `uikittools` is not declared. Add it: `Depends: firmware (>= 17.0), uikittools` |
| Executable | arm64, mode from the build (755) | OK. The validator checks the mode, Mach-O type, architecture and signature |
| Libraries | SwiftPM products are static, so there is no embedded framework and no app extension | OK. Only the main executable needs signing, which is what the script does |
| Signing | `ldid -S<entitlements>` on the packaged copy only | OK. The CI uses `Unleashed-optional-entitlements.example.plist`: `com.apple.private.security.no-sandbox = true` |
| Helper / privileges | No helper, no setuid, no LaunchDaemon. `sudo`/`su` only when the user types them | OK. Sessions run as the app's user (normally `mobile`), never automatically as root |
| postinst | `uicache -p /var/jb/Applications/HCGTerminalUnleashed.app` | OK (see the uikittools note above) |
| postrm | `uicache -u …` on `remove`/`purge` | OK. Removes only the registration. App data in the app's data container is not deleted |
| Ownership | `dpkg-deb --root-owner-group` | OK. The `ar` fallback path in the script also uses root ownership |
| Separation from the IPA | Bundle id `…hcgterminal.unleashed` ≠ standard `…hcgterminal` | OK for the standard edition. This repository refuses any `.deb` carrying `com.hakmicapitalgroup.hcgterminal`. ⚠ The *sideloaded Unleashed IPA* uses the **same** bundle id as the `.deb`. Delete it before installing the `.deb`, or give the jailbreak edition its own bundle id. |

**Simulated check.** A `.deb` was built in this session with exactly the script's control file, scripts and layout, and the CI's entitlements. A stand-in arm64 binary replaced the real app. `bin/hcgrepo inspect` on it gives:

```
ERROR [entitlements-unreviewed] …/HCGTerminalUnleashed carries entitlements ['com.apple.private.security.no-sandbox'] that are not recorded in packages/…/package.toml
warn  [app-uicache-undeclared] postinst uses uicache but Depends does not include uikittools
```

The import succeeded once the entitlements were recorded, and the build plus HTTP verification under `/HCG-repo/` passed. That run was a rehearsal in a scratch copy, so nothing was committed to `pool/`.

### Entitlements: decision needed

`com.apple.private.security.no-sandbox` alone may not be enough for every jailbreak. Some terminal apps also use `platform-application` or `com.apple.private.security.container-required = false`. **Which set lets the app spawn a PTY shell on your jailbreak has not been verified on a device.** Decide after a device test, record the exact set in `packages/com.hakmicapitalgroup.hcgterminal.unleashed/package.toml`, and keep it minimal. The validator then refuses any build whose binary carries a different set.

## Exact steps to publish it

1. In **HCG-Terminal**:
   - Add `uikittools` to `Depends` in `Scripts/package-ios.sh`.
   - Bump `CURRENT_PROJECT_VERSION` (e.g. to `2`).
   - Decide the entitlements file.
   - Make the CI keep the `.deb`: add an `actions/upload-artifact` step for `$RUNNER_TEMP/dist-rootless/unleashed/*.deb` (and rootful if wanted), or attach the files to a GitHub release.
2. Note the run id (or release tag) and the full commit SHA it built.
3. In **this repository**:
   ```bash
   GITHUB_TOKEN=<token with actions:read on HCG-Terminal> \
   bin/hcgrepo import-github-artifact --repo F1r3srp3nt/HCG-Terminal --run-id <id> \
     --commit <sha> --artifact <artifact-name>
   # or: bin/hcgrepo import-github-release --repo F1r3srp3nt/HCG-Terminal --tag <tag> --commit <sha> --asset <file>.deb
   # or, with a local file: bin/hcgrepo import-deb <file>.deb --built-from F1r3srp3nt/HCG-Terminal@<sha>
   ```
   Run `bin/hcgrepo inspect` first, then fill in `[review.entitlements]` in the package's `package.toml` from its output.
4. Add a `## <version>` entry to `packages/com.hakmicapitalgroup.hcgterminal.unleashed/CHANGELOG.md`. Commit `pool/…deb`, `…provenance.json`, `repo-lock.json` and the metadata in one pull request. Merge it once **Validate repository** passes.
5. Run **Publish repository**.
6. Run [DEVICE_CHECKLIST.md](DEVICE_CHECKLIST.md) and record the evidence. Only then set `status = "device-tested"` for that version.

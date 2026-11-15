# HCG Terminal Unleashed: packaging review and what is needed to publish it

**Status: imported for review, not published.** `0.1.0-1` (rootless, `iphoneos-arm64`) is in `pool/` on the
review branch. Nothing has been installed on a device.

- **Where it came from:** HCG-Terminal's CI builds the `.deb` but does not keep it. **Prepare packages** therefore built it on macOS from the pinned commit `ddc51977c95f499a0568bee02aa1f0ba6b87ded7`, using the project's documented command (`imports/hcg-terminal-unleashed-0.1.0-1-rootless.toml`; [run 43673491962](https://github.com/F1r3srp3nt/HCG-repo/actions/runs/43673491962)).
- **SHA-256:** `7fdb735e74e551fa05a23a81714ff537f97556a67d600f49f090b543ef1d42bf`.
- **Publication plan:** see `docs/PUBLICATION_PLAN.md`.

**Read first:** the package requires **iOS 17.0 or later** (`Depends: firmware (>= 17.0)`, matching the app's deployment target).
- Sileo hides it on iOS 16.x, including the iPhone 13 Pro on iOS 16.5 that HCG Nexus targets.
- To support that device, HCG-Terminal would have to lower its deployment target and pass its own tests, and a new version would then be imported here.

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
| Libraries | SwiftPM products are static. The Xcode used for the build (26.x) embeds Apple's back-deployment library `Frameworks/libswiftCompatibilitySpan.dylib` | ⚠ The script ldid-signs only the main executable, so that dylib keeps **Apple's own signature** (CMS, team `59GAB85EFG`). It is a valid Apple signature, but whether the jailbreak loads it next to an ldid-signed executable has to be shown on a device. The validator reports this as `app-mixed-signatures`. *(Correction: an earlier review, at a commit built with an older Xcode, said there was no embedded library.)* |
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

### Entitlements: recorded, unverified on a device

`packages/com.hakmicapitalgroup.hcgterminal.unleashed/package.toml` records exactly the set the project's own documented build uses: `com.apple.private.security.no-sandbox` only. The import verified that the built binary carries that set and nothing more. `com.apple.private.security.no-sandbox` alone may not be enough for every jailbreak. Some terminal apps also use `platform-application` or `com.apple.private.security.container-required = false`. **Which set lets the app spawn a PTY shell on your jailbreak has not been verified on a device.** Decide after a device test, record the exact set in `packages/com.hakmicapitalgroup.hcgterminal.unleashed/package.toml`, and keep it minimal. The validator then refuses any build whose binary carries a different set.

## Publishing this version and the next

**0.1.0-1:** follow `docs/PUBLICATION_PLAN.md`. Merge the branch, fix HTTPS, run **Publish repository**, then test on an iOS 17+ rootless device.

**Next version.** Recommended changes in HCG-Terminal:
1. Add `uikittools` to `Depends` in `Scripts/package-ios.sh`.
2. Bump `CURRENT_PROJECT_VERSION`. `0.1.0-1` is locked here and cannot be rebuilt with other bytes.
3. Optionally re-sign embedded libraries with `ldid` as well, and keep the `.deb` as a CI artifact.
4. Consider whether the deployment target can be lowered below iOS 17 for current Dopamine devices.

Then add a request in this repository:
1. Copy `imports/hcg-terminal-unleashed-0.1.0-1-rootless.toml` and change `version` and `commit`. If HCG-Terminal now uploads the `.deb`, use `kind = "github-artifact"` with the run ID and SHA-256 instead of `kind = "build"`.
2. Add a `## <version>` changelog entry.
3. Push to a review branch. **Prepare packages** imports and validates it, and commits the result for review.

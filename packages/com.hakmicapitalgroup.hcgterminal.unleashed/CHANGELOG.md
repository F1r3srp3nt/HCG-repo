# HCG Terminal Unleashed changelog

Entries are keyed by the exact Debian version in the package's control file (`## <version>`).

## 0.2.0~rc2

Frozen release candidate (SHA-256 `597eb8f11519c9a91a0daa8c72eb9f70f5e38c4fc2e1ffaba469d8420606ff84`, 7,053,756 bytes), built once by HCG-Terminal CI
(main @ `22912b4`, run 45365201577) with `--deb-version 0.2.0~rc2`. App 0.2.0 (2). Same entitlements as rc1.

- 0.2.0~rc1 installed and registered on an iPhone 11 Pro Max (iOS 17.4, Dopamine rootless) but closed about a second after launch. The cause needs the device's crash report.
- Startup journal: each startup step, crash signals, iOS and app version and injected tweak libraries are written to `/var/mobile/Library/Application Support/HCGTerminal/Startup/`. No passwords, keys, tokens, host names or commands.
- Startup Recovery: after a launch that did not finish, the app opens on a recovery screen first: share the startup report, Start in Safe Mode (no environment detection or background jobs), or Start Normally.
- Jailbreak Diagnostics no longer prints "at ." for an empty prefix; the HCG Ecosystem screen gives Agent Runtime's real status.
- Not yet verified on a physical device.

## 0.2.0~rc1

Frozen release candidate (SHA-256 `02626804763200f9e29d651a213bdb92330351d3052073e6a52cb86d62fc5a28`, 7,020,986 bytes),
built by HCG-Terminal CI (commit `1cc76a7`, run 45051075163) with
`Scripts/package-ios.sh unleashed … --deb rootless --ldid-entitlements HCGTerminal-Support/Unleashed-jailbreak.entitlements --deb-version 0.2.0~rc1`.

- Job control in local shells: `hcg-pty-helper` makes the terminal the shell's controlling terminal (Ctrl-C/Z, window size, `/dev/tty`) and sets the working directory.
- Every Mach-O is signed with `ldid` (the app with the reviewed no-sandbox entitlement only; the helper and the embedded Swift library with none). No Apple signature is mixed in any more.
- Declares `uikittools` (its `postinst` runs `uicache`); maintainer scripts run only on configure / remove / purge.
- Jailbreak Diagnostics (read-only): install location and signature, rootless bootstrap, dpkg health, repositories, tweak injection and Safe Mode clues, storage.
- Navigation-only URL routes for HCG Nexus (`hcgterminal-unleashed://open/<screen>`).
- Not yet verified on a physical device.

## 0.1.0-1

First jailbreak package, built from HCG-Terminal commit `ddc5197` with the project's documented
`Scripts/package-ios.sh unleashed … --deb rootless` route.

- Local shell in a pseudo-terminal (input/output, resize, Ctrl-C, exit status), where the device allows it.
- Read-only environment scan, program list, file browser, process list and package list.
- Remote SSH development, unchanged from the standard edition.
- Not yet verified on a physical device.

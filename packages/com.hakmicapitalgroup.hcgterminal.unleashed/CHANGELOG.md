# HCG Terminal Unleashed changelog

Entries are keyed by the exact Debian version in the package's control file (`## <version>`).

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

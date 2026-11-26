# HCG Nexus changelog

## 0.6.0~beta1

Free in HCG Repo Candidate only. Simulator-tested; real-device testing is pending. Everything in 0.5.0~beta1 (which
includes 0.4.0~beta1), plus:

- **Command Center** (HCG Ecosystem › Command Center): every HCG product in 13 categories with search, favorites, expandable categories, a connection dashboard and notices.
  - Each product has a profile: install status and version, connection status and last successful communication, capabilities, how it connects, permissions, troubleshooting, development requirements and roadmap.
  - Actions appear only when they really work. Links and endpoints are only official HCG addresses.
- **Connected only with evidence:** a real two-way exchange (for example *Check now* reading HCG News), HCG Halo answering the border switch, or HCG Server through Device Link. Installed apps show *Installed*.
- **Roadmap:** 14 development states with the evidence for each and a history; development gaps can be exported as an HCG LifeOS import file (no XP is awarded).
- **Connected devices:** import HCG System Tester's device inventory; ten device classes with adapters, missing capabilities and the safety rule (never vehicle, robot or machinery control, never firmware changes).
- **HCG Ecosystem dashboard** (from 0.4.0): installed HCG products and versions, HCG Terminal screens, tweak status, opening Nexus from other HCG apps.
- **Package Trust Center** (from 0.5.0): repository signatures, checksums, Release Passports and entitlements, read-only. Its cryptography now uses Apple's CommonCrypto and Security framework, and an independent review's findings are fixed (revocations, oversized keys, replayed indexes).
- Declares iOS 15.0 or later. Works offline; *Check now* is the only network read and happens only when you tap it.

## 0.5.0~beta1

Free in HCG Repo Candidate only (superseded by 0.6.0~beta1). Simulator-tested; real-device testing is pending. Everything in 0.4.0~beta1, plus:

- **Package Trust Center** (HCG Ecosystem › Security), read-only: HCG Repo signature checks against a key you pin (RSA, SHA-256/512; SHA-1 refused), package index and download checksums, Release Passports and compatibility, declared and installed-app entitlements, installed packages and security warnings.
- It never calls a package safe because its checksum matches, and it installs nothing. HCG Repo is unsigned today, so it shows *Unsigned*.
- Uses the portable verifier that 0.6.0~beta1 later hardened (independent review fixes) and moved to Apple's cryptography; prefer 0.6.0~beta1.

## 0.4.0~beta1

Free in HCG Repo Candidate only (superseded by 0.6.0~beta1). Simulator-tested; real-device testing is pending. Everything in 0.3.3~beta1, plus:

- **HCG Ecosystem dashboard** (was HCG Products): installed HCG products with versions, compatibility and health, read-only settings of HCG Halo, Vortex, Portal and Animation Center, Candidate updates with Zebra/Sileo links (never installs) and an activity history.
- **HCG Terminal** screens through Terminal's own links (version and build, iOS 17 compatibility); diagnostics, packages and processes through Unleashed Tools.
- **Opening Nexus from HCG apps:** HCG Portal's interface and Darwin requests from HCG apps (unlocked only, rate-limited, can be turned off).

## 0.3.3~beta1

Free in HCG Repo Candidate only. Simulator-tested; real-device testing is pending. Everything in 0.3.2~beta1, plus:

- **Command Mode keeps your panel glass.** Its card now uses the blur and opacity set in Settings › HCG Nexus › Panel glass, like the panel itself (before, it always used a thin blur and a fixed tint).
  - With blur off, the card keeps the panel's minimum opacity so text stays readable.
  - Increase Contrast makes the card at least 86% opaque.
  - Reduce Transparency still makes it solid, with no blur, whatever the setting.
  - Changing the setting updates the card straight away.
- Works with HCG Halo 0.3.1~beta1 exactly as 0.3.2~beta1 does. Works offline; no sign-in needed.

## 0.3.2~beta1

Free in HCG Repo Candidate only. Simulator-tested; real-device testing is pending. Everything in 0.3.1~beta1, plus:

- **Use HCG Halo border** in Settings › HCG Nexus › Appearance and in the panel's own Settings.
  - States: *Not installed*, *Incompatible*, *Disabled*, *Ready* and *Active*.
  - With HCG Halo 0.3.1~beta1 the switch turns HCG Halo's panel border on or off through HCG Halo's own setting, and never turns on HCG Halo's screen-edge light.
  - *Active* means Nexus itself sees HCG Halo drawing around the open panel.
  - Without HCG Halo, Nexus keeps its own outline.
- **HCG Terminal** in HCG Products:
  - Installed (with the version read from the app), Launch, Integration and Session are shown separately.
  - Opening the app is never shown as *Connected*; Command Mode shows *Launch available*.
  - *Open HCG Terminal* works with today's HCG Terminal 0.1.0.
  - *Open Saved Connection…* appears only with an HCG Terminal build that has the Nexus handoff. It accepts only a link copied from HCG Terminal, and never runs a command.
- Works offline; no sign-in needed.

## 0.3.1~beta1

The combined candidate: the redesign, Command Mode and the optional HCG Halo border interface. It is free in HCG Repo
Candidate only. Simulator-tested; real-device testing is pending. It replaces 0.3.0~beta1, which was never published,
and supersedes 0.2.2~beta1.

- **Redesign:** a smoked-glass panel with adjustable opacity and blur. It respects Reduce Transparency, Reduce Motion
  and Increase Contrast.
  - The official chrome HCG logo is in the header, and the official HCG Nexus banner is on the Overview.
  - Seven sections: Overview, Workspaces, Tools, HCG Products, Connected Devices, Apps and Account.
  - Text follows Dynamic Type, scaled once, and the chrome is capped at the largest sizes.
- **Your data:** your 0.2 workspaces and shortcuts are kept. *Tools › Restore Previous Layout* undoes the migration.
- **Command Mode** (the grid button in the panel header) shows:
  - device status: battery, charging, Low Power, network, storage and thermal;
  - your shortcuts;
  - HCG integration states, which never claim a connection they cannot prove.
- **HCG Halo border interface** (interface 1): an installed, declarative border definition
  (`HCGNexusPrefs.bundle/HaloBorder.plist`, schema 1) and a non-interactive border host inside the panel, with open,
  close, geometry and workspace events. With HCG Halo 0.2.0~beta1 installed, Halo draws its animated border around the
  panel. Nexus never requires Halo.
- New Settings row: *Appearance › Halo border*, which opens HCG Halo's border page.
- **HCG Connect (standalone app):** token responses are verified (ES256/JWKS, issuer, audience, expiry, nonce) before use.
  Sign-in stays *Not configured* until HCG issues Nexus's client.
- Not yet verified on a physical device.

## 0.2.2~beta1

Candidate beta: the 0.2.1~beta1 code plus an optional border interface for HCG Halo. Free in HCG Repo Candidate only.

- Installs a declarative border definition (`HCGNexusPrefs.bundle/HaloBorder.plist`, schema 1).
- Adds an in-process, non-interactive border host inside the panel, with open, close, geometry and workspace events
  (interface 1).
- With HCG Halo 0.2.0~beta1 installed, Halo can draw its animated border around the panel. Nexus never requires Halo.
- New Settings row: *Appearance › Halo border*, which opens HCG Halo's border page when Halo is installed.
- Not yet verified on a physical device.

## 0.2.1~beta1

Candidate beta for device testing (HCG-Nexus commit `58e2dbd`, Xcode build run #13). Free in HCG Repo Candidate only.

- Same tweak and Settings code as 0.2.0 (HCG-Nexus run #10): only the version and the packaging change.
- Packaged with gzip instead of legacy lzma (`data.tar.lzma`, 64 MiB dictionary), the format of a beta whose unpack crashed on a device.
- Not yet verified on a physical device.

## 0.2.0

First device release candidate (HCG-Nexus commit `907b1df`, Xcode build run #9).

- System-wide cyan edge handle on the left or right side, with adjustable position and visibility.
- Command panel: searchable shortcuts, workspaces (Command, Developer, Studio, Infrastructure presets), recent actions.
- Settings › HCG Nexus: enable switch (off by default), status and copyable diagnostics.
- Hides on the lock screen and the cover sheet; respects Reduce Motion.
- Not yet verified on a physical device.

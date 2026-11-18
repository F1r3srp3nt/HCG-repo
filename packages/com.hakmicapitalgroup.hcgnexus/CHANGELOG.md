# HCG Nexus changelog

## 0.3.1~beta1

Combined candidate: the 0.3.0~beta1 redesign, Command Mode and the optional HCG Halo border interface. It is free in
HCG Repo Candidate only. Simulator-tested; real-device testing is pending. It supersedes 0.2.2~beta1 and 0.3.0~beta1,
which were never published.

- **Redesign:** a smoked-glass panel with adjustable opacity and blur. It respects Reduce Transparency, Reduce Motion
  and Increase Contrast.
  - The official chrome HCG logo is in the header, and the HCG Nexus emblem is on the Overview.
  - Seven sections: Overview, Workspaces, Tools, HCG Products, Connected Devices, Apps and Account.
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

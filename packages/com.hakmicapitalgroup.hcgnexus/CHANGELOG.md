# HCG Nexus changelog

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

# HCG Nexus changelog

## 0.3.0~beta1

Redesign candidate (HCG-Nexus branch `claude/hcg-nexus-tweak-yb1ii2`). Free public demo in HCG Repo Candidate only.

- Smoked-glass panel with adjustable opacity and blur; respects Reduce Transparency, Reduce Motion and Increase Contrast.
- Official chrome HCG logo in the header; HCG Nexus emblem on the Overview (no Nexus banner yet).
- Seven sections replace the single shortcut list: Overview, Workspaces, Tools, HCG Products, Connected Devices, Apps and Account.
- Apps: favourites, search and ordering. Workspaces save their own quick actions and pinned tools.
- Your 0.2 workspaces and shortcuts are kept. Tools › Restore Previous Layout undoes the migration.
- HCG Products: installed / not installed / unable to determine, kept separate from account state.
- Connected Devices and HCG sign-in remain standalone-app features; the tweak says so instead of faking them.
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

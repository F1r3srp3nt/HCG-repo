# Physical-device checklist

Nothing in this repository counts as device evidence until this checklist has been run on real
hardware. Do not mark a version `device-tested`, or report that Sileo installation or shell
execution works, without the evidence below.

## Record first

| Field | Value |
|---|---|
| Date | |
| Tester | |
| Device model and chip | |
| iOS / iPadOS version (Settings → General → About) | |
| Jailbreak, version, layout (rootless/rootful) | |
| Bootstrap (e.g. Procursus) and `dpkg --print-architecture` | |
| Sileo version | |
| Source URL used | `https://f1r3srp3nt.github.io/HCG-repo/repo/` |
| Package version and SHA-256 (from the website) | |

Before starting, delete any sideloaded **HCG Terminal Unleashed** (from Xcode, an IPA or TrollStore), because it has the same bundle id. The standard **HCG Terminal** from TestFlight or the App Store can stay installed. Its data must not be affected, and step 11 checks that.

## Steps

For each step, record pass/fail and a screenshot or the terminal output.

**Source and package**

1. **Add the source.** Open the website on the device and tap *Add to Sileo*. Sileo's Add Source prompt shows the exact URL; confirm it. (Alternative: *Sources → +*, paste the URL.)
2. **Refresh.** The source appears as "HCG Repo" with the HCG icon, and the refresh finishes without errors. The source's package list matches the website.
3. **Find the package.** Open *HCG Terminal Unleashed* in Sileo and check:
   - the depiction (header, Details and Changelog tabs);
   - the version matches the website;
   - only the build for your layout is offered.

**Install and launch**

4. **Review dependencies and install.** The confirmation screen should list only the expected dependencies (e.g. `firmware`, `uikittools`). Install. Save the full Sileo log, including the `uicache` line from `postinst`.
5. **Launch.** The icon appears on the home screen; tap it. Check *Diagnostics → Build*:
   - the variant is "Unleashed (backend included)";
   - the bundle id is `com.hakmicapitalgroup.hcgterminal.unleashed`;
   - the version matches.

**Shell**

6. **Shell test.** *Settings → Unleashed Environment → Run Shell Test* should report *Verified working*. If it reports *Permission denied*, record the errno and stop: the entitlements or install route do not work on this jailbreak.
7. **Local shell.** Open a local Unleashed session and run:
   ```
   id
   pwd
   echo $SHELL
   ls /var/jb/usr/bin | head
   ```
   - Expected: `id` shows the app's normal user (typically `uid=501(mobile)`), **not** `uid=0(root)`.
   - Record the exact output.
8. **Terminal I/O.** Check:
   - typing and backspace;
   - a long-running command (`sleep 30`) interrupted with Ctrl-C;
   - `printf 'a\tb\n'`;
   - resizing or rotating the device and then running `stty size`;
   - `exit`, after which the session shows its exit status and closes cleanly.

**Upgrade and removal**

9. **Upgrade.** Publish the next version (higher build number) and refresh in Sileo.
   - Sileo offers the upgrade; install it.
   - The app still launches, the shell test still passes, and the settings and hosts you created are still there.
10. **Uninstall.** Sileo → *Installed* → the package → *Remove*.
    - The icon disappears (`postrm` runs `uicache -u`).
    - `/var/jb/Applications/HCGTerminalUnleashed.app` is gone.
    - Record whether the app's data container remains.
11. **Unrelated data unaffected.** After removal:
    - the standard HCG Terminal (if installed) still opens with its data;
    - other apps and other Sileo sources are unchanged;
    - `apt`/`dpkg` report no broken packages (`dpkg --audit` prints nothing).
12. **Source removal** (optional). Delete the source in Sileo and confirm nothing else changes.

## Recording a pass

Store the evidence (screenshots, logs, command output) somewhere durable, e.g. `docs/device-tests/<date>-<device>.md` in this repository. Then add an entry to `packages/<id>/package.toml`:

```toml
[versions."0.1.0-2"]
status = "device-tested"
[[versions."0.1.0-2".device_tests]]
device = "iPhone 12 (A14)"
ios = "17.0"
jailbreak = "Dopamine 2.x (rootless)"
date = "2026-11-20"
result = "pass"
evidence = "docs/device-tests/2026-11-20-iphone12.md"
```

The validator refuses `device-tested` without a passing entry that has every field filled in. The website then shows **Device-tested** with that device, iOS version and jailbreak, and makes no claim about other combinations.

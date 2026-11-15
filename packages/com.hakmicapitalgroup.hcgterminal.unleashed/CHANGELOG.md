# HCG Terminal Unleashed changelog

Entries are keyed by the exact Debian version in the package's control file (`## <version>`).

## 0.1.0-1

First jailbreak package, built from HCG-Terminal commit `ddc5197` with the project's documented
`Scripts/package-ios.sh unleashed … --deb rootless` route.

- Local shell in a pseudo-terminal (input/output, resize, Ctrl-C, exit status), where the device allows it.
- Read-only environment scan, program list, file browser, process list and package list.
- Remote SSH development, unchanged from the standard edition.
- Not yet verified on a physical device.

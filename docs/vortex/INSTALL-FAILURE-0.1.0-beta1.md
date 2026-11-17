# For the Vortex project: 0.1.0~beta1 does not install (dpkg-deb Bus error)

**To:** the maintainers of HCG-Jailbreak-Research `HCGVortex/`. This session cannot write to that repository, so
this handoff lives here. Copy it into an issue or `HCGVortex/docs/`.

## Observed (device)

- **Setup.** iPhone 11 Pro Max, iOS 17.4, Dopamine 2.x (rootless), Zebra 1.1.x, 17 Nov 2026.
- **Result.** Listing and download worked. Unpacking failed:

  ```
  dpkg-deb: error: <decompress> subprocess was killed by signal (Bus error: 10)
  dpkg: error processing archive …/xyz.willy.Zebra/debs/com.hakmicapitalgroup.vortex_0.1.0~beta1.deb (--unpack):
   dpkg-deb --fsys-tarfile subprocess returned error exit status 2
  ```

- **Runtime.** Nothing was installed, so nothing ran.
- **Full record.** HCG-repo `docs/device-tests/vortex-0.1.0-beta1.md`.

## The artifact (unchanged, retained)

| | |
|---|---|
| Source | HCG-Jailbreak-Research `7d5e5b9153e9e7246d87e6f3bf4f7c6927d42ad4`, run 43970942708, artifact 14278312698 (`hcg-vortex-rootless-deb`) |
| File | `com.hakmicapitalgroup.vortex_0.1.0~beta1_iphoneos-arm64.deb`, 81,650 bytes |
| SHA-256 | `bb0af72745a36158d4022c1b97ebc9800847cf4b1fb42ca14d05b9fc07673eea` |
| Members | `debian-binary`, `control.tar.gz` (736 B), `data.tar.lzma` (80,722 B) |
| `data.tar.lzma` header | lzma-alone, properties `0x5d` (lc=3 lp=0 pb=2), **dictionary 67,108,864 B (64 MiB)**, uncompressed size unknown (-1) |
| Off-device check | `dpkg-deb --fsys-tarfile` (dpkg 1.22.6, Linux) and liblzma both decode it (560,640 B); the file is not corrupt |

## Hypothesis to test (not confirmed)

On the phone, `dpkg-deb` decompresses the data member in a subprocess. For lzma-alone streams the decoder allocates
the declared dictionary, here 64 MiB for 548 KiB of content. A **Bus error** in that subprocess points at the
decompressor itself, so the format and dictionary size are the first suspects.
- HCG Terminal Unleashed's `.deb` uses `data.tar.gz`.
- HCG Nexus 0.2.0 uses the same `data.tar.lzma` and has never been installed on a device.

## Suggested next steps for Vortex

1. **Reproduce on the phone, over SSH, without changing anything installed.** Each command only reads the file:
   ```sh
   cd /var/mobile/Documents
   shasum -a 256 com.hakmicapitalgroup.vortex_0.1.0~beta1_iphoneos-arm64.deb    # expect bb0af727…
   dpkg-deb -I com.hakmicapitalgroup.vortex_0.1.0~beta1_iphoneos-arm64.deb       # control: worked in Zebra
   dpkg-deb -c com.hakmicapitalgroup.vortex_0.1.0~beta1_iphoneos-arm64.deb       # lists data: expected to fail the same way
   dpkg --version; dpkg-deb --version
   ```
2. **If `dpkg-deb -c` fails the same way, rebuild with a different data compression.** Set Theos's deb compression in
   `HCGVortex/Makefile`, preferably **gzip** (what HCG Terminal Unleashed uses), or **xz with a small preset**. Do not
   reuse the version number: publish **0.1.0~beta2**. It sorts after 0.1.0~beta1 and before 0.1.0, so testers are
   offered it as an upgrade. Record its commit, run, artifact ID and SHA-256 as for beta1.
3. **Add a packaging check** to `validate.sh`: fail if the data member is `data.tar.lzma`, or if its dictionary is
   larger than needed. Then the same problem cannot reach a device again.
4. Then run the device handoff (A–G) on the iPhone 11 Pro Max, iOS 17.4.

HCG Repo keeps 0.1.0~beta1 listed in the **candidate** channel only, marked "Installation failed in testing", for
reproduction on other devices. It is not in the stable source. A fixed build is imported the same way, through a
pinned import request with `channel = "candidate"`.

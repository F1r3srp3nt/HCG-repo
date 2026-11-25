# HCG Mail Unleashed 0.1.0~beta1: test record

**Package under test:** `com.hakmicapitalgroup.hcgmail.unleashed_0.1.0~beta1_iphoneos-arm64.deb`, 43,663,718 bytes,
SHA-256 `ac4b31b10dbdf9f060b6e02274c3123c4e95045b8ba3b458f77f99387563099a`.
- Built from F1r3srp3nt/HCG-Mail commit `b0c6fee17c756a1dffff62e77574ad60b22415be`, Unleashed run 45580748239, artifact 15008300234.
- Published to HCG Repo Candidate by Publish repository run 45592182860 (HCG Repo `ec24cc8`, 25 Nov 2026). The published
  bytes stay as they are: a fixed build is a new version (`0.1.0~beta2`), never new bytes for `0.1.0~beta1`.

This record holds only what was observed. A step that was not done stays **not done**.

## Installation and first launch, 25 Nov 2026: installed, launch FAILED

| Field | Value |
|---|---|
| Device | The owner's jailbroken iPhone. **Model not yet reported**: to be added from the device's crash report |
| iOS | **Not yet reported**: to be added from the crash report |
| Jailbreak | Rootless (the package installs only under `/var/jb`). Jailbreak name and version not yet reported |
| Package manager | Zebra |
| Source | HCG Repo Candidate, `https://repo.hakmicapitalgroup.com/repo/candidate/` |
| Evidence | The owner's report (below). The device's crash report is pending |

**Results, as reported by the owner:**

| Step | Result |
|---|---|
| Repository distribution (source, listing, download) | Pass |
| Package installation | Pass |
| Application registration | Pass |
| Home Screen icon | Pass |
| Application startup | **Fail**: "The application immediately closes after attempting to launch it." |

**Not done:** mail (IMAP/SMTP) and Keychain storage; the app never reached its first screen.

**Cause:** not yet determined. No root cause is claimed until the crash report has been read.

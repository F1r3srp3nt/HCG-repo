# HCG Executable Maker → HCG Repo submission contract (v1)

**For:** HCG Executable Maker's Unleashify (`F1r3srp3nt/hcg-executable-maker`). **Status:** implemented in HCG Repo
(`tools/hcgrepo/submission.py`, `release.py`); Maker can emit it today. A local build never needs a GitHub Actions run.

## 1. Submission folder

Unleashify exports one folder per build:

```
<package>_<version>-hcg-repo-submission/
  submission.json          required  manifest, format hcg.repo.artifact-submission v1 (§2)
  <package>_<version>_<arch>.deb     required  the exact bytes; the file name submission.json names
  SHA256SUMS               required  "<sha256>  <file>.deb" (must agree with submission.json)
  package.toml             required for a package HCG Repo does not have yet: draft metadata in HCG Repo's format,
                           including [review.entitlements."<path>"] with the exact entitlements of every signed binary,
                           [access] level, and NO [access] exception (only the owner approves one)
  CHANGELOG.md             required for a new package, recommended otherwise ("## <version>" sections)
  icon.png                 required for a new package (square PNG)
  KNOWN-ISSUES.md          optional
  DEVICE-TEST.md           optional: the device checklist; results are recorded only after a real device run
  VALIDATION.txt           optional: Maker's own report (kept as a claim)
```

Older handoffs (`<package>_<version>-candidate-handoff/` with `provenance.json`) are still accepted and converted;
a native `submission.json` replaces that conversion and removes its warnings (undeclared maintainer scripts).

## 2. `submission.json`

Schema: [schemas/artifact-submission-v1.schema.json](schemas/artifact-submission-v1.schema.json). Field by field:

| Need | Field | Rules |
|---|---|---|
| Artifact bytes | `artifact.filename` | plain `*.deb` name in the folder |
| Exact SHA-256 | `artifact.sha256` | 64 lowercase hex; must equal SHA256SUMS and the file |
| File size | `artifact.size` | bytes, exact |
| Package metadata | `package.identifier`, `version`, `architecture`, `name`; `dependencies`; `compatibility.minimumOS`, `environment` (rootless/rootful/roothide), `jailbreaks`, `deviceFamilies` | all compared with the `.deb`'s control file, app `Info.plist` and Mach-O load commands |
| Source commit | `source.repository`, `source.commit` (40 hex), `source.dirty` = `false`, `source.branch` | the source may be private: recorded, never fetched |
| Build provider | `build.provider` | `executable-maker-local` for a build on the developer's machine; `github-actions` / `github-actions-private` only with `build.runId`, `runRepository`, `artifactName` (refused for every other provider) |
| Toolchain | `build.toolchain`, `build.builtAt` | e.g. `"Xcode 27.2 (27C5000), HCG Executable Maker 0.5.0"`; ISO 8601 time |
| Validation evidence | `validation.tool`, `validation.checks[] {check, status, detail}` | kept verbatim as a claim; HCG Repo validates independently |
| Signing and entitlements | `signing.method` (`ldid`, `apple-development`, `apple-distribution`, `unsigned`), `signing.signedFiles[]`, `signing.entitlements{ "<path in package>": {…} }` | every listed file must be a signed Mach-O; every entitlements dictionary must equal what the binary carries |
| Maintainer scripts | `maintainerScripts.declared[]` | exactly the scripts in the package |
| Intended channel | `channel` = `"candidate"` | the only accepted value |

## 3. CLI operations Maker can call

All run from an HCG Repo checkout (`bin/hcgrepo`, Python 3.11+, `dpkg`/`dpkg-deb` on PATH). The incoming store is
private (`$HCG_REPO_INCOMING`, default `~/.local/share/hcg-repo/incoming`, refused inside the checkout).

| Operation | Command | Exit code |
|---|---|---|
| Submit and validate | `bin/hcgrepo release submit --handoff <folder> --result-json <file>` | 0 validated, 1 refused, 2 usage |
| Status for the GUI | `bin/hcgrepo release status <id>` (JSON on stdout) | 0 |
| List | `bin/hcgrepo release incoming --json` | 0 |
| Human review | `bin/hcgrepo release show <id>` | 0 |
| Owner approval | `bin/hcgrepo release approve <id> --sha256 <sha> --by "<owner>" --record "<words>"` | owner only; Maker never runs it |
| Candidate preparation | `bin/hcgrepo release prepare <id> [--via pool or local-file] [--create-package-metadata] [--free-demo-approval "<owner's words>"]` | after approval only |
| Inspect a package | `bin/hcgrepo inspect --deep --json <file.deb>` | 0 ok, 1 errors |

Maker should show the result and the owner's next action; it must not call `approve`, `prepare` or Publish on the
owner's behalf.

## 4. Submission result (`hcg.repo.submission-result` v1)

Schema: [schemas/submission-result-v1.schema.json](schemas/submission-result-v1.schema.json).

```json
{
  "format": "hcg.repo.submission-result", "formatVersion": 1,
  "submission": "com.hakmicapitalgroup.example_0.1.0-beta1_iphoneos-arm64_3f2a9c1b7d4e",
  "state": "validated",
  "package": {"identifier": "…", "version": "0.1.0~beta1", "architecture": "iphoneos-arm64"},
  "artifact": {"sha256": "…", "size": 123456},
  "provider": "executable-maker-local",
  "levels": {
    "static_inspection":        {"state": "passed"},
    "maintainer_script_review": {"state": "passed", "executed": false},
    "script_free_lifecycle":    {"state": "passed", "scripts_executed": false, "steps": [ … ],
                                 "note": "… does not prove the original maintainer scripts work"},
    "device_installation":      {"state": "not-recorded"},
    "application_launch":       {"state": "not-recorded"},
    "runtime_verification":     {"state": "not-recorded"}
  },
  "counts": {"errors": 0, "warnings": 1},
  "findings": [{"level": "warning", "stage": "repository", "code": "…", "message": "…"}],
  "requirements": [
    {"kind": "checksum-approval", "satisfied": false, "command": "bin/hcgrepo release approve …"},
    {"kind": "free-demo-approval", "satisfied": false, "detail": "…"},
    {"kind": "publication-approval", "satisfied": false, "detail": "…"}
  ],
  "next_step": "wait for the owner's checksum approval",
  "channel": "candidate"
}
```

- `state`: `refused`, `validated`, `approved`, `prepared` or `withdrawn`.
- Level states: `passed`, `passed-with-warnings`, `failed`, `partial`, `not-run`, `not-recorded`, `not-applicable`.
- `stage` of a finding: `manifest`, `static`, `scripts`, `lifecycle` or `repository`.
- The result never contains credentials, the private store path or source code.

**Display rule:** show the six levels separately. A passed `script_free_lifecycle` means dpkg handled the payload with
the maintainer scripts removed; never present it as proof that the scripts, the installation on a device or the app
work.

## 5. CI builds (no local folder)

A GitHub Actions build (e.g. HCG Mail's `unleashed.yml`) uploads the `.deb` as an artifact. HCG Repo's
**Inspect artifact** workflow (`workflow_dispatch`: repository, run ID, commit, artifact) downloads it, runs the same
review and reports the exact SHA-256 and the levels without committing anything. After the owner's approval of that
SHA-256, a `github-artifact` import request pins the run, commit, artifact and SHA-256.

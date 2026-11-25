# imports/

Each `*.toml` file here pins one package to an immutable source. The **Prepare packages**
workflow (`.github/workflows/prepare-packages.yml`) runs on pushes to non-`main` branches that touch this
directory. For each request that is not yet in `repo-lock.json`, it:

1. fetches the pinned artifact or release asset, or builds the package from the pinned commit with
   the project's own documented build script (macOS runner);
2. imports the result with `bin/hcgrepo import-request`, which checks the expected package, version,
   architecture and (when given) SHA-256, and runs every repository validation;
3. commits `pool/…deb`, its `provenance.json` and `repo-lock.json` back to the same branch, for review.

Nothing is published: publishing remains the manual **Publish repository** workflow on `main`.
A request is refused if it uses "latest", a moving reference, or a commit that is not a full SHA.

```toml
[request]                      # what the package must be (checked against its control file)
package = "com.hakmicapitalgroup.example"
version = "1.0.0"
architecture = "iphoneos-arm64"

[source]                       # one of the three kinds below
kind = "github-artifact"       # a workflow-run artifact
repository = "OWNER/NAME"
run_id = 123456789
commit = "<40-char sha the run built>"
artifact = "<artifact name>"
sha256 = "<expected .deb sha256, optional but recommended>"

# kind = "github-release"; tag = "v1.0.0"; asset = "file.deb"; commit = "<sha the tag points to>"
# kind = "build"; recipe = "hcg-terminal-unleashed"; layout = "rootless"; entitlements = "path/in/source.plist"
```

Distribution v2 ([docs/DISTRIBUTION_V2.md](../docs/DISTRIBUTION_V2.md)) adds two kinds. Both go to the candidate channel
only, need the owner's approval of the exact `sha256` in `submissions/<id>/review.json`, and have no `run_id`:

```toml
# kind = "local-submission"   # written by `bin/hcgrepo release prepare`; the bytes come from the private incoming
#                             # store, so in CI an unimported one reports AWAITING-LOCAL-IMPORT (not a failure)
# provider = "executable-maker-local"; submission = "submissions/<id>/submission.json"; sha256 = "<approved>"

# kind = "https-artifact"     # HCG Server Build Host or an approved developer upload
# provider = "hcg-server-build-host"; url = "https://…/file.deb"; size = 123456; sha256 = "<approved>"
# submission = "submissions/<id>/submission.json"; auth_env = "HCG_BUILD_HOST_TOKEN"   # names a CI secret
```

Private GitHub repositories use `kind = "github-artifact"` unchanged; the workflow reads them with the
`HCG_IMPORT_TOKEN` secret. Every request-driven import also runs the v2 validation engine (secrets, maintainer
scripts, privileged helpers, simulated dpkg lifecycle with scripts never executed).
### Locally built packages (`kind = "local-file"`)

For a package built outside GitHub Actions, for example by HCG Executable Maker's Unleashify on the owner's Mac when
the app's source repository is private. The approved `.deb` is committed under `imports/local/` next to its request,
and **Prepare packages** imports it in CI. Only the binary package and the source's name and commit enter HCG Repo:
the source stays private and no token is needed.

It shares the Release Manager's validation, approval and provenance ([DISTRIBUTION_V2.md](../docs/DISTRIBUTION_V2.md)).
Do not write this request by hand; the Release Manager writes it after the owner's approval:

```
bin/hcgrepo release submit --handoff <Unleashify handoff folder>     # private store, independent validation
bin/hcgrepo release approve <id> --sha256 <exact sha> --by "<owner>" --record "<approval, verbatim>"
bin/hcgrepo release prepare <id> --via local-file                    # stages imports/local/ + imports/<id>.toml
```

```toml
[request]
package = "com.hakmicapitalgroup.example"
version = "0.1.0~beta1"
architecture = "iphoneos-arm64"
channel = "candidate"                # local-file imports are Candidate only

[source]
kind = "local-file"
provider = "executable-maker-local"
repository = "OWNER/NAME"            # the app's source (may be private: recorded, never fetched)
commit = "<40-char sha the package was built from>"
submission = "submissions/<id>/submission.json"   # its review.json must approve exactly this sha256
sha256 = "<exact .deb sha256>"
path = "imports/local/com.hakmicapitalgroup.example_0.1.0~beta1_iphoneos-arm64.deb"
built_by = "HCG Executable Maker 0.5.0 (Unleashify, local)"
built_at = "2026-11-25T10:00:00Z"    # optional extras: toolchain, host, source_visibility, validation
```

Prepare packages checks the committed file's SHA-256, the owner's approval, package, version and architecture, and
runs the v2 validation engine and every repository validation. It records the shared provenance and removes the
`imports/local/` copy once the package is in `pool/`. `validate` refuses any package file in `imports/` that is not
an approved `local-file`, and it never allows a run ID or the stable channel here. A Candidate build of a sign-in or
paid package still needs the owner's free-demo approval in `packages/<id>/package.toml` ([ACCESS.md](../docs/ACCESS.md)).

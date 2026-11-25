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

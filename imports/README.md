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

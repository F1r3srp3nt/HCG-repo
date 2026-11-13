# Importing and publishing packages

All commands run from the repository root. `bin/hcgrepo <command> --help` lists every option.

## 1. Review the package

```bash
bin/hcgrepo inspect ~/Downloads/com.hakmicapitalgroup.example_1.0.0_iphoneos-arm64.deb
```

This prints the control file and the installed paths. It also lists each Mach-O with its architectures, signature state, entitlements and linked libraries, then the maintainer scripts. Finally it shows every validation error and warning. Nothing is imported.

Fill in `packages/<package-id>/package.toml` before importing (copy the HCG Terminal one as a template):

- `[display]`: summary, `icon`, `header`, `tint`, and notes shown on the depiction.
- `[review.entitlements."<path in package>"]`: the exact entitlements of every signed Mach-O. An import fails if a binary carries entitlements that are not recorded here, or that differ from them. Recording them is the explicit review step.
- `allow_setuid`: normally empty.
- `[versions."<version>"]`: `status = "device-tested"` together with `device_tests` evidence. Without an entry, a version is labelled *Experimental*.
- `CHANGELOG.md`: one `## <exact Debian version>` section per version.

Versions, architectures and dependencies are never written by hand. They come from the `.deb`'s own control file. The repository adds only `Depiction`, `SileoDepiction`, `Icon` and `Header` (presentation URLs), and `Filename`, `Size` and checksums.

## 2. Import

There are three import routes, and every route runs the same validation.

**Local file**:

```bash
bin/hcgrepo import-deb path/to/pkg.deb --built-from F1r3srp3nt/HCG-Terminal@<full commit sha> [--note "..."]
```

**GitHub release asset.** You must give an exact tag, plus the full commit SHA that tag must point to. `latest` is refused.

```bash
bin/hcgrepo import-github-release --repo F1r3srp3nt/HCG-Terminal --tag v0.1.0-2 \
  --commit <40-char sha> --asset com.hakmicapitalgroup.hcgterminal.unleashed_0.1.0-2_iphoneos-arm64.deb \
  [--sha256 <expected>]
```

The tool resolves annotated tags to their commit and refuses the import on a mismatch. It refuses draft releases. It checks the download's size and GitHub's asset `digest` (and `--sha256`, when given).

**GitHub Actions artifact.** This needs `GITHUB_TOKEN` with `actions:read` on the source repository.

```bash
GITHUB_TOKEN=... bin/hcgrepo import-github-artifact --repo F1r3srp3nt/HCG-Terminal --run-id 123456789 \
  --commit <40-char sha> --artifact deb-rootless [--member path/in/zip.deb]
```

The run must have completed successfully and must have built exactly `--commit`. The artifact must not have expired. If the zip holds more than one `.deb`, you choose one with `--member`.

On success the import does three things:

- It writes `pool/<arch>/<package>_<version>_<arch>.deb`.
- It writes `…deb.provenance.json`, which records the source, repo, tag/run, commit, release/asset/artifact ids, digests, SHA-256, SHA-512 and an inspection summary.
- It appends an entry to `repo-lock.json`.

Commit these together in a pull request. The **Validate repository** workflow re-runs every check, and it compares `repo-lock.json` against the base branch.

### Immutability

- Filenames are canonical: `<package>_<version>_<architecture>.deb`. Epochs are refused, so a filename always identifies exactly one version.
- `repo-lock.json` stores the SHA-256 of every imported package version.
  - Re-importing identical bytes does nothing.
  - Importing different bytes for an existing package/version/architecture is refused: bump the version.
- In pull requests, lock entries that already exist on `main` cannot be changed or deleted. They can only move from `published` to `withdrawn`.

### Withdrawing a version

```bash
bin/hcgrepo withdraw --package <id> --version <v> --arch <arch> --reason "why"
```

This deletes the file from `pool/` and marks the lock entry `withdrawn`, so that version can never be re-published with other content. The next publish removes it from the index.

## 3. Build and test locally

```bash
bin/hcgrepo validate                         # pool + lock + provenance + dependencies
bin/hcgrepo build --target pages --out _site # or --target production, or --base-url https://…/
bin/hcgrepo serve --target pages --dir _site # serves under /HCG-repo/, 404 outside it
bin/hcgrepo verify --target pages --fetch-url http://127.0.0.1:8000/HCG-repo/ --list-urls
bin/hcgrepo smoke                            # all of the above, for every base URL in config
```

`build` writes the snapshot into a temporary directory and verifies it. Only then does it replace `--out`, so a failed build never leaves a half-written site behind.

The base URL is configurable in four ways:

- `config/repo.toml [urls]` (`pages`, `production`, `default_target`);
- `--target`;
- `--base-url`;
- the `HCG_REPO_BASE_URL` environment variable.

Absolute URLs (depictions, icons, the website's Sileo link) are derived from the base URL. `Filename` is always relative to the source root (`debs/…`). Website links are relative, so the same tree works under `/HCG-repo/` and at a domain root, as long as the build used the right base URL.

## 4. Publish

Run **Actions → Publish repository → Run workflow** on `main`, choose the target, and type `PUBLISH`. The workflow has three jobs:

1. **build** (`contents: read`): runs the tests, `validate` and `smoke`. If the signing secret exists, it imports the key into a temporary GNUPGHOME. It then builds the snapshot, with the date taken from the commit, and uploads it as the Pages artifact. The key material is removed afterwards.
2. **deploy** (`pages: write`, `id-token: write`): `actions/deploy-pages`.
3. **verify-deployed** (`contents: read`):
   - waits until `build-info.json` on the live site shows this commit;
   - runs `hcgrepo verify` against the HTTPS URLs: Release, every index, checksums, every `.deb` download, depictions, icons and the website.

Every action is pinned to a full commit SHA. Nothing is published automatically on push.

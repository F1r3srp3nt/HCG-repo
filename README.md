# HCG-repo
Jailbreak Repository for Hakmi Capital Group Projects

A flat APT repository for Sileo and compatible package managers, with the tooling to validate,
import, index, sign, build, publish and verify it. It distributes HCG's own jailbreak apps, tweaks
and command-line tools.

| | |
|---|---|
| Test source (GitHub Pages, after the first publish) | `https://f1r3srp3nt.github.io/HCG-repo/repo/` |
| Production source (after the custom-domain migration) | `https://repo.hakmicapitalgroup.com/repo/` |
| Website | the same URLs without `repo/` |
| Packages published | **none yet** (the repository is valid and empty) |
| Metadata signing | supported, **no key configured yet** |

## Status

- **Infrastructure:** complete and tested locally. 78 automated tests, plus build → serve under the real path prefix → verify over HTTP, for both base URLs.
- **Deployment:** not yet done. GitHub Pages is not enabled for this repository yet, and publishing runs only from `main`. See [First deployment](#first-deployment).
- **HCG Terminal Unleashed:** not published. No built `.deb` exists yet: HCG-Terminal's CI builds one but does not upload it. The packaging has been reviewed and two fixes are recommended; see [docs/TERMINAL_UNLEASHED.md](docs/TERMINAL_UNLEASHED.md).
- **HCG Nexus, HCG Sentinel:** shown on the website as planned and unavailable, with no install button.
- **Device testing:** none. Nothing has been installed through Sileo on a device. Use [docs/DEVICE_CHECKLIST.md](docs/DEVICE_CHECKLIST.md).

## Quick start

Requirements: Python 3.11+ (standard library only), `dpkg-deb`, and `gpg`/`gpgv` (signing only).
Debian/Ubuntu: `apt-get install dpkg gnupg`. macOS: `brew install dpkg gnupg`.

```bash
bin/hcgrepo inspect path/to/package.deb             # review a package; does not import it
bin/hcgrepo import-deb path/to/package.deb \
    --built-from F1r3srp3nt/HCG-Terminal@<40-char sha>   # validate + add to pool/ + lock + provenance
bin/hcgrepo validate                                # whole pool, lock, provenance, dependencies
bin/hcgrepo build --target pages --out _site        # complete static snapshot
bin/hcgrepo serve --target pages --dir _site        # http://127.0.0.1:8000/HCG-repo/ (real prefix)
bin/hcgrepo verify --target pages --fetch-url http://127.0.0.1:8000/HCG-repo/
bin/hcgrepo smoke                                   # build + serve + verify, for every configured base URL
python3 -m unittest discover -s tests               # test suite
```

## Layout

```
config/repo.toml                  repository identity, architectures, base URLs, signing policy
config/external-dependencies.toml dependencies provided by the bootstrap or other repos (allow-list)
config/products.toml              planned products shown as "not available" on the website
packages/<id>/                    presentation + review metadata per package (icon, header, changelog,
                                  reviewed entitlements, device-test evidence). Never versions or deps.
pool/<arch>/<pkg>_<ver>_<arch>.deb    imported packages (immutable), each with .provenance.json
repo-lock.json                    append-only record of every imported package and its SHA-256
branding/                         repository icon (CydiaIcon*.png) and brand provenance
keys/                             public archive key, once one exists (never private keys)
tools/hcgrepo/                    the tool (Python, no third-party dependencies)
bin/hcgrepo                       entry point
tests/                            unit and regression tests
.github/workflows/validate.yml    pull-request and push validation (read-only)
.github/workflows/publish.yml     manual publication to GitHub Pages (workflow_dispatch, main only)
```

Generated output, which is never committed: `_site/` holds the website, and `_site/repo/` holds the APT source (`Release`, `Packages{,.gz,.bz2,.xz}`, optionally `Release.gpg` and `InRelease`, plus `debs/`, `depictions/<id>/{index.html,sileo.json}`, `assets/<id>/`, `CydiaIcon*.png`). The output also includes `build-info.json`.

## First deployment

1. Merge this work into `main`. Publishing is refused from any other branch.
2. In GitHub, open **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**. This is the only repository setting that has to change.
3. Go to **Actions → Publish repository → Run workflow**. Choose target `pages` and type `PUBLISH`.
4. The workflow builds and verifies the snapshot, deploys it, then waits until `https://f1r3srp3nt.github.io/HCG-repo/build-info.json` reports this commit, and verifies every live HTTPS endpoint.
5. Follow [docs/DEVICE_CHECKLIST.md](docs/DEVICE_CHECKLIST.md) on a device.

## Documentation

- [docs/PUBLISHING.md](docs/PUBLISHING.md): importing, provenance, immutability, building, publishing, withdrawing.
- [docs/ROOTLESS_ROOTFUL.md](docs/ROOTLESS_ROOTFUL.md): architectures, paths, libroot, separate sources.
- [docs/SIGNING.md](docs/SIGNING.md): repository-metadata signing and key management. This is not iOS code signing.
- [docs/CUSTOM_DOMAIN.md](docs/CUSTOM_DOMAIN.md): migrating to `repo.hakmicapitalgroup.com`. Not done yet.
- [docs/TERMINAL_UNLEASHED.md](docs/TERMINAL_UNLEASHED.md): packaging review, and what is needed to publish it.
- [docs/DEVICE_CHECKLIST.md](docs/DEVICE_CHECKLIST.md): physical-device test procedure.
- [docs/VALIDATION.md](docs/VALIDATION.md): what the validator rejects, and the latest results.

# HCG-repo
Jailbreak Repository for Hakmi Capital Group Projects

A flat APT repository for Sileo and compatible package managers, with the tooling to validate,
import, index, sign, build, publish and verify it. It distributes HCG's own jailbreak apps, tweaks
and command-line tools.

| | |
|---|---|
| APT source | **`https://repo.hakmicapitalgroup.com/repo/`**. The old `https://f1r3srp3nt.github.io/HCG-repo/repo/` redirects to it |
| Website | `https://repo.hakmicapitalgroup.com/` |
| HTTPS | **Not working yet:** GitHub has not issued a certificate for the domain, so the site is served over http. Fix: `docs/TRANSPORT.md` |
| Metadata signing | Implemented; **no archive key yet**, so `Release` is unsigned. Prerequisites: `docs/SIGNING.md` |
| Packages on the review branch | HCG Nexus 0.2.0 (rootless) and HCG Terminal Unleashed 0.1.0-1 (rootless): validated, **Experimental**, not yet published. See `docs/PUBLICATION_PLAN.md` |

## Status

- **Website:** the "HCG Futuristic" design, using the approved banner and square icon. It is generated from the real index.
- **Packages:** two genuine packages were imported through pinned requests (`imports/`) with recorded provenance and checksums. They become public only after merge and a manual **Publish repository**.
- **In development:** HCG Sentinel has no jailbreak package. It is listed under "In development" with no install action.
- **Device testing:** none yet. Use `docs/DEVICE_CHECKLIST.md`. Nothing is marked device-tested without recorded evidence.

## Quick start

Requirements: Python 3.11+ (standard library only), `dpkg-deb`, and `gpg`/`gpgv` (signing only).
Debian/Ubuntu: `apt-get install dpkg gnupg`. macOS: `brew install dpkg gnupg`.

```bash
bin/hcgrepo inspect path/to/package.deb             # review a package; does not import it
bin/hcgrepo import-deb path/to/package.deb \
    --built-from F1r3srp3nt/HCG-Terminal@<40-char sha>   # validate + add to pool/ + lock + provenance
bin/hcgrepo validate                                # whole pool, lock, provenance, dependencies
bin/hcgrepo build --out _site                       # complete static snapshot (default target: production)
bin/hcgrepo serve --dir _site                       # http://127.0.0.1:8000/
bin/hcgrepo verify --fetch-url http://127.0.0.1:8000/
bin/hcgrepo import-plan                             # pinned import requests in imports/ not yet imported
bin/hcgrepo keyring-deb --layout rootless --version 1.0   # once an archive key exists
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
.github/workflows/prepare-packages.yml  imports/builds pinned requests on review branches (never publishes)
.github/workflows/check-transport.yml   read-only DNS / certificate / redirect diagnostics
imports/                          pinned import requests (one .toml per package version and variant)
tools/recipes/                    build recipes that run a project's own documented packaging command
```

Generated output, which is never committed: `_site/` holds the website, and `_site/repo/` holds the APT source (`Release`, `Packages{,.gz,.bz2,.xz}`, optionally `Release.gpg` and `InRelease`, plus `debs/`, `depictions/<id>/{index.html,sileo.json}`, `assets/<id>/`, `CydiaIcon*.png`). The output also includes `build-info.json`.

## Publishing

1. Merge the review branch into `main`.
2. Run **Actions → Publish repository** with target `production` and type `PUBLISH`.

The workflow rebuilds and verifies the snapshot, deploys it, and checks every live URL. Before publishing packages, fix HTTPS (`docs/TRANSPORT.md`) and follow `docs/PUBLICATION_PLAN.md`.

## Documentation

- [docs/PUBLICATION_PLAN.md](docs/PUBLICATION_PLAN.md): exactly which package, version and variant is ready, and the steps to publish.
- [docs/TRANSPORT.md](docs/TRANSPORT.md): HTTPS diagnosis and the settings fix.
- [imports/README.md](imports/README.md): pinned import requests and the **Prepare packages** workflow.
- [docs/PUBLISHING.md](docs/PUBLISHING.md): importing, provenance, immutability, building, publishing, withdrawing.
- [docs/ROOTLESS_ROOTFUL.md](docs/ROOTLESS_ROOTFUL.md): architectures, paths, libroot, separate sources.
- [docs/SIGNING.md](docs/SIGNING.md): repository-metadata signing and key management. This is not iOS code signing.
- [docs/CUSTOM_DOMAIN.md](docs/CUSTOM_DOMAIN.md): migrating to `repo.hakmicapitalgroup.com`. Not done yet.
- [docs/TERMINAL_UNLEASHED.md](docs/TERMINAL_UNLEASHED.md): packaging review, and what is needed to publish it.
- [docs/DEVICE_CHECKLIST.md](docs/DEVICE_CHECKLIST.md): physical-device test procedure.
- [docs/VALIDATION.md](docs/VALIDATION.md): what the validator rejects, and the latest results.

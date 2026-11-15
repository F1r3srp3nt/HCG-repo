# Package publication plan (for review before the production deployment)

> **Withdrawn.** Both packages below were withdrawn under the HCG release policy (both products are `login-required`, and nothing login-gated may be published at a public URL) pending sign-in/access enforcement and launch approval. They are no longer in `pool/` or in any index; their
> `repo-lock.json` entries are kept with status `withdrawn`. The `.deb` files remain in the public git
> history; removing them would need a history rewrite, which has not been done. See
> [STORE_INTEGRATION.md](STORE_INTEGRATION.md). The rest of this page is kept as the record of the review.

Nothing below is published yet. The packages are in `pool/` on branch `claude/hcg-apt-sileo-repo-unxxqg`.
They reach `https://repo.hakmicapitalgroup.com/repo/` only after this branch is merged into `main` **and**
**Publish repository** is run by hand.

## Ready: validated, labelled Experimental

| Package | Version | Variant | Size | SHA-256 | Source |
|---|---|---|---|---|---|
| `com.hakmicapitalgroup.hcgnexus` (HCG Nexus) | 0.2.0 | rootless, `iphoneos-arm64` (arm64 + arm64e) | 238,108 B | `1987156996c2e76fb005bb66590aff76b77c474c4387ede6e015f19a081616a5` | Artifact `hcg-nexus-rootless-deb` of [HCG-Nexus run 43553119233](https://github.com/F1r3srp3nt/HCG-Nexus/actions/runs/43553119233), commit `907b1df3305c8960639a662db4d21172162fbea3`. The SHA-256 equals the one HCG-Nexus recorded for that artifact |
| `com.hakmicapitalgroup.hcgterminal.unleashed` (HCG Terminal Unleashed) | 0.1.0-1 | rootless, `iphoneos-arm64` (arm64) | 6,463,750 B | `7fdb735e74e551fa05a23a81714ff537f97556a67d600f49f090b543ef1d42bf` | Built by [HCG-repo run 43673491962](https://github.com/F1r3srp3nt/HCG-repo/actions/runs/43673491962) from HCG-Terminal commit `ddc51977c95f499a0568bee02aa1f0ba6b87ded7`, using that project's documented `Scripts/package-ios.sh unleashed … --deb rootless --ldid-entitlements HCGTerminal-Support/Unleashed-optional-entitlements.example.plist` |

Each package has `pool/iphoneos-arm64/<file>.provenance.json`, which records:
- the source and the request file;
- the commit and run/artifact IDs;
- the SHA-256 and SHA-512;
- an inspection summary: paths, binaries, architectures, entitlements, linked libraries and minimum iOS.

### What was checked (both packages)

The checks ran in GitHub Actions at import and again in this repository:

- **Package metadata:** the package identity, version and architecture match the pinned request, and the control file parses.
- **Rootless layout:** every installed path is under `/var/jb`, with no setuid/setgid and no world-writable files.
- **Binaries:**
  - every Mach-O has a code signature;
  - the architectures fit `iphoneos-arm64`;
  - nothing is FairPlay-encrypted;
  - no rootful library paths are linked.
- **Entitlements:** they equal the reviewed set (Terminal: only `com.apple.private.security.no-sandbox`; Nexus: none).
- **Dependencies:** every dependency is satisfiable (Nexus: `mobilesubstrate | ellekit`, `preferenceloader`; Terminal: `firmware (>= 17.0)`).
- **Index and downloads:** the index is built from the control files, and every `.deb` URL downloads with a matching size, SHA-256 and SHA-512, both locally over HTTP and in the production build.

### Known points: shown on the package pages, not hidden

**HCG Nexus**
- The package declares no `firmware` range. Its binaries are built for iOS 15.0+, so Sileo may offer it on any rootless iOS version.
- The intended target is iPhone 13 Pro, iOS 16.5, Dopamine, ElleKit.
- It ships disabled.
- It has not run on a device.

**HCG Terminal Unleashed**
- **It requires iOS 17.0+.** Sileo hides it on iOS 16.x, including the iPhone 13 Pro on iOS 16.5.
- `postinst` uses `uicache` without declaring `uikittools`.
- The embedded `libswiftCompatibilitySpan.dylib` keeps Apple's signature while the app is ldid-signed.
- The no-sandbox entitlement is unverified on a device.
- Delete a sideloaded Unleashed IPA first, because it has the same bundle ID.

Both are labelled **Experimental**. Neither is device-tested.

## Not prepared

| Project | Why |
|---|---|
| HCG Terminal Unleashed, rootful | Not requested. With the app's iOS 17 minimum, the only rootful setups are palera1n's rootful mode on the few older (A9–A11) devices that run iOS 17, mainly iPads, and no rootful build has been validated. To add one, create a second request: a copy of `imports/hcg-terminal-unleashed-0.1.0-1-rootless.toml` with `layout = "rootful"` and `architecture = "iphoneos-arm"`. It is kept as a separate variant |
| HCG Nexus, rootful | HCG-Nexus marks rootful "not supported until verified", and no Xcode-built rootful artifact exists |
| HCG Sentinel | No jailbreak package exists: it is a sandboxed app, and its privileged helper is only a specification. Listed under "In development" with no install action |

## Steps to publish (all manual)

1. **Review and merge** the branch. CI re-validates the pool and the lock history.
2. **Fix HTTPS first** (`docs/TRANSPORT.md`):
   - set the Pages source to **GitHub Actions**;
   - remove and re-add the custom domain;
   - tick **Enforce HTTPS**;
   - confirm **Check transport** passes.
3. Run **Publish repository** with target `production`. It rebuilds from the merged commit, deploys, and verifies every URL over HTTPS.
4. **Device test** (`docs/DEVICE_CHECKLIST.md`):
   - HCG Nexus on the iPhone 13 Pro / iOS 16.5 / Dopamine;
   - HCG Terminal Unleashed only on an iOS 17+ rootless device.
5. Record passing results under `[versions."<version>"]` in `packages/<id>/package.toml` before any version is labelled Device-tested.

To withdraw a version before or after publishing, run `bin/hcgrepo withdraw --package … --version … --arch … --reason …`. The lock keeps its hash, so that version can never be reused with different bytes.

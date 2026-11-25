# HCG Nexus Package Trust Center ↔ HCG Repo contracts (v1)

A **read-only** interface for HCG Nexus (and any HCG product: the contracts are interchangeable). Nexus reads public
files and runs the same checks; it never needs credentials and never changes HCG Repo.

| Contract | Format | Where |
|---|---|---|
| Repository identity, sources, signing status, trusted fingerprints | `hcg.repo.trust` v1 | `bin/hcgrepo trust-document`; `<repo>/trust.json` once `[trust] publish = true` (off; preview only) |
| Repository metadata | Debian `Release` / `InRelease` / `Release.gpg` / `Packages` | `<repo>/` and `<repo>/candidate/` |
| Package checksums | the `SHA256`/`SHA512`/`Size` of each `Packages` stanza | the index (authenticated only when signed) |
| Release Passports | `hcg.release-passport` v1 (final) | `bin/hcgrepo passport`, [schema](schemas/release-passport-v1.schema.json) |
| Compatibility results | `hcg.repo.compatibility` v1 | `bin/hcgrepo compat --public`; `<repo>/compatibility.json` once published (off) |
| Entitlement inspection | `contents.binaries[].entitlements` in the artifact verification, and `code_signing.entitlements` in passports | read from the package itself |
| Artifact verification | `hcg.repo.artifact-verification` v1 | `bin/hcgrepo verify-artifact <file.deb> [--channel candidate] [--json]` |
| Monitoring | `hcg.repo.monitor` v1 | [SENTINEL_MONITORING.md](SENTINEL_MONITORING.md) |

## Independent verification of a downloaded artifact

`verify-artifact` checks **one local file**, in this order:
1. fetch `Release`;
2. if it is signed, verify `Release.gpg` and `InRelease` against a trusted key and the configured fingerprint;
3. check that `Release` lists the served `Packages`;
4. find the file's own package, version and architecture in the index;
5. compare SHA-256 and size;
6. report the code signatures and entitlements inside the file.

Verdicts:
- `verified`: the file matches an index whose signature verified;
- `matches-unauthenticated-index`: the file matches, but the index is unsigned (true today), so the match is not
  cryptographic proof;
- `failed`: anything else.

Every result carries this scope note:

> This result concerns only the file that was checked. It does not show that a different file, such as one a package
> manager downloads or installs later, has the same bytes.

Nexus must show that note and must never present `matches-unauthenticated-index` as authenticated. Sileo and APT do
their own download and checksum check at install time; Zebra relies on HTTPS (see [SIGNING.md](SIGNING.md) §2).

Tested: `tests/test_signing_clients.py`, `TrustCenterTests`. Covered: signed index verified; wrong channel;
tampered `Packages`; unsigned index; a modified file with the same identity.

Live example (25 Nov 2026): HCG Mail Unleashed 0.1.0~beta1 from the pool against the live Candidate index →
`matches-unauthenticated-index`, SHA-256 `ac4b31b1…563099a`. Its only entitlement is its Keychain group.

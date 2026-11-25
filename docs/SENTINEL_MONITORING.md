# HCG Sentinel ↔ HCG Repo monitoring contract (v1)

**Read-only.** Sentinel observes HCG Repo and reports. It never publishes, withdraws, re-signs, commits or changes
settings. Any remediation is a separate action, approved separately by the owner.

## Source of truth

```
bin/hcgrepo monitor --target production --json      # exit 0 ok or warning, 1 alert (--strict: 1 on warning too)
```

It fetches only public URLs over HTTPS and reads the HCG-repo checkout: the lock, the committed public key and Git
history. It needs no credentials. Sentinel can run it on a schedule, or reimplement the same checks against the same
public files.

## Result format (`hcg.repo.monitor` v1)

```json
{ "format": "hcg.repo.monitor", "formatVersion": 1, "checked_at": "2026-11-25T16:00:00+00:00",
  "target": "https://repo.hakmicapitalgroup.com/", "status": "warning", "read_only": true,
  "checks": [ { "id": "signature.stable", "status": "warning", "detail": "unsigned (…)", "signed": false } ] }
```

`status` is the worst check status: `ok` < `unknown` < `warning` < `alert`.

| Check id | Watches | `alert` when |
|---|---|---|
| `availability.stable`, `availability.candidate` | repository availability | `Release` or `Packages` is not HTTP 200 |
| `freshness.<channel>` | metadata freshness | `Release` `Date` in the future, or `Valid-Until` passed (`warning` within 2 days). Without `Valid-Until`, it reports the age of the metadata (replay protection is off: [SIGNING.md](SIGNING.md) §6) |
| `index.<channel>` | index consistency | `Release` does not list the served `Packages`' SHA-256 |
| `checksums.<channel>` | unexpected checksum changes | a live package's SHA-256 differs from `repo-lock.json` (published bytes never change) |
| `unknown-packages.<channel>` | unauthorized publication | a live package has no lock record |
| `missing-packages.<channel>` | missing packages | (`warning`) a published lock record is not live; normal between a merge and the next publication |
| `signature.<channel>` | signature validity | signed but not verifiable against the committed key and configured fingerprint, or unsigned once a fingerprint is configured. Before production signing: `warning` |
| `signing-key` | key expiry and revocation | the key is expired or revoked, or its signing ability ends within 14 days (`warning` from 60 days); includes `days_until_expiry` |
| `publication-origin` | unauthorized publication attempts | the live `build-info.json` revision is not a commit of `main` |

Suggested cadence: every 30 minutes for availability; hourly for the rest. Alert routing goes to the owner. Sentinel
must not retry publications or "fix" anything.

## Today (25 Nov 2026)

Production result: overall `warning`. Every availability, freshness, index, checksum, unknown-package,
missing-package and origin check is `ok`. `signature.*` and `signing-key` are `warning` because production signing is not configured yet.

## What Sentinel must not receive

No private keys, no CI secrets, no submission store contents and no device identifiers. Everything the monitor reads
is public, apart from the local Git checkout.

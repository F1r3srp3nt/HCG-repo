# HTTPS and transport: current state and the fix

HTTPS (transport) and repository-metadata signing (`docs/SIGNING.md`) are separate problems:

- **HTTPS** protects the connection, so a browser shows a padlock instead of "Not Secure".
- **Signing** lets APT/Sileo check that `Release` came from HCG, even through a mirror or cache.

The website never shows a security badge for either. It states the signing status as it is, and this
document records the transport status.

## How it is checked

**Check transport** (`.github/workflows/check-transport.yml`, read-only) runs on GitHub's runners and records five things:
- GitHub Pages' own record of the domain and certificate;
- DNS;
- the certificate actually served on port 443;
- HTTP→HTTPS redirects and old-URL redirects;
- whether `https://repo.hakmicapitalgroup.com/repo/Release` can be fetched with certificate verification on.

It fails while HTTPS is not valid. Run it from **Actions → Check transport → Run workflow** after each settings change.

## Result on 15 Nov 2026 (run 43673491995)

| Check | Result |
|---|---|
| DNS | ✅ `repo.hakmicapitalgroup.com` → CNAME `f1r3srp3nt.github.io` → GitHub Pages addresses (185.199.108–111.153, 2606:50c0:8000–8003::153) |
| Content over plain http | ✅ `http://repo.hakmicapitalgroup.com/` and `/repo/Release` → 200 (the published snapshot) |
| Old source URL | ↪ `https://f1r3srp3nt.github.io/HCG-repo/repo/…` → 301 to **`http://`**`repo.hakmicapitalgroup.com/repo/…` (same path; existing sources keep working, but over http) |
| Certificate served on 443 | ❌ GitHub's shared `*.github.io` certificate (Let's Encrypt), which does not cover `repo.hakmicapitalgroup.com` |
| GitHub Pages record | ❌ custom domain `repo.hakmicapitalgroup.com`; **certificate state: none**; **HTTPS enforced: false** |
| GitHub Pages build type | ⚠️ **`legacy`** ("Deploy from a branch"). This repository publishes with GitHub Actions. With the branch source, a push to `main` may redeploy the raw branch over the APT site |

So browsers show "Not Secure" because GitHub has never issued a certificate for the custom domain. That
usually happens when the domain was saved before DNS pointed at GitHub. It is not a DNS or site-build problem.

## Fix (repository settings, by you)

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.** Choosing it saves immediately.
2. **Re-trigger certificate provisioning.** Under **Custom domain**, click **Remove**, then enter
   `repo.hakmicapitalgroup.com` again and **Save**. Wait for "DNS check successful".
3. Wait until **Enforce HTTPS** can be ticked. GitHub issues the Let's Encrypt certificate, usually within
   minutes and occasionally up to 24 hours. Then **tick Enforce HTTPS**.
4. Run **Actions → Check transport**. It should pass and show:
   - certificate state `approved`;
   - a served certificate for `repo.hakmicapitalgroup.com`;
   - `http://` → `https://` redirects;
   - old URLs redirecting to `https://repo.hakmicapitalgroup.com/…`.
5. Republish with **Publish repository** (target `production`). Its live check fails unless HTTPS verifies.

Optional hardening: verify `hakmicapitalgroup.com` under **your GitHub account → Settings → Pages →
Add a domain**, so no other GitHub account can claim the subdomain.

## Existing source URLs

| URL users may have added | Behaviour after the fix |
|---|---|
| `https://repo.hakmicapitalgroup.com/repo/` | Primary source; served directly over HTTPS |
| `https://f1r3srp3nt.github.io/HCG-repo/repo/` | 301 to the same path on the custom domain (GitHub Pages behaviour); keep it working by keeping the custom domain on this repository |
| `http://repo.hakmicapitalgroup.com/repo/` | 301 to https once **Enforce HTTPS** is on |

Users who added the github.io URL should switch to the custom-domain URL when convenient. The redirect
keeps working, but the custom-domain URL avoids the extra hop.

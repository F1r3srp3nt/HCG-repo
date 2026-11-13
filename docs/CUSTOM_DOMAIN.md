# Migrating to `https://repo.hakmicapitalgroup.com/repo/`

**Not done yet. Do not change DNS until you are ready to follow every step.** Until then the
publish workflow uses `target: pages` and the test source is `https://f1r3srp3nt.github.io/HCG-repo/repo/`.

## What changes

| | Before | After |
|---|---|---|
| Site base | `https://f1r3srp3nt.github.io/HCG-repo/` | `https://repo.hakmicapitalgroup.com/` |
| APT source | `https://f1r3srp3nt.github.io/HCG-repo/repo/` | `https://repo.hakmicapitalgroup.com/repo/` |
| Metadata URLs (depictions, icons) | under the project prefix | at the domain root |
| `Filename:` | `debs/…` | `debs/…` (relative, unchanged) |

GitHub Pages redirects project-site URLs to the custom domain once the domain is configured. **Do not rely on that redirect for APT clients.** Test it (step 6) and keep telling users the new URL.

## Steps

1. **Rehearse locally:** `bin/hcgrepo smoke --target production` builds with the production base, serves it at `/`, and verifies it. CI already does this on every push.
2. **DNS (manual, at your DNS provider):** create one record:
   ```
   repo.hakmicapitalgroup.com.  CNAME  f1r3srp3nt.github.io.
   ```
   - Use a hostname-only CNAME **target**: `f1r3srp3nt.github.io`, with no path and no `/HCG-repo`. A CNAME cannot contain a path, and Pages maps the domain to this repository through the repository setting in step 3.
   - Do not add a wildcard record.
   - Do not point A/AAAA records for this subdomain anywhere else.
   - Optional but recommended: verify `hakmicapitalgroup.com` under your GitHub account (**Settings → Pages → Add a domain**). This prevents domain takeover if the repository is ever unpublished.
3. **GitHub:** in **Repository Settings → Pages → Custom domain**, enter `repo.hakmicapitalgroup.com` and save. With Actions-based deployment, the setting is what counts, so no `CNAME` file is needed in the artifact. Wait for the DNS check to pass.
4. **HTTPS:** wait until GitHub has issued the certificate, then tick **Enforce HTTPS**. Check it:
   ```bash
   dig +short repo.hakmicapitalgroup.com CNAME     # → f1r3srp3nt.github.io.
   curl -sSI https://repo.hakmicapitalgroup.com/ | head -1
   curl -sSI http://repo.hakmicapitalgroup.com/ | grep -i '^location: https://'
   ```
   Do not switch the source URL until HTTPS works without certificate errors.
5. **Switch the metadata:** set `default_target = "production"` in `config/repo.toml` and merge. Then run **Publish repository** with `target: production`. The workflow's live check verifies `https://repo.hakmicapitalgroup.com/repo/` end to end.
6. **Test existing client URLs.** Users who added the old source still request `https://f1r3srp3nt.github.io/HCG-repo/repo/…`:
   ```bash
   for p in Release Packages.xz Packages.gz Packages CydiaIcon.png; do
     curl -sS -o /dev/null -w "%{http_code} %{redirect_url}  $p\n" "https://f1r3srp3nt.github.io/HCG-repo/repo/$p"
   done
   bin/hcgrepo verify --base-url https://repo.hakmicapitalgroup.com/ --list-urls
   ```
   Expect a `301` to the same path on the custom domain.
   - On a device that already has the old source, refresh in Sileo. Note whether it refreshes successfully and whether packages download.
   - Then add the new URL, and remove the old one.
   - Record the result in `docs/DEVICE_CHECKLIST.md` evidence.
   - If an old-URL client fails, tell users to replace the source. Do not revert DNS while users are mid-migration.
7. **Announce:** the website and README show the production URL once the build uses it. Keep the old URL documented as deprecated.

## Rollback

Remove the custom domain in **Settings → Pages**, set `default_target = "pages"`, and publish with `target: pages`. Leave the DNS record in place, or delete it **after** removing the domain from GitHub; never the other way round, which would leave a dangling record.

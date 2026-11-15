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

## Result on 15 Nov 2026, 04:21 UTC (run 43676397361), after "HTTPS enabled" was reported

| Check | Result |
|---|---|
| Authoritative DNS | Nameservers `ns1/ns2/ns3.dnsdomene.net` (Domene). All three answer `repo.hakmicapitalgroup.com` → **CNAME `f1r3srp3nt.github.io`** |
| Public resolvers | 1.1.1.1, 8.8.8.8, 9.9.9.9 and 208.67.222.222 all agree: GitHub Pages 185.199.108–111.153 |
| Each address | `http://…/repo/Release` → 200, `Server: GitHub.com`, **no `cf-ray` (no Cloudflare)** |
| GitHub Pages record | build type `workflow` (correct now); custom domain set; **certificate state: none; HTTPS enforced: false** |
| Certificate on 443 | GitHub's shared `*.github.io`, which does not cover the domain, so `https://…/repo/Release`, `Packages*` and the icons all fail verification |

### Conclusion

- **Server side:** GitHub has not issued a certificate, so HTTPS cannot work yet. Whatever was switched on, it was not GitHub Pages' **Enforce HTTPS** (GitHub reports it off) and not a certificate at GitHub. This alone makes Sileo report "The certificate for this server is invalid".
- **Device side:** **522 is Cloudflare's "origin timed out" status**, and Cloudflare is not in this domain's DNS or serving path. The phone is therefore not reaching GitHub the way every resolver above does. The likely causes are:
  - a stale DNS answer for the old Domene web host (the earlier `php.ini` page);
  - a VPN, DNS profile or content filter (for example Cloudflare WARP or a 1.1.1.1 profile);
  - a proxy configured in Wi-Fi settings.
- **So it is both:** the certificate issue is real for everyone, and the 522 is specific to how the phone reaches the domain.

## Fix (by you)

**At GitHub (certificate):**
1. Open **Settings → Pages**. Under **Custom domain**, click **Remove**, then enter `repo.hakmicapitalgroup.com` and **Save**. Wait for "DNS check successful". This makes GitHub request a new Let's Encrypt certificate.
2. Wait until **Enforce HTTPS** can be ticked, usually within minutes and sometimes up to 24 hours, then **tick it**.
3. If the checkbox is still unavailable after 24 hours, check CAA records at Domene. They must allow `letsencrypt.org`, or contain none. **Check transport** prints them.

**At Domene (DNS):** keep exactly one record for `repo`: **CNAME → `f1r3srp3nt.github.io`**. Do **not** add A/AAAA records or a web-hotel subdomain for `repo`, and do **not** put a proxy/CDN (e.g. Cloudflare's orange cloud) in front of it while GitHub issues the certificate.

**On the phone (522):**
1. Turn off any VPN, DNS profile (Settings → General → VPN & Device Management) or Wi-Fi HTTP proxy.
2. Toggle Airplane mode on and off, or restart, to flush cached DNS.
3. Open `http://repo.hakmicapitalgroup.com/` in Safari. You should see the HCG Repo page. If you see a `php.ini` listing or a Cloudflare error page instead, the phone is still resolving the old host.
4. Once **Check transport** passes, open `https://repo.hakmicapitalgroup.com/repo/Release` in Safari. It must show plain text starting `Origin: HCG Repo` with no certificate warning.

## After the fix: refresh the source in Sileo

1. Sileo → **Sources** → swipe left on the old source → **Delete**. This clears Sileo's cached failure, "Untitled Repo" and the generic icon.
2. **Sources → + →** `https://repo.hakmicapitalgroup.com/repo/` → **Add**, or use **Add to Sileo** on the website.
3. Pull down to refresh. Expected:
   - the source is named **HCG Repo** with the square HCG icon;
   - opening the source shows the HR banner at the top (from `sileo-featured.json`);
   - the Sources-list row itself shows only the icon and name, which is all Sileo supports there.
4. Expected package count: **0** while protected delivery is not live. Nexus is paid and Terminal Unleashed needs HCG Connect sign-in, so neither is in the public index yet (`ACCESS.md`). "Package not found" for Nexus is therefore expected until then. It does not mean refresh failed.

## Existing source URLs

| URL users may have added | Behaviour after the fix |
|---|---|
| `https://repo.hakmicapitalgroup.com/repo/` | Primary source; served directly over HTTPS |
| `https://f1r3srp3nt.github.io/HCG-repo/repo/` | 301 to the same path on the custom domain (GitHub Pages behaviour); keep it working by keeping the custom domain on this repository |
| `http://repo.hakmicapitalgroup.com/repo/` | 301 to https once **Enforce HTTPS** is on |

Users who added the github.io URL should switch to the custom-domain URL when convenient. The redirect
keeps working, but the custom-domain URL avoids the extra hop.

## Source icon in Sileo's Sources list

**What is deployed.** The approved square HCG Repo icon is served relative to the source URL as:
- `https://repo.hakmicapitalgroup.com/repo/CydiaIcon.png` (60×60)
- `https://repo.hakmicapitalgroup.com/repo/CydiaIcon@2x.png` (120×120)
- `https://repo.hakmicapitalgroup.com/repo/CydiaIcon@3x.png` (180×180)

These are PNG files with the exact capitalisation shown. They are copied unchanged from `branding/` into `repo/` by every build. The build's verifier and **Check transport** both fail if any variant:
- is missing or redirects;
- is not `image/png`;
- is not square at its scale;
- is an HTML page;
- differs from the approved file.

The website favicon and the HCG Store payment-provider icon are separate files and are not used for the source.

**How Sileo loads it** (checked in Sileo's source, `RepoManager._checkUpdatesInBackground` and `SourcesTableViewCell`):
- Sileo tries `CydiaIcon@<screen scale>x.png`, then the lower scales, then `CydiaIcon.png`, next to the source URL. It stops at the first download that returns data.
- It does this **once per app launch per source**. If that one attempt fails, for example while the certificate was invalid, the Sources list shows the generic icon for the rest of that launch, even after refreshing.
- A successfully downloaded icon is cached and refreshed on later launches. The name and the banner are fetched separately, which is why they can be correct while the icon is still generic.

**Refresh steps, least disruptive first:**
1. Force-quit Sileo (swipe it away in the app switcher) and open it again. Then pull down on **Sources** to refresh.
2. If the icon is still generic, check from Safari on the phone that `https://repo.hakmicapitalgroup.com/repo/CydiaIcon@3x.png` shows the HCG Repo icon with no certificate warning. If Safari fails, the phone still has a network or DNS problem; see the 522 notes above.
3. Only if both of those work and the icon is still generic after another relaunch: remove the source and add `https://repo.hakmicapitalgroup.com/repo/` again. No packages are installed from this source, so nothing else is affected.

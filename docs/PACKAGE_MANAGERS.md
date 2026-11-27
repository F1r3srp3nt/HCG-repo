# Package manager compatibility matrix

What each package manager actually implements for HCG Repo: free installs, paid packages, account sign-in, signature
and checksum checks. Every row is read from the upstream source at an exact revision (below). Nothing here is a
device result: physical qualification is separate (`docs/DEVICE_QUALIFICATION_COMMERCIAL.md`), and the TEST
fixtures (`tests/commercial_env.py`) send exactly the requests these sources send. Review this file again when a
client ships a new release.

**There is no universal "commercial repository standard".** Sileo defined a *payment provider* API that Zebra and
Saily/Irisin later implemented. Each implementation differs in small ways (listed below). Cydia used saurik's own
closed system; Installer 5 uses its own; APT has none. HCG Store implements the payment provider API
(`/repo-api/v1/sileo/`, `web-hcg-store/server/src/sileo.ts`); HCG Repo only advertises it (`repo/payment_endpoint`).

## Sources reviewed

| Client | Repository and revision | Version | Notes |
|---|---|---|---|
| Sileo | `Sileo/Sileo` `c9f70158a037dc76b91c450e72af2de0514dd3ee` (2024-10-08) | tag 2.5.1 | Deployment target iOS 11.0–12.1 by target; one package for rootful and rootless |
| Zebra | `zbrateam/Zebra` `e2f3c2725db8f98989ffd892c5913a58fb34fa70` (2026-11-15) | MARKETING_VERSION 1.1.36 | Deployment targets 9.0 and 15.0 by target |
| Saily → Irisin | `Lakr233/Irisin` (= `SailyTeam/Saily` HEAD) `39242846e35fb60740a6fb3a3940dd24bb9769c3` (2026-11-11) | tag v5.1.8 | Rewritten and renamed Irisin; iOS 15+, rootless and roothide only |
| Saily (legacy) | `SailyTeam/Saily` legacy tree `8c2ea30070f24c1a244d86914bb311ea5f634e67` (2025-07-26) | RL-1753762792 | Pre-Irisin "Chromatic" code; only used to confirm the payment behaviour did not regress |
| Cydia | `sbingner/cydia` mirror `bea5ee0aadb0ac95f71eb858fdb7f02e748f50d7` (2018-07-11) | 1.1.32-era | git.saurik.com is not reachable from here; the mirror is the newest public source we could read |
| Installer 5 | Unofficial source snapshots `ctabuyo/Installer5`, `ctabuyo/Installer5-UI`, `ctabuyo/AppTapp-Installer5`, `jbeezo/AppTapp` | — | Installer 5 itself is closed source. Snapshots are old and may not match the shipping app |
| APT | `Debian/apt` mirror `6b128124271e94bdb0f4e7850d9286170d712b04` (2025-11-10) | 3.1.12 | Procursus/ElleKit builds patch APT; the patches do not add a payment protocol |

## Matrix

| | Sileo | Zebra | Saily / Irisin | Cydia | Installer 5 | APT |
|---|---|---|---|---|---|---|
| Rootless (`/var/jb`) | yes | yes | yes (and roothide) | no | no | yes (Procursus) |
| Rootful | yes | yes | no | yes | yes | yes |
| Minimum iOS (source) | 11/12 targets; jailbreak decides | 9 / 15 targets; jailbreak decides | 15 | old | old | n/a |
| Free HCG packages | yes | yes | yes | likely (not tested) | likely (not tested) | yes |
| Paid packages (payment provider API) | **yes** (defined it) | **yes** (`ZBPaymentVendor.m`) | **yes** | no (saurik's system) | no (snapshot skips `cydia::commercial`) | no |
| Discovery | `<repo>/payment_endpoint`, trimmed, https required | same, trimmed; only for **https** sources | same, **not trimmed** (`URL(string:)`) | — | — | — |
| Calls provider `info` | yes | yes | **no** | — | — | — |
| Sign-in | `ASWebAuthenticationSession` → `sileo://authentication_success?token=&payment_secret=` | same callback scheme `sileo` | same; accepts **any** `sileo://` URL carrying both params | saurik web UI | own apptapp.me login | — |
| Token storage | keychain | keychain | keychain | — | — | — |
| Sends device identifiers | UDID + model in body | UDID + model | UDID + model; `X-Machine`, `X-Unique-ID` (real UDID), `X-Firmware` headers | UDID to bridged hosts | — | none |
| Signed-out requests | no token | token `"none"` | no token | — | — | — |
| `payment_secret` used for | `purchase` only (biometric gate) | `purchase` | `purchase` **and** `authorize_download` | — | — | — |
| Download of a paid package | `authorize_download` (version, repo, architecture) → https URL | same, only for packages whose `Filename` is relative/hostless and `requiresAuthorization` | same | — | — | auth.conf Basic auth over https only |
| Repository signature (Release.gpg) | **verified** with `apt-key verify` against `<prefix>/etc/apt/trusted.gpg.d/` (needs the HCG Repo Keyring) | **not verified** | **not verified** | `AllowInsecureRepositories=true` | not checked in snapshots | **strict** |
| Package checksum vs index | yes | **none found** | yes (deb hash) | via APT | — | yes |
| Install step | `apt-get --allow-unauthenticated` on the fetched deb | `apt` on local debs with `Dir::State::lists=/var/empty` | own installer daemon | APT | own | APT |
| `Valid-Until` (replay) | not handled | not handled | not handled | APT default | — | enforced |
| HCG status | **Recommended** (protocol-tested; device qualification pending) | **Evaluated** at protocol level only (test 27); not qualified | Protocol-tested (rootless/roothide) | Legacy; not for paid packages | Free only, not tested | Free only |

Key file paths: Sileo `Sileo/Backend/Payment Manager/` (`PaymentManager.swift`, `PaymentAuthenticator.swift`,
`PaymentProvider.swift`, `PaymentPackageInfo.swift`), `Sileo/Backend/APT Wrapper/APTWrapper.swift` (`verifySignature`); Zebra `Zebra/Tabs/Sources/Helpers/ZBPaymentVendor.m`,
`Zebra/Downloads/ZBDownloadManager.m`, `Zebra/Tabs/Sources/Views/ZBSourcesAccountBanner.m`, `ZBSource.m`;
Irisin `Packages/AptRepository/Sources/AptRepository/Repository/RepositoryCenter+Internal.swift` and `RepositoryCenter+Downloader.swift`.

## The payment provider API (as the clients send it)

All requests are `POST` with a JSON body to `<payment_endpoint><path>` except `authenticate` and `info`.
Every body carries `token` (when signed in), `udid` and `device`. Errors are `{"success": false, "error": "...",
"invalidate": true|false}`; `invalidate: true` makes the client forget its token.

| Path | Purpose | HCG Store behaviour (`sileo.ts`) |
|---|---|---|
| `GET info` | provider name, icon, description, `authentication_banner` | public, no account data |
| `GET authenticate?udid=&model=` (web auth session) | sign-in | redirects to HCG Connect (PKCE S256) through the Store; on success `sileo://authentication_success?token=&payment_secret=`. The UDID is ignored |
| `user_info` | name, email, owned package IDs (`items`) | from HCG's entitlement authority at request time |
| `package/<id>/info` | `price`, `purchased`, `available` | price only from an approved Store offer; `purchased` only from the authority |
| `purchase` | buy | returns status `1` with a URL to the HCG Store checkout. **Never grants**; the entitlement is created only by HCG Payments' verified webhook |
| `authorize_download` (`version`, `repo`, `architecture`) | the file URL for one exact build | entitlement re-checked server-side; a signed URL to the exact approved artifact, 120 s by default |
| `sign_out` | forget the token | revokes the Store's repo token |

Facts that matter for HCG:

1. **No client implements the same thing.** Irisin does not call `info` and does not trim `payment_endpoint`, so
   HCG Repo writes `repo/payment_endpoint` **without a trailing newline** (`tools/hcgrepo/site.py`). Zebra uses the
   API only on HTTPS sources: the TEST environment serves HCG Repo over HTTPS for that reason.
2. **UDIDs are sent by every client.** They are never a credential. HCG Store ignores them and does not store them.
3. **Irisin accepts any `sileo://` callback carrying both parameters**, so another app on the device that can open
   a `sileo://` URL could inject a token. Mitigation on the server: tokens are random, bound to the HCG account,
   revocable, and useless for anything but the payment provider API.
4. **A signature never authorizes and an entitlement never verifies.** Sileo verifies repository signatures only
   after the HCG Repo Keyring is installed; Zebra and Irisin do not verify them at all. Repository signing therefore
   does not protect every installation equally (`docs/SIGNING.md`).
5. **Zebra checks no package checksum** that we could find, so for Zebra the protected download relies on HTTPS
   and the short-lived signed URL.

## What HCG Repo does not do

- It does not modify any third-party package manager.
- It does not invent endpoints. Anything not in the table above (a "library" or "restore" call, for example) does
  not exist in these clients: restoration happens because `user_info` and `package/<id>/info` report ownership
  from the account, and the Profile page on the website (`docs/PROFILE.md`) is the account view.
- It does not support paid packages in Cydia, Installer 5 or APT. Where a legacy client cannot run the current flow
  securely, the customer uses the browser: HCG Store and the HCG Repo Profile.

# Physical qualification: commercial delivery in Sileo and Zebra

The procedure for the owner's **iPhone 11 Pro Max** and **iPhone 13 Pro**. Protocol tests (`docs/COMMERCIAL_TEST.md`)
are not device results; a result counts only when this procedure has been run on the phone and recorded in
`docs/device-tests/` with evidence. Nothing here has been run yet.

Known configuration on record: iPhone 11 Pro Max, iOS 17.4, Dopamine 2.x (rootless), Zebra 1.1.x and Sileo
(`docs/device-tests/`). The iPhone 13 Pro's iOS and jailbreak are not on record: write them down in step 0.

## Prerequisites

| Phase | Needs | Status |
|---|---|---|
| A — free and account, today | Nothing new | Ready |
| B — paid flow with TEST products | A **staging** HCG Store reachable from the phone over public HTTPS with a trusted certificate (iOS will not use a self-signed one for `ASWebAuthenticationSession`), in `HCG_STORE_MODE=test` with the HCG Connect *staging* client (or the Store mock, labelled TEST), test payments, TEST products; and a **staging** HCG Repo build whose `payment_endpoint` points at it (for example a separate GitHub Pages site or branch preview, never `repo.hakmicapitalgroup.com`) | Not available; founder decision on where staging runs |
| C — production soft launch | All prerequisites of `docs/COMMERCIAL_DISTRIBUTION.md` §12 | Not approved |

Never test Phase B against the production Store or the production HCG Repo source.

## Step 0 — record (each phone, each package manager)

| Field | iPhone 11 Pro Max | iPhone 13 Pro |
|---|---|---|
| Date, tester | | |
| iOS version (Settings → General → About) | | |
| Jailbreak + version, rootless/rootful | | |
| `dpkg --print-architecture` | | |
| Sileo version (Settings → About) | | |
| Zebra version (Settings) | | |
| Source URL(s) added | | |

## Phase A — free packages, trust and account surfaces (run now)

1. **Sources.** Add `https://repo.hakmicapitalgroup.com/repo/` (and `/repo/candidate/` if testing betas) in Sileo and
   in Zebra. Refresh: no errors, HCG icon.
2. **No payment provider.** HCG Repo advertises none today: Sileo Settings shows no HCG payment provider; Zebra shows
   no sign-in banner for the source. Screenshot both.
3. **Free install and removal.** Install one free Candidate package (record version + SHA-256 from the website),
   launch, remove. Same in Zebra.
4. **Signing.** Sileo: with the HCG Repo Keyring not installed, refresh works (index unsigned). Do not install a
   keyring until production signing exists (`docs/SIGNING.md`). Zebra: note that it does not check signatures.
5. **Website.** Open `/account/` in Safari: it must say the library is managed by HCG Store (profile endpoint not
   deployed) and never ask for a password. Open `/install/` and `/trust/`.

Pass when every step matches and the screenshots are saved.

## Phase B — paid flow with TEST products (needs the staging environment)

Use a TEST HCG account. Record each request outcome from the staging Store log (never copy tokens into notes).

| # | Step | Expected | Sileo | Zebra |
|---|---|---|---|---|
| B1 | Add the staging source, refresh | Source shows the payment provider (Sileo: Settings → payment providers; Zebra: sign-in banner) | | |
| B2 | Open a TEST paid package signed out | Price "4.99 EUR TEST", button asks to sign in; no download possible | | |
| B3 | Sign in | System browser sheet opens HCG Connect (staging); no password field inside the package manager; returns to the app signed in | | |
| B4 | Signed in, not purchased | Shows price, install not possible | | |
| B5 | Purchase | Opens HCG Store checkout in the browser; TEST payment; app shows "Owned" only after the Store records the webhook | | |
| B6 | Install | Downloads and installs; SHA-256 of `/var/jb/var/cache/apt/archives/…deb` (or the client's cache) equals the staging index | | |
| B7 | Same account in the other package manager | Owned without paying again (restoration) | | |
| B8 | Second TEST product not bought | Not installable | | |
| B9 | Refund the purchase in staging (Store admin / test webhook) | Package shows not owned; reinstall refused; the installed app stays (no client removes it) | | |
| B10 | Sign out in the package manager | Token revoked in the Store (connected package managers list shrinks on `/account/`) | | |
| B11 | Airplane mode during install, then retry | Retry succeeds within the link lifetime or re-authorizes; never a partial install | | |
| B12 | Free Candidate and stable packages | Still install as in Phase A | | |
| B13 | Profile page on the phone (Safari, staging site) | Shows the TEST account, library, purchase; no tokens or device IDs | | |

## Recording

Create `docs/device-tests/<date>-commercial-<phone>-<client>.md` with step 0, the tables above, screenshots or a
screen recording, and the staging Store revision. Only then may `docs/PACKAGE_MANAGERS.md` change a client's status
from "protocol-tested" to "device-qualified" for that phone, iOS and jailbreak.

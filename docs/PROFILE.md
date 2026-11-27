# HCG Repo Profile ("My Library")

`https://repo.hakmicapitalgroup.com/account/` — the signed-in customer's HCG Repo view of their HCG account. Built by
`tools/hcgrepo/pages.py` (`account_body`) with `tools/hcgrepo/assets/account.js`.

**Display only.** It grants nothing, stores nothing, never sees a password or token, and is not an entitlement
cache. Every download is still decided by HCG Store at the moment a package manager asks.

## Data source

`GET <store>/api/v1/repo/profile` with `credentials: "include"` (the HCG Store session cookie). Proposed in
`docs/store-integration/0001-repo-profile-and-ranges.patch`; **not deployed**. CORS: credentialed, only for origins
in `HCG_STORE_REPO_ORIGINS`; any other origin gets no CORS headers (test 21).

```json
{
  "signedIn": true,
  "user": {"name": "…", "email": "…"},
  "library": [{"productId": "…", "editionId": "…", "name": "…", "editionName": "…", "hcgRepoKey": "…",
               "package": "com.hakmicapitalgroup.…", "acquisition": "licensed", "licence": "…", "state": "downloads-available",
               "currentRelease": {"version": "…"}}],
  "purchases": [{"createdAt": 0, "title": "…", "amount": 499, "currency": "EUR", "status": "paid", "testOnly": true}],
  "connectedPackageManagers": [{"kind": "sileo", "label": "Sileo (HCG Repo)", "connectedAt": 0}],
  "devicesNote": "…", "ownershipUnavailable": false, "downloadsMessage": "…",
  "sign_in_url": "<store>/auth/login?return_to=%2Faccount", "store_url": "<store>"
}
```

Signed out: `{"signedIn": false, "sign_in_url", "store_url"}`. Authority or HCG Connect unavailable: **503**
`{"signedIn": null, "unavailable": true, …}`. The response never contains a token, payment secret, download URL or
device identifier.

The library is the Store's own `library()` (the same data as the Store account page), filtered to products mapped to
HCG Repo, so the two can never disagree.

## Sections and states

| Section | Source | Notes |
|---|---|---|
| Account | `user` | Sign-out and account management link to HCG Store |
| Packages in your library | `library` + this site's own `repo/Packages` | "Version in HCG Repo" from the index; "Open in Sileo" only when downloads are available |
| Available updates | — | Explains that only the package manager knows the installed version; nothing is claimed |
| Purchase history | `purchases` | TEST purchases labelled |
| Connected package managers | `connectedPackageManagers` | Active payment-provider tokens; revocable in HCG Store |
| Restore purchases | static | Add the source, sign in with the same HCG account |
| Betas / Package trust | static | Candidate source URL; link to the Package Trust Center |

| Situation | Page shows |
|---|---|
| Endpoint not deployed (404) or network/CORS failure | "Your library is managed by HCG Store", link to the Store account |
| 503 | "Could not be checked right now. Nothing has changed" |
| Signed out | "Sign in with HCG Connect" (link to the Store's `/auth/login`) |
| Signed in | the sections above |

Production today: the endpoint does not exist on the deployed Store, so the page shows the first state.

## Native package-manager account pages

Sileo (Settings → payment provider → `PaymentProfileViewController`) and Zebra (`ZBSourcesAccountBanner` on the
source page) show the provider's sign-in and account information, read from `user_info` of the same Store payment
provider. HCG Repo does not modify those apps. We did not review an account page in Saily/Irisin. Cydia, Installer 5
and APT have no account page for HCG; customers use this website.

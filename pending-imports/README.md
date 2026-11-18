# Pending import requests (not imported)

Import requests here are complete and pinned, but **held until the owner explicitly approves the versions**.
Files in this folder do not trigger *Prepare packages* (it only watches `imports/**`).

To import after approval: `git mv pending-imports/<file>.toml imports/` and push; *Prepare packages* then imports
the pinned bytes into this branch. Publication is a separate step (merge, then *Publish repository*).

| Request | Package | Pairs with |
|---|---|---|
| `hcg-halo-0.3.1-beta1-rootless.toml` | HCG Halo 0.3.1~beta1 | HCG Nexus 0.3.2~beta1 |
| `hcg-nexus-0.3.2-beta1-rootless.toml` (added when its CI run is final) | HCG Nexus 0.3.2~beta1 | HCG Halo 0.3.1~beta1 |

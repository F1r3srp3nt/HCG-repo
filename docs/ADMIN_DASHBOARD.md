# HCG Repo administration dashboard

```
bin/hcgrepo admin [--port 8765] [--incoming <private store>]
```

This opens a local page (the printed URL carries a session token) over the **same engine** as the command line. It
has no backend, database or rules of its own. Each view or button calls the function the CLI calls:

| View | Shows | Engine |
|---|---|---|
| Incoming | submissions in the private store, state, provider, SHA-256, errors/warnings | `release incoming` |
| Pending approvals | validated submissions; review with the six levels, findings, requirements; record an approval of the exact SHA-256 | `release show` / `status` / `approve` |
| Candidate preparation | approved submissions; prepare via `pool` or `local-file`, optional free-demo approval, optional metadata from the draft; superseded Candidate versions | `release prepare` |
| Published packages | every lock record: channel, status, SHA-256, size | `repo-lock.json` |
| Compatibility evidence | status and six levels per version | `compat` |
| Known failures | failing and partial device tests with evidence | `compat` |
| Release history | imports, protections, withdrawals and Release Manager events | lock + reviews |
| Artifact provenance | the release passport of any release | `passport` |
| Withdrawals | withdraw a version (typed confirmation); withdraw a submission | `withdraw`, `release withdraw` |

- **Never publishes.** Approvals, preparations and withdrawals change local files to review and commit, as with the
  CLI. Publication stays the manual Publish repository workflow, run with the owner's approval.
- **Local only.** It listens on 127.0.0.1. Every API call needs the session token, POST requests must come from the
  dashboard's own origin, and the Host header must be the loopback address, so other web pages cannot drive it. It
  reads no credentials.

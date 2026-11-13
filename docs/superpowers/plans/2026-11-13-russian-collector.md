# Russian WSDL collector implementation plan

**Goal:** Collect both SFR contours in Russian container 102 and publish immutable inputs to the existing reference repository under the owner's account.

**Architecture:** A Python 3.9 standard-library collector downloads approved WSDL dependencies and uploads content-addressed objects plus manifests through the GitHub Git Data API. GitHub replays each manifest offline before using it. A dedicated fine-grained PAT has Contents write permission only on macros0/sedo-reference; no separate feed repository or signature infrastructure.

**Constraints:** Preserve all archived editions and exact source bytes. No existing account token copied to the server. Do not change existing container applications. No runtime Git or new Python dependencies.

## Tasks

- [ ] Add failing tests for manifest replay, tampering, contour isolation and unchanged-content deduplication.
- [ ] Implement snapshot validation and loading from state/collector, preferring validated local inputs to live WSDL.
- [ ] Add failing Git API publisher tests for allowed paths, atomic commit, no-op and concurrent branch updates; implement publisher and collector CLI.
- [ ] Connect Pages to pending snapshots before build; keep historical snapshot records. Add systemd configuration and operator documentation.
- [ ] Run focused and full checks, regenerate production trees, publish code and initial inputs.
- [ ] Install isolated service in CT102, transfer newly created token without logging it, collect both contours and enable the daily timer.
- [ ] Verify deployed Pages, missing-type coverage, remote timer and idempotent second collection.

## Review focus

Reject changed raw bytes or metadata, cross-contour URLs and incomplete closures. Never force-push or overwrite concurrent changes. Do not log token headers or remote error bodies. Keep old manifests and editions on remote outages. A PAT authenticates its holder, not a particular physical server; revoke it if the server is compromised.

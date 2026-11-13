# SourceCraft daily collection implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. No subagents in this side conversation.

**Goal:** Daily Russian-network WSDL/XSD capture with immutable SourceCraft storage and verified GitHub imports.

**Architecture:** SourceCraft runs at 04:00 UTC (07:00 Moscow), collecting prod and test sequentially. A separate `snapshots` branch stores SHA-256 objects, immutable manifests and an index with current pointers. GitHub's existing 04:17 UTC job reads the public branch and verifies new content offline before updating the reference. No GitHub token goes to SourceCraft.

**Tech Stack:** Existing Python 3.9+ standard-library collector, Git, SourceCraft CI, GitHub Actions.

**Spec:** User request and successful one-shot probe in this conversation; same two-repository pull architecture previously proposed and accepted.

## Constraints and review focus

- At least 5 seconds after each completed SFR request; 30 second timeout; one attempt; stop further requests after the first error, including other seeds/contours. HTTPS verification stays enabled.
- Failed/incomplete collections never replace current pointers. All historical manifests/objects are append-only. Returning to old content updates the current pointer without overwriting its original manifest.
- Only branch `snapshots` is published by CI; its push cannot start another collection. Concurrent publication rejects rather than force-pushing.
- GitHub imports only hash-verified data, downloads missing objects, validates every new manifest and publishes no partial input batch on corruption.
- Both services need a successful end-to-end run before switching off the old home timer to avoid duplicate daily SFR scans.

## Tasks

- [ ] Add/test feed index merge, immutable storage and SHA-256 import; support explicit current pointers in `collector_snapshots.load_library`.
- [ ] SourceCraft: add conservative daily collection, data-branch publisher and cron; test first-error stop and unchanged snapshot no-op; run both contours once and verify public feed.
- [ ] GitHub: import feed before existing catalog check, document schedule/recovery/history; run regression suites, publish, dispatch and verify end-to-end update/Pages; switch off home timer after verification.

## Decisions

Separate clone under Documents/Codex protects the parent chat's working copy and Git state. Existing catalog/DOCX processing and historical edition rules are retained. Failed SourceCraft runs leave the previous successful feed readable. Current-pointer metadata solves reappearance of a previously seen content_id.

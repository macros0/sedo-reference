# Automation status implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline; no subagents in this side conversation.

**Goal:** Show actual SourceCraft contour check times, new catalog revisions and confirmed GitHub publication in the reference.

**Architecture:** SourceCraft publishes a separate per-run heartbeat to `status`, including unchanged/failed runs. A small GitHub workflow, independent of Pages, aggregates this heartbeat and actual Actions results/artifacts into `automation-status/status.json`. The browser reads the public JSON without tokens, so failures remain visible even when a new site cannot deploy. Existing immutable schema storage and request pacing are unchanged.

**Tech Stack:** Python standard library, existing Git/gh CLIs, SourceCraft CI, GitHub Actions, plain browser JavaScript/CSS.

## Constraints and verification

- Distinguish real check timestamps from first-seen schema dates and changed XSD snapshots from new message revisions.
- Do not label an update published because an older Pages run succeeded. Match the expected commit, or prove a legacy run contains the update's checked_at.
- Unknown, failed, pending, cancelled, stale (>36 hours) and unavailable states never appear as success.
- No browser credentials, personal XML, extra SFR requests or recursive Pages deployment from status writes.
- Retain last successful SourceCraft result after a failed run; publish failure heartbeat from an always-run CI step, including timeout fallback.

## Tasks

- [ ] SourceCraft heartbeat/publisher, per-contour change flag, always-run step; test failure fallback and unchanged heartbeat.
- [ ] GitHub status aggregation, independent workflow_run/schedule job, publication target metadata; tests for publication matching, missing reports and errors.
- [ ] Compact expandable status UI and states; Node tests and browser verification; document status/refresh.
- [ ] Deploy both services, verify real status JSON and public UI, verify successful Pages event updates independent status branch.

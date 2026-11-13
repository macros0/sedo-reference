# Group XML validation diagnostics

User request: one card per XML tag, shared path/buttons, list of every violated restriction; entirely local.

Design: private core.groupErrors(errors) groups only an unambiguous single occurrence with exact source ranges and the same field/path metadata. Source coordinates distinguish repeated tags even with identical structural paths. Attribute fields stay separate to retain correct reference navigation. Unknown/ambiguous/truncated locations remain separate rather than claiming one instance. Preserve all original diagnostics and order; group across differing diagnostic lines only if exact location matches, displaying the original line on differing reasons. First diagnostic provides shared source jump; existing tag/reference buttons unchanged. Raw worker errors and sourceView errors remain intact. No new dependencies, storage, parent data or network channels.

- [x] RED/GREEN real WASM facet grouping, distinct repeated tags, ambiguous/unknown locations, attributes and actual private controller card/path/buttons.
- [x] Render shared header plus reason list, update README/CSS/build;18 Node suites and focused Python checks; independent review and browser QA.
- [ ] Authorized main/Pages publication, full CI and public QA/screenshot; clean final state.

Ledger: existing isolated worktree at38222d6; main checked current before edits. Small explicit feature; implement directly with test-first. No real user XML used; screenshot attachment is evidence only, its identifiers must not be reused for testing.

RED/GREEN: real WASM two facets -> one group/card with2 reasons and one path/button set; repeats, unknown/ambiguous positions, attributes and input immutability covered. Review found namespaced absent-attribute identity collision. Reproduced RED with required id and codes:id; fixed conservatively by requiring an actual attribute range for grouping attribute paths. Present namespaced attributes still group their own facet reasons; absent attributes remain separate. No private identifiers from attachment used.

Local verification: all18 Node suites passed after review fix;8 focused Python build tests passed;264 revisions rebuilt. Review P2 resolved with RED/GREEN absent/present namespaced-attribute tests; no other findings. Actual browser on synthetic10110: lnNumber length16 vs12 and pattern12 are one card with2 reasons, one path and one button set. Source jump highlighted both names; reference jump selected lnNumber. Editor correction to12 zeros removed only that card; remaining missing-block diagnostics stayed separate. Clear emptied cards. Only static GET requests in local server log. Own QA tab closed and server stop sentinel written.

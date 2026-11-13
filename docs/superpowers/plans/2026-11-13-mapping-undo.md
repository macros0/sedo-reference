# Mapping selection and undo implementation plan

**Goal:** Hide occupied endpoints in the browser mapping selectors and undo one saved mapping from its comparison card.
**Architecture:** Keep the existing draft/store and renderer. Filter the picker from selected draft rows, retaining the edited pair. A card button saves the latest mapping set minus exactly one pair and awaits comparison refresh. Preserve comparison filters; block undo when there is an unfinished draft or stale route.
**Tech Stack:** Vanilla JS, Node VM regressions, existing static build, CUA.

- [x] RED/GREEN: selector occupancy/current pair/deselection, per-card undo/persistence/parent/split/unchanged/stale/draft guard.
- [x] Implement picker filtering and undo with visible feedback; document behavior.
- [x] Run Node suites, build, browser QA and review.
- [x] Publish main, await CI/Pages and verify public assets/UI.

**Additional user direction:** Saving one pair keeps the pickers open for the next pair, clears the selected paths/comment and refreshes available tags. Show a save confirmation. An empty continuation form allows applying the draft; a partial next pair blocks apply until finished or cancelled. Closing the form retains already saved draft pairs.

**Validation:** Selector/undo regressions and open-form regression each failed before implementation and passed afterwards. All 12 Node suites and static build passed. CUA: 16→15 pairs on undo, search retained, freed paths selectable, save retains blank form and filters used paths, apply directly from blank form restores 16 pairs / 0 removals, persisted after reload. Independent review: no substantial outstanding findings. GitHub run 43348841788 passed 86 Python tests and all 13 Node suites; source 97d882b built and deployed. Public CSS/JS fingerprints match source. CUA with published assets verified undo (16→15), returned selectors, open continuation form after save, and direct apply restoring 16 pairs / 0 removed.

**Integration note:** Main advanced with schema grouping and then 64ae481, which preserves raw importer journal identities alongside the grouped browser journal. The redundant local alias-lookup fix was dropped before publication; no importer behavior change is part of this UI patch. An earlier direct Windows test `test_docx_and_xsd_build_tree_and_comparison` hit a pre-existing LF/CRLF byte-history guard in the checkout. Focused clean-archive checks passed; the final combined revision passed the full Linux CI suite. Independent reviews reported no outstanding substantial findings.

**Follow-up: retain picker searches (source d1de7bc).** Preserve the exact search text in both pickers when a pair is saved; selection/comment still clear and used tags remain excluded. Regression first failed on an empty before-query, then passed for two successive pairs with path and Cyrillic description searches, preserving whitespace and excluding other blocks. All 13 Node suites and build passed; independent review found no substantial issues. GitHub run 43383885825 passed 86 Python tests, all JS checks and Pages deployment. Published asset fingerprints match source. Browser QA using published assets saved code/details pairs in the same error block and retained both searches with only message left available.

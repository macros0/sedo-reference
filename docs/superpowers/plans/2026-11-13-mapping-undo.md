# Mapping selection and undo implementation plan

**Goal:** Hide occupied endpoints in the browser mapping selectors and undo one saved mapping from its comparison card.
**Architecture:** Keep the existing draft/store and renderer. Filter the picker from selected draft rows, retaining the edited pair. A card button saves the latest mapping set minus exactly one pair and awaits comparison refresh. Preserve comparison filters; block undo when there is an unfinished draft or stale route.
**Tech Stack:** Vanilla JS, Node VM regressions, existing static build, CUA.

- [x] RED/GREEN: selector occupancy/current pair/deselection, per-card undo/persistence/parent/split/unchanged/stale/draft guard.
- [x] Implement picker filtering and undo with visible feedback; document behavior.
- [x] Run Node suites, build, browser QA and review.
- [ ] Publish main, await CI/Pages and verify public assets/UI.

**Additional user direction:** Saving one pair keeps the pickers open for the next pair, clears the selected paths/comment and refreshes available tags. Show a save confirmation. An empty continuation form allows applying the draft; a partial next pair blocks apply until finished or cancelled. Closing the form retains already saved draft pairs.

**Validation:** Selector/undo regressions and open-form regression each failed before implementation and passed afterwards. All 12 Node suites and static build passed. CUA: 16→15 pairs on undo, search retained, freed paths selectable, save retains blank form and filters used paths, apply directly from blank form restores 16 pairs / 0 removals, persisted after reload. Independent review: no substantial outstanding findings. CI/Pages pending publication.

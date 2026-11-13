# Source additions to an existing schema edition

**Goal:** Show a separate journal event when a new source document appears for a known schema, without adding another schema edition.

**Design:** Derive source_added events from immutable original updates and existing schema groups. Source identity is document SHA-256 plus original URL within the message/contour/schema group. Documents first found together in the group's initial snapshot belong to its initial appearance, not later additions. A new source in a later snapshot gets a stable event ID, the original first-observation timestamp and a link to the canonical existing edition plus that source's DOCX. Repeated observations and alternate revision IDs of the same source produce no event. Original updates and grouped schema_updates contracts remain intact; new source_updates is a separate derived list.

The journal merges both event lists by discovery date. Added-source cards have their own badge and show only the new document, with no tag comparison counters or new-edition label. Existing search, contour and date filters apply. Only with changes retains its existing meaning: changes in XML tags; source-only events are hidden by that filter.

- [x] Add failing tests for initial batches, later sources, duplicate records, changed schema/contour, timestamps and stable event IDs.
- [x] Implement derivation and integrate with site compilation without changing immutable records.
- [x] Add journal rendering and filter regressions, including source-specific searches and date boundaries.
- [ ] Update README, run focused and integration checks, publish and verify CI and public page.

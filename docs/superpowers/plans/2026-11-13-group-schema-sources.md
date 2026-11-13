# One schema edition with multiple document sources

**Goal:** Show identical message schemas once per contour, with every source document, document version, date and original download.

**Design:** Keep immutable source revisions, fields, source objects and historical events unchanged. During site compilation annotate equivalent ready revisions with a stable first-seen display revision ID and a combined source list. Equality requires the same message, contour, root, namespace, exact SHA-256 set of transitive XSD dependencies and the same field tree (excluding source file locations). Document-only records never merge. XML, constraint or description differences prevent merging. Thus document versions are source metadata, not competing schema editions.

The site filters edition lists and comparison choices by display ID; old revision URLs, comparison IDs, saved field files and workbook IDs remain available. The browser journal uses schema_updates with one discovery event per schema group and comparisons between distinct groups. Full updates with their original IDs and comparisons remain published for importer report compatibility. Source lists are visible on cards, viewer, comparison, journal and exported workbook metadata. No snapshot migrations, discarded history or collector changes.

## Implementation

- [x] Add failing Python cases for exact equivalence, contours, dependencies, constraints/descriptions, document-only rows, stable representative, source retention and journal predecessors.
- [x] Implement display grouping in edition_groups.py and integrate after field compilation in build_site.py; leave data/catalog.json and source history untouched.
- [x] Render all sources consistently, filter canonical choices and preserve legacy routes/IDs. Add UI regression coverage for message 14 and legacy links.
- [x] Verify workbook metadata includes all sources without changing its import contract.
- [ ] Run focused checks, build and inspect the real browser, then publish and follow full CI/Pages to completion.

## Acceptance

Message 14 has one visible schema in prod and one in test, each with three distinct source DOCX documents. Different constraints or root/namespace remain distinct editions. Old URLs open their original field data and Excel identity. New appearances of an unchanged schema add a source to its group. Archived bytes and original source events are retained.

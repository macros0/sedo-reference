# Remaining SEDO schemas and functional section navigation

User scope: investigate all messages without trees, find official missing schemas, fix parser/update issues, publish. Additional steering: section menu has no useful options for 86. Preserve all 169 published editions and their original fields; follow official provenance, do not guess missing constraints.

Plan: (1) classify diagnostics and missing declarations; inspect original modules and same-contour SFR pages/archives. (2) Add regression tests before fixing root association, package/dependency resolution and parser constructs. (3) Make section navigation use actual XML blocks, not benefit-name whitelist, with scoped search and useful labels. (4) Build candidate, record per-message remaining reasons, focused tests/browser checks, independent review; required full CI gate, push, await Pages and verify public pages.

Use current checkout. Publication explicitly authorized. Current baseline 027052e, 85 numbers/169 editions. Old message data/trees are immutable acceptance baseline.

Steering: add bidirectional comparisons of confirmed previous/current message numbers (105↔10510 and all explicit mappings), with correct per-side tree links and message-number labels. Keep ordinary prod/test and version comparisons. Never infer transitions by suffix.

Implementation decisions: preserve native full XSD bytes; extract complete inline xs:schema blocks without collapsing whitespace; recover declared roots from complete XML examples only if needed; repair bad relative imports only via unique name+namespace within one archive; combine packages only from the same DOCX. Add disjunctive union alternatives to tree data/viewer/comparison. Extend published companion pages to MCHD and ELN, filtering schema links so software installer archives are excluded. No cross-contour mixing.

Validated outcome: source collection fetched 110 files and built 228 editions for 114 numbers. All 169 pre-task published editions retained byte-identical field trees and events. 16 unresolved type numbers remain; exact missing import reasons are saved in data/schema-coverage.json. No schema dependencies are borrowed from another document/contour without matching provenance. Source DOCX 602 contains a copied XML example; the table root and appendix boundary regression protects it.

Additional user correction: README fixed catalog/edition/mapping totals removed. Browser checks passed at 1280 and 390px: scope/select/search/reset on86, union104 including visible empty-string alternative, 105→10510 and10510→105, swap and correct field/tree links. Independent final review found no substantial issues. Full Python/Node CI gate and public verification run during publication.

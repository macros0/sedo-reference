# Embedded module XSD implementation plan

User scope: XML trees for 600–602 and 611–612 in both contours, searchable names/descriptions and original XSD constraints; publish to GitHub Pages.

Use original embedded XSD archives from the DOCX, extracted as data using pinned olefile/py7zr dependencies. Associate a message with the root declared in its own structure table. Feed extracted schemas through the existing Schema/tree pipeline; isolate document/environment/revision identities and retain all previous fields/events. Store the exact DOCX and deterministic extracted ZIP with provenance. Daily updates repeat the extraction for new versions. Reject missing, ambiguous or incomplete schema dependencies rather than silently guessing constraints.

Tasks: (1) inspect actual embedded archives and test extraction, root association and dependency resolution; (2) implement extraction/sync/catalog integration and dependency installation in all CI workflows; (3) create validated editions and check real restrictions, search, enum values and contour isolation; (4) one independent read-only review, full CI gate, push, await Pages, verify public viewer.

Reuse the current checkout. Requirements and publication are explicitly authorized. Preserve all existing editions and journal events.

Scope update: include RPU 86/87/88 and fix discovery generically. Root cause: only /specs_sedo ZIPs were fetched, no OLE extraction and no traversal of /prab.html. The RPU DOCX actually points to PRAB rather than containing OLE. Automatically match document-declared roots to complete same-contour schemas; validate through Schema before adding an edition, expose unresolved declarations in update-report.schema_diagnostics. Equivalent editor temp-file aliases are deduplicated by the full normalized XSD XML and dependency contracts, including namespace contexts/forms and QName fixed/default values; only comments, indentation and the XMLSpy root vc:minVersion hint are excluded. Differing contracts are rejected.

Validation completed locally: 11 extraction/crawl/ambiguity/limit tests; two acceptance tests covering all eight types in both contours and original restrictions; archive/module/observation checks; all four Node suites; verify.py; 112 browser assertions at each of 1280 and 390px. Real staged update built 169 editions for 85 numbers and preserved all original 56 edition IDs, fields and events. All new matches were revalidated after stricter XSD normalization. Independent review's namespace-dedup and aggregate-budget findings were fixed with failing-then-passing regressions, and reviewer confirmed both closed.

Publication uses the existing required full Python/Node suite and Pages deployment gate. No additional approval is needed: user explicitly requested publication.

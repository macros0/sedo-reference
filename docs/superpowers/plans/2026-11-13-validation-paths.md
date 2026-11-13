# Validation error paths and reference navigation

> Execute inline with superpowers:executing-plans; TDD and one independent final review.

**Goal:** Show XML paths in local validation errors and navigate to the matching field of the exact selected edition without disclosing XML values.
**Architecture/design:** Add public tree paths to the verified schema companion. A bounded namespace-aware lexical index runs inside the worker only after successful libxml parsing; error element/attribute plus line determines canonical paths. Repeated instances of one structural path collapse; ambiguous paths are alternatives. Syntax errors remain unlocated; unknown tags link to nearest public ancestor, explicitly labeled. Child click sends only a numeric public field index. Parent checks active frame/revision/index, updates tree/hash without rerouting or clearing validator, scrolls to field. XML, actual unknown paths, line numbers, excerpts and results never leave isolation. CSP and network prohibition unchanged. This user-requested index-only channel replaces the earlier no-messages wording.
**Tech:** current Python/JS/WASM, no dependency or engine replacement. Existing isolated branch codex/browser-excel-mapping; clean start cf38e8e. Standing authorization includes GitHub publication.

## Constraints
Exact archived tree paths, no archive/history/ZIP changes. Canonical paths omit instance indices to match the reference. Location indexing cap100000 elements/depth256; if exceeded, retain XSD validation and line errors. At most8 distinct alternatives, explicitly label ambiguity/truncation. No XML data in parent messages, URL, storage or network. Maintain input/results during navigation.

## Tasks
- [x] RED/GREEN public paths companion and build cache: xsd_bundle.py/build_site.py/test_xsd_bundle.py.
- [x] RED/GREEN real WASM errors: nested/repeated/same-line/namespaced paths, attributes, missing child, unknown tag, malformed XML, bounds. New web/validation-paths.js, worker and build assets.
- [x] RED/GREEN rendering and checked numeric navigation, stale/source/revision/index guards, preserve validation state and clear filtered tree scope. validation-frame.js/validation.js/app.js/build_site.py and JS tests.
- [ ] Full JS/focused Python/all-closure tests, independent review and browser privacy/navigation QA.
- [ ] Publish main/Pages, wait full CI, verify public operation, close QA server/tabs, record results.

## Review focus
Same-line homonyms must not be guessed; prefixes/default namespaces/attributes handled conservatively; syntax/unknown fields must not fabricate known leaves; spoofed/stale/out-of-range messages must not navigate; field navigation must not reroute or leak private data. Archive/history/schema ZIPs unchanged.

## Ledger
Use existing canonical field routes, no instance indices. Ambiguous structural paths show alternatives rather than guessing. No extra user preferences needed for the stated behavior.

Independent review findings fixed with regressions: quoted XML values resembling attribute diagnostics no longer affect the subject parser; multiline opening tags use all same-QName line intervals rather than an unsafe start-line priority. Added exact per-field namespace sets from a read-only Schema subclass following XSD elementFormDefault/attributeFormDefault/form/global ref rules; every candidate and ancestor must match these before its public field index is offered. Wrong-root diagnostics have no reference link. This supersedes the initially considered local-path-only match; no immutable tree/source data is changed. Namespace-cache keys include public path order. More than8 alternatives stop after9th; reconstructed path length cap8192. Resource-bound finding graded important because unbounded private path strings can exhaust memory, not just formatting polish. Reviewer independently verified264 companion dictionaries/15074 public paths; browser/public/CI QA remains root-owned.

Local evidence:31 focused Python tests pass, all16 Node suites pass,142 real WASM closures compile. Browser error /subscribeRequest/recipientList/regNum opens exact field with regex \d{10}; unrelated section/search reset, XML/error remain. Repeated jump clears new search. Wrong-namespace regNum opens confirmed recipientList parent. Server log contains no synthetic XML/query markers or POST. All264 dictionaries/15074 public paths match historical order, with nonempty XSD-derived namespace sets. Namespaceless message115 uses empty URI consistently (catalog null normalized).

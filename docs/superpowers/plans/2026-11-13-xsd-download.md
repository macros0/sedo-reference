# Selected-edition XSD download implementation plan

> Execute inline with superpowers:executing-plans; TDD for each behavior and one final independent review.

**Goal:** Download the XSD closure for the exact selected message/contour/revision so an XML payload can be validated locally.
**Architecture:** xsd_bundle.py reads only the revision content-addressed archive and recorded dependency hashes, preserves original bytes, generates a local copy with only schemaLocation paths changed, compiles it without network access, and publishes a deterministic per-revision ZIP. xsd_validate.py is shared by the build and bundled as validate.py. Build adds download metadata without mutating historical data. app.js/compare.js expose downloads for cards/viewer/selected comparison sides, retaining legacy revision IDs.
**Tech Stack:** Python/lxml 6.1.3, stdlib ZIP, current vanilla JS site and CI.

## Contract
- ZIP contains message.xsd, schemas/*, originals/*, manifest.json, README.txt, validate.py and requirements.txt.
- Manifest records selected number/contour/revision/document version/date/root/namespace, source archive hash, per-file original/local hashes and path rewrites.
- Only the archived dependency closure is included. No current/latest/other-contour substitution. Originals remain byte-identical. Local copies preserve namespace/QName/facet/annotation content; only import/include paths are rebound.
- Checksums, missing files and unsafe ZIP paths block publication as integrity errors. Unsupported/invalid XSD is reported without a misleading ready download. No-schema editions retain DOCX and explain unavailability.
- Local validation denies external resources/DTDs, validates the selected root, reports XML errors, and sends no XML anywhere. Requirements installation is a separate one-time step. It checks XSD structure/types, not SFR business checks or digital signature authenticity.
- Existing ready trees, archived documents, imports/updates, grouping and legacy IDs remain compatible. Build automatically creates bundles for future imported/scheduled editions.

## Tasks
- [x] RED/GREEN: package integrity/determinism, exact old/test revision, local and broken/absolute imports, QName-safe rewrite, input validation and selected root.
- [x] Implement standalone validator and package publisher, install/pin lxml and integrate build. Audit every available revision closure with real offline schema compilation.
- [x] RED/GREEN UI: card/viewer revision switching, grouped legacy ID and comparison sides; unavailable state. Add downloads/documentation/workflow coverage.
- [x] Focused tests, all JS suites, full CI, browser download/extraction/validation QA and final independent review.
- [x] Publish main, wait Pages, verify public archive/hash/UI, clean temporary test state.

## Investigation
Read archive/dependency derivation, DOCX/module/WSDL extraction, grouping, build and UI. An isolated read-only-source diagnostic compiled all 99 distinct archived schema roots with lxml 6.1.1 after rebinding paths (81 changed references across those closures). Original archives were not changed. Production uses the current stable 6.1.3 pin. Scope choice: ZIP plus optional standalone Python helper; existing validators can also use message.xsd directly.

## Verification ledger
Package module absence and missing UI links were observed before implementation. Nine XSD tests and seven existing document/build tests pass; all 13 Node suites pass. ZIP headers were additionally normalized with create_system=3 after a cross-platform byte-equality test failed; regression now passes. The complete current archive set exports successfully (264 edition bundles, 8.43 MiB); original XSD bytes are verified against each exact source archive. No source data/history was changed. Browser downloaded 10110 prod3.0 to Downloads; extracted helper --check-schema returned0 and incomplete XML returned1. The viewer switches to the correct test3.0.4 ZIP. Independent final review and targeted ZIP-header re-review found no actionable findings. Full CI/Pages and public verification completed; see final verification below.

**Linux regression found in CI 43393722040:** libxml2 passed native /tmp/... dependency filenames rather than file URIs. The local-only resolver incorrectly rejected these, leaving bundles unavailable. A native-filename loader regression reproduced the issue on Windows before the fix. Resolver now accepts absolute paths only within the package, rejects remote/UNC/outside paths, reads verified local files itself and retains an explicit file-URI base for nested imports. Full CI is repeated after this correction; initial failure was not published.

Linux correction verification: native-filename regression RED→GREEN; 23 focused Python tests and all 13 Node suites pass, all bundles rebuilt. Independent resolver review confirmed remote/UNC/outside paths remain blocked. Download URLs now include a content-hash cache key; its UI regression was RED→GREEN and separately reviewed.

## Final verification
CI/Pages run 43396039362 passed on source commit 8c321d094a4d4a41e975a7f4ddd73d3ab7855808: 102 Python tests, all 13 Node suites, collector/import/build and deployment succeeded. Public ZIPs for 10110 prod3.0, 10110 test3.0.4, 86 prod1.12 and message1 prod3.1 match published checksums; all manifest original/local file hashes and selected identities match, and the downloaded helpers compile the schemas offline with lxml6.1.3. Public browser UI confirms the exact-edition download URL with its bundle-hash cache key. Screenshot: C:/Users/alexey/Documents/Codex/sedo-xsd-download-2026-11-13.jpg. Temporary QA server stopped and temporary browser tabs closed. Working copy synchronized with generated publication commit d5d22f0. No private XML was transmitted.
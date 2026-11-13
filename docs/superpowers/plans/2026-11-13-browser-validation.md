# Browser validation implementation plan

> Execute inline with superpowers:executing-plans, TDD and one final independent review.

**Goal:** Validate selected-edition XML entirely inside the browser without transmitting or persisting private file data.
**Architecture:** deterministic verified schema companion + vendored WASM engine; parent loads public assets, creates opaque sandbox iframe with network-denying CSP, and never receives file data. Child owns file chooser, root precheck, worker lifecycle and result rendering.
**Tech Stack:** existing Python build, vanilla JS, xmllint-wasm5.3.0, browser WASM/Web Crypto/DecompressionStream.
**Spec:** docs/superpowers/specs/2026-11-13-browser-validation-design.md

## Global constraints
Exact selected message/environment/revision, original archives/history unchanged. No XML upload, parent messaging, analytics or browser persistence. 10MiB, 30seconds, 30 errors. Reject DTD/entity declarations. Explicit XSD-only scope. No dependency CDN.

## Review focus
- Opaque sandbox and inherited blob-worker CSP really deny network, including XML-supplied URLs.
- Edition changes and async preparation cannot show a stale schema or retain private file state.
- WASM must receive unchanged XML values and all nested imports from the exact archived closure.
- Malformed/oversize/DTD/legacy-encoded input never executes markup or hangs the UI.
- Hash mismatch, unsupported browser and runtime error fail closed and remain retryable.

## Tasks
- [x] RED/GREEN exact-edition companion integrity/determinism; publish metadata, leave ZIP bytes intact. Files: xsd_bundle.py, tests/test_xsd_bundle.py.
- [x] RED/GREEN real WASM worker tests and private UI/controller tests; vendor fixed npm artifact/license/hash. Files: web/validation-worker.js, web/validation-frame.js, web/validation.js, web/vendor/xmllint/*, tests/test_browser_validation.cjs.
- [x] Integrate controls, revision lifecycle, build assets and CI tests; update README. Files: web/app.js, web/index.html, web/app.css, build_site.py, workflow test lists, existing build fixtures.
- [ ] Run full suites/build, browser synthetic valid/invalid/DTD/offline/CSP QA; independent final review.
- [ ] Publish authorized main/Pages update, verify public operation, stop temporary server and record results.

## Ledger
Existing isolated worktree codex/browser-excel-mapping verified; clean start7301d18. User requirement supplies the browser-local/no-transmission architecture; implementation choices are documented above. No additional user preferences are needed. Existing standing authorization covers GitHub publication. Execute inline to avoid implementation delegation.

Encoding finding: xmllint-wasm lacks Windows-1251 iconv support. The real fixture failed with Unsupported encoding. Strict browser TextDecoder plus an in-memory UTF-8 encoding declaration resolves this without changing source files or XML values; UTF-16 and CP1251 fixtures verify it.

Final review fixes: CSP whole-script LF normalization; strict ASCII bytes and exact ISO8859-1 decoding instead of browser Windows1252 aliases; lexical DTD scan skips comments/CDATA/PIs; parent pagehide disposes the panel before BFCache. Each reported defect has a RED-to-GREEN regression. Added executed timeout, late-worker, worker-error, stale preparation, hash failure and history-disposal tests. Review independently checked all264 companions/1654 working XSD files against exact revision ZIPs and found no outward data path. Reviewer declined browser/public/network QA; root agent owns those checks. Device/extensions/future code are outside app control and explicitly documented.
Browser QA: actual isolated controller validated a synthetic schema-valid message1, reported invalid field on line2, rejected external DTD, and cleared results. Repeated valid/invalid/DTD checks after local HTTP server was shut down; no private marker/filename or POST appeared in its request log. Browser automation's filechooser event was unavailable for opaque iframe; file reading/selection controller tested in Node against real WASM, while live browser used the provided text input. Sandbox was retained.

Full local Python run103 tests failed on11 history checks plus dependent inbox rollback: old worktree data/fields files had CRLF although their immutable HEAD blobs are LF (git ls-files --eol i/lf w/crlf attr/-text). JSON content was identical. Restored each affected local file to exact HEAD bytes after verifying LF-only difference; no source data/history diff. Repeat full suite after this workspace correction.

Actual browser CSP enforcement probe used createDocument with a disposable synthetic controller: fetch, POST XMLHttpRequest and a blob-worker fetch all reported blocked. Server log contains only GETs for the probe page, program and favicon, none of the attempted endpoints. Probe page removed before commit.

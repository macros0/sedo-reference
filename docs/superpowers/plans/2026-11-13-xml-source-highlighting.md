# Local XML source highlighting and error jumps

> Execute inline with superpowers:executing-plans; test-first and one independent review.

**Goal:** Clicking a validation error opens its source line and highlights its XML tag locally, retaining current reference-field jumps and the privacy boundary.
**Design:** Existing private worker index gains bounded opening/closing tag (and present attribute) character ranges. Normalize CR/CRLF to LF for display/coordinates only; retain original input/file unchanged. Pasted non-UTF8 declarations carry original display text separately inside the private worker channel so encoding-header conversion cannot shift highlights. Repeated instances of one canonical path retain multiple bounded ranges; ambiguity is explicit. Syntax/unindexed errors navigate to line only, never guess a tag.
**Viewer:** New validation-source.js creates a read-only source pane using text nodes/mark, numbered lines, line/page/long-line-fragment controls. Keep at most25 lines,2048 chars per line and192 highlight spans in DOM; source <=10MiB remains only in isolated memory. Do not split millions of lines into a DOM/array. Error header is a keyboard-accessible button; per-path source buttons handle ambiguous paths separately. No auto scroll on validation, only explicit error clicks. New file/edit/clear invalidates viewer/results and stops stale work. Existing public numeric field-index channel unchanged; no source/ranges/line numbers leave opaque frame. CSP/network denial unchanged, no CDN/dependency.

## Tasks
- [x] RED/GREEN private source ranges: namespaces, CRLF, multiline, attrs/missing attrs, repeats, bounds, original pasted encoding header. validation-paths.js/worker.js and real-WASM tests.
- [x] RED/GREEN bounded safe source pane, error line/tag selection, long single-line XML, last line, clear and stale input. New validation-source.js/test_validation_source.cjs.
- [x] Integrate error buttons/preview, runtime asset hash/CSP/build/CI and README; preserve reference navigation and ordinary search.
- [x] Full Node, focused Python, independent review, actual browser click/highlight/privacy QA.
- [x] Publish authorized main/Pages, wait full CI, verify public behavior/screenshot and close temporary state.

## Review focus
Original versus transcoded offsets; same-line repeated instances and ambiguous errors; no unsafe HTML or unbounded large-file DOM; edit/clear/stale results cannot retain old text/highlights; private coordinates/source never reach parent/network, existing field jumps still work.

## Ledger
Clean existing worktree/branch at23619e7. User scope is explicit, including local-only operation. Standing authorization covers GitHub publication. XML source viewer is read-only; source line endings are normalized for display, source files/input are not edited. Ambiguous candidates are marked as possible rather than selecting an arbitrary instance.

Independent review found3 issues before publication. Regression booting actual assembled srcdoc (without injected module globals) reproduced omitted source module; include it before frame and hash. Header click now honors diagnostic line, selecting a tag there if available; explicit per-path tag button can go to its opening tag/attribute. Real WASM CR-only regression reproduced wrong line1; normalize EOL only in temporary validation copy, preserving source file/input and character-reference semantics. Tests cover CR/CRLF/LF, original pasted declarations and a40-line opening tag. Reviewer found no additional material privacy/DOM/bound/stale issues; live browser and publication remain root-owned.

Local verification: all17 Node suites and8 focused Python build tests pass. Actual browser highlighted both regNum names on line3; manual line1 then error click returned to3 and focused MARK; reference constraints jump retained XML/marks. With40-newline opening tag, header jumped41 and explicit tag button jumped1. Edit invalidated preview, clear removed text/marks. Request log has no XML marker/POST. Independent review findings all addressed with regressions; no deferred findings.

Publication rebase: automatic SFR update12a8ab3 advanced main during development. Preserved all upstream state/data/objects; only generated site/catalog-data.js and site/index.html conflicted. Rebuilt site from combined sources/new inventory instead of choosing either side. Re-run all JS after this integration.

Final publication: source commit4ab9dee; generated artifact7e394c1. GitHub Pages run43432031704 succeeded (105 Python tests,17 Node suites,142 published schema closures compiled in WASM;264 revisions built). All17 Node suites also passed after rebase onto12a8ab3. Public runtime SHA-256 verified for all7 assets. Actual public-browser synthetic XML confirmed line1 to error click to line3, focused MARK, both regNum names highlighted; reference constraints navigation retained source and marks. Screenshot: C:/Users/alexey/Documents/Codex/sedo-xml-source-highlighting-2026-11-13.png. Own QA tab closed; local server stopped; no private user XML used. No deferred review findings.

# Local browser XML validation

User contract: validate XML inside the existing selected-message/contour/edition UI without transmitting user data to GitHub or any other service.

## Approach
Use vendored xmllint-wasm 5.3.0 (libxml2 XSD1.0) and the existing exact-edition archived closure. A server validator violates the privacy requirement. Python ZIPs remain available but do not satisfy in-browser use. Selected approach is an implementation choice: WASM inside an opaque-origin sandboxed srcdoc iframe, with its own file chooser and results. No backend, CDN, telemetry or persistence.

The parent loads only public schema/program assets before creating the isolated frame. Inside the frame a restrictive CSP denies connections, images, forms, nested frames and external scripts; only a hashed inline controller and blob workers are allowed. Blob workers inherit this CSP, use in-memory WASM and schema files, and additionally disable networking APIs. The frame never sends XML, filenames or results to its parent. All untrusted XML and diagnostics are text, never HTML. Switching edition/closing discards the frame; clearing terminates workers and clears file/results.

## Behavior
Add Проверить XML локально beside exact-edition XSD downloads on the viewer, and a link on edition cards. A preparation status precedes file selection. Show selected message, contour, document version/date and revision; 10 MiB per XML, 30-second worker timeout, cancellation, at most 30 displayed errors. Check well-formedness, selected root/namespace and XSD constraints, including imports. Reject DTD/entity declarations; ignore external schema locations and never fetch resources from XML. UTF-8, BOM UTF-16 and declared Windows-1251, ASCII and ISO-8859-1 are decoded for the root precheck; an in-memory UTF-8 copy is passed to libxml2 without changing values or source files. Business rules and signature authenticity remain outside XSD scope.

Publish a deterministic compressed JSON companion containing only the same verified working XSD closure and selected identity, with SHA-256 metadata. Existing ZIP/original bytes/history remain unchanged. Verify companion and engine SHA-256 before isolation. The validator requires WebAssembly, Web Crypto and gzip DecompressionStream in a modern HTTPS browser.

## Privacy boundary
The published app does not transmit the selected XML, its name or diagnostics and does not persist them. GitHub receives ordinary requests for the site, program and selected public schemas, including normal connection metadata. After preparation validation works with networking disabled. This guarantee describes this implementation; browser extensions, compromised devices and future modified code cannot be guaranteed by a web page.

## Verification
Real WASM tests with valid/invalid facets, dependency imports, selected-root checks, malformed XML, DTD, legacy encoding, cancellation/timeouts, unavailable and switched editions; checksum and deterministic exact-edition companions; full existing suites. Browser QA with synthetic XML only, restrictive CSP/network prohibition and offline operation, followed by independent code review and published-site verification.

Encoding finding: xmllint-wasm lacks Windows-1251 iconv support. The real fixture failed with Unsupported encoding. Strict browser TextDecoder plus an in-memory UTF-8 encoding declaration resolves this without changing source files or XML values; UTF-16 and CP1251 fixtures verify it.

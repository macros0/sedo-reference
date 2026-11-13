# Russian XML validation explanations

User request: plain Russian reasons (e.g. expected12 digits, supplied16), original XSD diagnostic under «Подробнее», retain grouped cards and private/local processing.

Design: new pure web/validation-messages.js explain(message)->string recognizes only anchored known libxml2 message shapes. Numeric pattern summaries interpret only simple digit patterns, count Unicode code points and distinguish ASCII-only [0-9] from Unicode digit \d; no generic regex guessing. Common lengths, numeric bounds/precision, enum values, primitive types, missing attributes/children and unexpected elements get Russian explanations. Unknown/ambiguous/truncated wording uses a neutral Russian fallback. No string replacement translation inside user values. Raw diagnostic stays exactly as received in a closed native details/pre, inserted via textContent. Each reason retains its own details; existing one path/buttons/card and source/edit/recheck remain unchanged.

Integration: add private messages asset to build manifest/CSP inline controller before frame (8 assets total); worker continues returning original diagnostics. No translation API/CDN/network/storage, no raw source/messages to parent. Tests must boot actual assembled srcdoc and cover raw text preservation and closed details.

- [x] RED/GREEN formatter unit cases and actual WASM/controller Russian+raw-details behavior; protect unknown/truncated/malicious echoed text.
- [x] Integrate eighth asset, styles/README/CI,19 Node suites and focused Python build checks; independent review and synthetic browser QA.
- [x] Authorized main/Pages publication, full CI/public hash checks and browser/screenshot, final clean state.

Ledger: existing isolated worktree at a128fec, clean and main-current before edits. Explicit small feature; execute directly with test-first; user screenshot identifiers are not test inputs.

Review P2: raw length facets cannot establish units (strings, binary bytes, list items). Reproduced RED with real WASM hexBinary length3 / AABB (actual2) and base64Binary / YWI=. Ruling: use unit-neutral «Точная/Минимальная/Максимальная длина по XSD»; digit-specific summaries remain only for explicit recognized digit patterns. This avoids mislabeling valid byte/item counts as visible characters.

Verification: formatter RED on missing module, then GREEN unit cases and real-WASM facet/type wording. Actual assembled srcdoc now runs a real validation and renders Russian reasons, guarding dependency order. Initial19 Node suites and8 Python build checks pass;264 revisions built. Review length-unit P2 fixed with binary regressions; reviewer spot-check clean. Native browser synthetic10110 showed Russian length and expected12 digits/supplied16, all raw details closed; opening «Подробнее» reveals untouched English, closing hides it. Reference jump and edited recheck/clear work. Grammar regression for1 fractional/significant digit also corrected.

Final local verification: all19 Node suites pass after grammar fix and full assembled-srcdoc behavior check;8 Python build checks pass;264 revisions built with8 private runtime assets. Native More open/close, unchanged raw English, reference jump, edited recheck and clear confirmed. QA tab closed; local server stop sentinel written. Server log contains only static GET paths and no synthetic XML marker. No deferred review findings.

Published: feature eefbadd and generated7f51654. Pages run43477568698 succeeded:122 Python tests,19 Node suites,142 published XSD closures compiled in WASM. SHA-256 verified for all8 public runtime assets including messages. Public synthetic10110 showed Russian length and «Ожидается12 цифр, указано16», raw diagnostics closed initially, native More revealed exact original and closed again; common reference jump and source highlighting work. Screenshot C:/Users/alexey/Documents/Codex/sedo-russian-validation-messages-2026-11-13.png. Own public/local QA tabs closed and local server stopped; no user XML used. No deferred review findings.

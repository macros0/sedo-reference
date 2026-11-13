# Current message filter

User requirement: default-on «Только актуальные» catalog checkbox hides obsolete message types.

Design: reuse explicit data/message-transitions.json correspondences. isSuperseded(number,catalog,environment) hides a previous type only when an existing successor is present in that contour. With all contours selected, require replacements in every contour containing the previous type; do not remove a production type just because its successor exists in test. No guesses by number length, document age, listed_now, or schema availability. Checkbox starts checked on each page load, combines with existing search/environment, keeps its value during navigation. Hidden counter explains omitted matches; empty results explain how to show older types. Archived direct links, all versions, comparison and journal stay accessible. No persistent settings needed.

- [x] RED/GREEN classification and actual catalog DOM controls (extend existing transitions/edition_groups suites).
- [x] Checkbox/layout, explanatory count/empty state, README; build and full17 Node suites, focused Python build checks, local browser.
- [x] Authorized main/Pages publication, full CI, public browser/screenshot, final clean status.

Ledger: small clear feature, implement directly under user's request; existing isolated worktree on codex/browser-excel-mapping at b819072. No source-data changes or new dependencies.

Verification: RED missing isSuperseded; GREEN classification and actual app control tests. All17 Node suites and8 focused Python build tests pass;264 revisions built. Independent review has no actionable findings. Local browser: checkbox checked on initial load;105 query shows10510, unchecked shows105 and10510; legacy105 page and comparison links work; return retains toggle; test contour filtering works; reload restores checked default. Own local QA tab closed; server stop sentinel written.

Integration: main advanced to09734da with SourceCraft collector/status work. Rebased feature onto it. Preserved upstream web/index.html and app.css content and inserted only filter controls/styles; generated site conflicts are resolved by rebuilding combined source. Run expanded JS suite and current Python build checks before publication.

Published: feature36c595f and generated490dc2d, preserving upstream09734da collector/status changes. Rebased build passed264 revisions; all18 Node suites and8 focused Python checks passed locally. Pages run43464718798 succeeded with122 Python tests,18 Node suites,142 published XSD closures compiled in WASM. Public JS/CSS/transition asset hash URLs verified. Public browser confirmed default checked; search105 shows10510 only; unchecked shows105 and10510; rechecked returns10510. Existing automation status panel works. Screenshot C:/Users/alexey/Documents/Codex/sedo-current-messages-filter-2026-11-13.png. Own browser tabs closed, local server stopped. No deferred findings.

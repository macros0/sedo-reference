# XML editor line numbers

User request: show line numbers while editing XML, consistent with validation diagnostics.

Design: keep the existing native textarea/undo and wrap it with a separate, non-selectable gutter inside the opaque private iframe. No soft wrapping; horizontal scroll never moves numbers. Render only visible rows plus small overscan (max64) using actual textarea scrollTop, clientHeight, line-height and padding. Limit native vertical resize to1200px so every visible row fits bounded gutter DOM. ResizeObserver synchronizes user resizing; scroll/input/caret/error navigation synchronize numbering and active line. Gutter width accounts for total number digits. Textarea value and validation byte/line semantics stay unchanged; no new dependencies/assets or parent/network/storage channel.

- [x] RED/GREEN initial/CRLF/trailing line numbers, blank lines and typing, scroll/horizontal isolation, resizing, error jumps, active caret, large DOM bound, clear. Source/actual-controller regression suites.
- [x] CSS/README/build,19 Node suites and focused Python build checks, independent review and native browser typing/scroll/resize/undo.
- [ ] Authorized main/Pages publication, full CI/public QA/screenshot and clean final state.

Ledger: existing isolated worktree at8283c26, clean and main-current before edits. Explicit small feature; implement directly with test-first and preserve local-only XML boundary. Synthetic XML only for QA.

RED/GREEN: missing gutter then initial/source/controller suites green. Additional RED confirmed automatic validation changed active gutter line while caret stayed in place. Ruling: when edit mode receives automatic results (scroll=false), keep the caret line/column; explicit error clicks still navigate/select. Regression covers this without resetting textarea selection.

Review P2: caret-only changes left previous/next and long-line controls stale. Reproduced RED first-line->line2; fixed with shared fragment/navigation updater, preserving native selection/scroll without rebuilding textarea. Tests cover upward/downward movement and long->short lines.

Local verification:19 Node suites and8 focused Python build checks pass;264 revisions built. Review P2 resolved and spot-check clean. Native browser synthetic105-line XML: error103 selects regNum and active number103, gutter93-105 aligns visually. CtrlHome ->1, ArrowDown ->2 enables previous, PageDown scrolls numbers, newline ->106, undo ->105. Editing clears errors/widens layout: native clientHeight245->260 and gutter viewport follows260px (ResizeObserver resize coverage). Revalidation keeps caret105; explicit error click ->103. Horizontal scroll keeps numbering fixed. Clear empties text/numbers. Manual resizer callback also tested at600px in unit suite. QA tab closed; server stop sentinel written; only static GET paths logged.

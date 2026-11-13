# Local XML editing implementation plan

> Execute inline using superpowers:executing-plans and test-first; use the existing isolated worktree.

**Goal:** Correct XML inside the source pane and revalidate locally, preserving private file/data isolation.
**Architecture:** A native textarea toggles within the existing source pane. The viewer owns its draft; the private controller always prioritizes that draft over the originally chosen file or pasted input. No new dependencies, network channels, storage, download, or source-file writes. Validation results and marks are invalidated on every edit. Editor content/undo survives repeated validation and view switches; explicit error navigation selects the correct text in edit mode.
**Tech stack:** Existing vanilla JavaScript, opaque iframe, private WASM worker.
**Spec:** User request in this chat plus the design below.

## Design and constraints
A button opens the full XML for editing at the current line, another switches to the bounded highlighted view. A nearby repeat-validation button and the existing check button both validate the current draft. After validation the editor stays available, and errors remain clickable. Empty draft cannot fall back to a previous file. Selecting another file, editing the original paste box, clearing, closing, or changing revision discards the source draft. Original file on disk never changes. Textarea uses native text input, not contenteditable or interpreted HTML; no CDN/editor library. Browser text input normalizes EOL to LF; initial file validation remains encoding-aware, edited text is validated as a temporary UTF-8 copy with original display offsets. Maximum input remains 10 MiB and existing DTD/network restrictions stay in force.

Ruling: Treat existing explicit task and standing publication authorization as sufficient for routine UI choices; implement inline without a new approval round. Use a native textarea because existing bounded view cannot safely be contenteditable and no extra library is needed.

## Tasks
- [x] RED/GREEN source editor: toggling, current-line selection, draft, change callback, stale marks, empty text, safe text, preserve editor on revalidation, clear. Files: web/validation-source.js, tests/test_validation_source.cjs.
- [x] RED/GREEN controller: original file -> invalid -> edit -> valid, global and inline repeat checks use draft, stale worker ignored after edit, blank draft, new-file and clear reset. Files: web/validation-frame.js, tests/test_browser_validation.cjs.
- [x] Source editor CSS and README; rebuild site. All17 Node suites, focused Python build tests, independent review, real local browser edit/recheck/clear and privacy QA.
- [x] Publish main/Pages under standing authorization; full CI, public browser QA and screenshot; clean worktree and record evidence.

## Review focus
Repeated checks must not restore original file/paste or erase input/undo; late worker/file reads must not restore stale diagnostics; empty draft must stay empty; current line/selection must agree with LF-normalized source, including long lines; edited XML cannot leave iframe/worker or execute HTML.

## Ledger
Start at d0476c5 with clean working tree. Existing worktree is isolated on codex/browser-excel-mapping. No user XML is needed for QA.

RED: source suite failed with missing local edit-mode button. GREEN: source and real-WASM controller suites pass, including invalid file -> corrected valid draft, global check with draft, late result discarded, blank draft. Added explicit new-file reset regression.

Review: no material findings in first independent pass; reviewer requested native undo/caret QA, especially tabs. Actual browser confirmed editor repeat-check and native undo survive validation. Reproduced horizontal caret bug:100 tabs before regNum selected correct text but scrollLeft521 versus scrollWidth6414. RED regression failed. Ruling: measure prefix using actual textarea computed font via local Canvas2D and visual tab stops instead of character-count approximation; no XML drawn or exported. This is still inside private frame.

Local completion: all17 Node suites passed after geometry fix;8 focused Python build tests pass;264 revisions built. Independent review and geometry spot-check found no remaining actionable issues. Real browser: invalid XML -> edit -> valid, editor stays open; native undo survives check and invalidates result; error click selects regNum;100-tab case now scrollLeft5760/6414 and selected tag visibly in viewport. Clear removes editor text/marks; no XML markers or POST in local server log. Local QA tab closed, stop sentinel written. Standing authorization covers main/Pages publication.

Publication complete: source a820514, generated c36a90b. Pages run43435925341 succeeded:105 Python tests,17 Node suites,142 published XSD closures compiled in WASM,264 revisions built. Verified SHA-256 for all7 public validator assets. Public browser synthetic case: error click selects regNum in editor; correct BAD_PUBLIC_EDIT_QA to1234567890; inline repeat validates successfully and editor stays open. Screenshot C:/Users/alexey/Documents/Codex/sedo-xml-editor-2026-11-13.png. Own public QA tab closed, local server stopped, no private user data used. No deferred review findings.

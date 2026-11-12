# Module message discovery implementation plan

User scope: add 78→7810, 79→7910, 82→8210, 83→8310; expose 600–602 in prod and 611–612 in both contours; update daily discovery and publish to GitHub Pages.

Design: retain the existing Python/std-library pipeline and checkout. General registry rows have priority. Parse message declarations in module DOCX body headings (not XML examples, tables, or the table of contents). Supplemental rows carry environment, module, original DOCX hash, version/date and source. Keep them in historical snapshots, archive original bytes, and expose downloads. Do not create XML editions without a matched XSD or invent retirement dates.

1. Add failing unit tests for DOCX heading extraction, false positives, supplemental registry precedence/contour separation, originals and historical retention.
2. Implement sync.inspect_file metadata and catalog.derive fallback rows; retain module originals in auto_update and build_site; show module source/version/download in web/app.js. Preserve all existing edition IDs, field trees and journal events.
3. Add four explicit transitions using individual official module specifications as sources. Update README and SOURCES with discovery behavior and verified totals.
4. Run focused tests, real staged update, full Python and Node suites, and browser checks. Obtain one read-only review, fix significant findings, commit/push, wait for Pages and verify public routes/downloads.

Progress: initial repository clean at cf54994. The request already specifies implementation and publication; no additional approval is needed.

Validation: six module tests, five archive tests and two observation tests passed. All four Node suites passed. Real auto_update fetched 100 files and produced snapshot 840c13cc06d385ea7eae6908a1671fbb56d7f683ba83427aed5488aab7f4b1ec: 56 unchanged editions, 249 registry rows, seven new environment/message combinations, no review issues. Browser checks: 38 assertions at each of widths 1280 and 390. Independent read-only review found no substantial issues.

Ruling: reuse the current clean checkout, consistent with the existing publication workflow and user instruction; no isolated worktree is required for this scoped update. Run the full suite once in the required Pages CI gate after focused local checks and the real staged update; avoid duplicating the costly full import suite on Windows.

Publication pending: push this implementation, wait for the full CI suite and Pages, verify public pages and archived downloads, then sync the bot build commit.

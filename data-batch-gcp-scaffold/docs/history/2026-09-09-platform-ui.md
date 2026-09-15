# Platform implementation: local UI milestone — 2026-09-09

Status at this milestone: implemented and validated locally; not deployed.
See [the completed cutover](../platform-cutover-review.md) for final deployment and acceptance.

## Baseline and changes

Inspected the existing modified worktree and untracked migrations 007–010,
subject-management service/UI, and integration suite. No applicable AGENTS.md was
found. Preserved that baseline and the protected source-split receipts. Did not
rerun source splitting. Live cloud inventory and final schema contracts are still
pending; historical production counts are not current verification evidence.

Changed `ui/subjects.js`, `ui/styles.css`, `ui/index.html`, and `ui/app.js`:

- Multiple sources checkbox and text share one clickable horizontal label.
- Sort subjects uses an explicitly associated label beside a shrinking select.
- Search Subjects fills the lookup container; existing filters and pagination remain.
- Recent runs lives under Check a run, refreshes on activation, and uses the same
  run opener as manual entry. Check navigation is represented by `/?view=check`.
- Requests from earlier recent-list visits cannot overwrite the newest response.
  In-flight run polling, selection, and results responses cannot take over a
  newly selected subject/check/submission view.

Updated browser coverage in `tests/integration/test_subject_management.py` for
responsive layout, keyboard focus, repeated recent-list navigation, out-of-order
failure responses, unavailable lists, manual lookup, and recent-run links. Updated
existing button locators to the requested capitalization. Fixed existing lint
issues in `scripts/verify_gpu_image.py` (executable mode and import ordering).

## Validation evidence

Commands ran from `face-batch-gcp-scaffold`:

```bash
.test-venv/bin/python -m pytest tests/unit -q
FACE_SUBJECT_TEST_PORT=55440 FACE_BROWSER_TESTS=1 \
  FACE_BROWSER_ARTIFACTS=/workspaces/ThunderCloud/.local/platform-ui-20260909 \
  .test-venv/bin/python -m pytest tests/integration/test_subject_management.py -q
.test-venv/bin/python -m ruff check worker tests scripts
node --check ui/app.js
node --check ui/subjects.js
git diff --check
```

Results: 120 unit tests passed; 25 PostgreSQL/integration/Chromium checks passed;
zero skips. Lint, JavaScript syntax, and whitespace checks passed. Browser layout
assertions covered light/dark at 390px and 1280px, inline vertical alignment,
full-width button, horizontal overflow, and keyboard focus order. Inspected the
390px dark and 1280px light screenshots visually as well. Existing subject edits,
move/merge, grouping, and source review browser flows passed.

Protected evidence: `.local/platform-ui-20260909/receipt.json` and four PNG files,
with SHA-256 digests in the receipt. Directory mode 0700; files 0600. Retained by
the ThunderCloud implementation until 2026-10-09 for browser acceptance evidence.
These synthetic local tests do not establish production IAP sign-in or user review.

## Cleanup and next action

Created only the disposable PostgreSQL container
`thundercloud-platform-ui-20260909`, labeled with execution
`platform-ui-20260909`, bound to localhost port 55440, using the already available
`pgvector/pgvector:pg17` image. Stopped it after verification; automatic removal
was confirmed by an empty label-filtered `docker ps -a`. No named volume or custom
network was created. The existing source-split container was not modified. No test
process remains running. Cleanup of this execution's compute is complete; only
the evidence described above is retained.

Next: finalize the field/API contracts and implement section 6 maintenance tooling
(validate, rehearse-migration, apply-migration, status, scoped cleanup). Then
capture live inventory, add migration tracking, and rehearse consolidation before
production cutover. Potential matches, framework retirement, production release,
IAP acceptance, and user review remain unfinished. No production mutation or
deployment was performed in this milestone.

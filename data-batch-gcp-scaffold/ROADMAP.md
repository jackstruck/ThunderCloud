# Current status and follow-up work

Reviewed 2026-09-15 against the current implementation and dated release records.
The schema 024 cutover is historical; the current schema is **026**. See
[Operations](OPERATIONS.md) for procedures and [History](docs/history/README.md)
for release evidence. This list is not an instruction to replay completed work.

## Implemented

- Shared durable runs for browser and archive submissions, including retries,
  cancellation, reconciliation, and encrypted storage.
- Explicit-video acquisition and cloud handoff. The separate JustPaste/Luluvid
  end-to-end acquisition pilot has not been established as complete.
- Exact-content enrollment reuse and alternate source URLs (schema 025).
  A duplicate video does not always create new subjects.
- Reversible subject exclusions and disabled examples (schema 026).
- Subject/source browsing filters, privacy view, and data terminology in the console.
- Artifact Registry cleanup: seven-day unprotected history, five recent versions,
  and explicit deployed/rollback image pins. This policy is already enabled.

## Remaining operational work

- Complete the remaining IAP/browser acceptance checks identified in the
  [cutover review](docs/platform-cutover-review.md). No browser test is launched
  by this repository cleanup.
- Replace the temporary single-user IAP grant with an approved group when team
  access is required.
- Measure concurrency, latency, GPU allocation, database load, interruption
  recovery, and per-run cost before increasing operational limits.
- Establish private-only Cloud SQL administration before removing its public
  endpoint.
- Keep deployed and rollback image pins synchronized on every release, and
  verify actual repository storage savings after cleanup cycles.
- Review the separate acquisition pilot and current queue status before scheduling
  new work. Historical queue estimates are not current counts.

Retention decisions for operational history, local review artifacts, and stored
records remain a separate task. This cleanup does not change retention or delete
anything under `.local/` or `data/`.

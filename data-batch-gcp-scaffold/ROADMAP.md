# Current status and follow-up work

Reviewed 2026-10-11 against the current implementation and dated release records.
The schema 024 cutover is historical; the current schema is **027**. See
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
- Durable import queue, bounded GPU admission, and parallel profile acquisition
  (schema 027); the performance pilot is complete, while the original import's
  final outcome requires a current operational check.
- Subject/source browsing filters, privacy view, and data terminology in the console.
- Artifact Registry cleanup: seven-day unprotected history, five recent versions,
  and explicit deployed/rollback image pins. This policy is already enabled.

## Remaining operational work

- Check live import, queue, and retry status against the latest failure export and
  recorded recovery decisions before launching additional work. Do not assume a
  dated completion estimate or pending count still applies.
- Complete the remaining IAP/browser acceptance checks identified in the
  [cutover review](docs/history/2026-09-10-platform-cutover-review.md), if they are still open after
  checking current evidence.
- Replace the temporary single-user IAP grant with an approved group when team
  access is required.
- Measure concurrency, latency, GPU allocation, database load, interruption
  recovery, and per-run cost before increasing operational limits.
- Establish private-only Cloud SQL administration before removing its public
  endpoint.
- Keep deployed and rollback image pins synchronized on every release.
- Review the separate acquisition pilot and current queue status before scheduling
  new work. Historical queue estimates are not current counts.

Treat this list as follow-up candidates until current cloud state and ownership
are verified. Completed release details belong in [dated history](docs/history/README.md).

# Historical source matching — 2026-09-16

Added an explicitly scoped, resumable historical backfill using the existing
complete-linkage cosine 0.5 algorithm. Sources with successful merge history are
protected, even if the merge was later separated; failed attempts do not count.
Already automatically matched sources are excluded. Each source is checked again
under the shared enrollment lock, and matching/report persistence is atomic.
Examples are preserved and their count is asserted before commit.

Deployment: CPU job `face-source-merge-backfill`, image
`sha256:ca7f9ddf2e3f451453c1b49949def2aff19dc1238ee6dc341234539bb0ab17a2`.
Terraform plan contained only creation of this job. No schedule is installed.

Backfill ID: `f895dca4-ffd8-4466-91b3-47381d7764e6`.
Cutoff: `2026-09-16T11:44:08.088520+00:00`.
Encrypted report:
`gs://teak-banner-dome-bulk-videos/batch-control/hotscope-20260914/historical-matching/f895dca4-ffd8-4466-91b3-47381d7764e6.json`.
Inventory execution `face-source-merge-backfill-ldx5k` succeeded, selecting 2,967
sources / 47,160 subjects and excluding 84 sources with prior merges. The largest
selected source had 209 subjects (within the existing 400-subject limit).

Validation: five dedicated real-PostgreSQL tests cover preservation/idempotency,
manual merge exclusion and recheck, separated merge exclusion, transactional
rollback, cutoff/automatic-pass exclusion, and failed merge eligibility. Existing
automatic-matching tests, Ruff, Terraform validation, and diff whitespace checks
also passed.

Apply execution: `face-source-merge-backfill-4xx27`.
The broader selected subject-management integration suite also passed (53 tests).

The first apply execution was stopped after 22 checkpointed sources to optimize
merge-history reads. The worker caches protected sources while the count/latest
time of other combine/move events remains unchanged; the check runs under the
shared advisory lock. Its own source-local merges cannot affect untouched source
eligibility. Tests prime this cache before manual merge/separation to verify
invalidation. The replacement image resumes the same inventory and reports.

Live diagnostics showed database I/O during donor subject updates. Bulk combine
now clears an emptied donor's vector/count in the redirect update, avoiding an
unnecessary insertion of its old embedding into HNSW before recalculation.
Recalculation, versioning, reversible audit snapshots, and transaction boundaries
are unchanged. History provenance uses separate indexed UNION queries. All 53
selected integration checks passed again after these changes. The second apply
execution (`face-source-merge-backfill-zs5r2`) was stopped at 34 checkpointed sources
(147 merged groups; 314 subjects combined; no errors) before the optimized resume.

Optimized resume execution: `face-source-merge-backfill-snl7d`. Confirmed processing
beyond the saved checkpoint with no errors; this managed execution continues in
the background. The encrypted report above is the authoritative live progress and
completion record. Its `summary` is finalized at the end of a pass; while a pass
is running, count the individual `outcomes` entries for current progress.

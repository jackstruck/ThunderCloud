# Remaining work

Historical processing and gallery backfill are complete by user acceptance on
2026-09-06. The items below are not implied complete by that acceptance.

- Implement the automatic JustPaste → Luluvid → download → verified CSEK upload →
  durable enqueue handoff with an `uploaded_not_enqueued` receipt. Validate the
  `ah7w2`, `c3bec`, and `6tg6b` pilot, existing upload, and duplicate-content cases.
- Review the exact unresolved new-URL queue, process it through the accepted permanent
  path, reconcile its results, and agree an observation period. The old 95–98 estimate
  is not a current count.
- Complete real-browser IAP acceptance for link ingestion, uploads, cancellation,
  retries, cleanup, recovery, and guarded arbitrary public media URLs.
- Replace the temporary single-user IAP grant with an approved group.
- Add authorized identity lookup and assignment/editing. Operator subject merge and
  split-group commands already exist in `face-gallery-correct`.
- Calibrate matching and clustering with labeled same-person/different-person cases.
- Measure concurrent runs, latency, GPU allocation, database load, interruption,
  safe parallelism, and per-run cost before increasing limits.
- Approve retention policies for embeddings, operational history, backups, and local
  review artifacts. Protect deployed and rollback image digests before enabling
  Artifact Registry deletion policies.
- Establish private-only Cloud SQL administration before removing its public endpoint.
- Replace deprecated ByteTrack construction.

Track teardown completion and remaining deployment gates in
[the cleanup checklist](../cleanup_opportunities.md).

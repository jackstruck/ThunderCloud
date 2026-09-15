# Historical records

Historical processing and gallery backfill were accepted as complete by the user on
2026-09-06. No new corpus reconciliation was requested or performed.

Superseded design plans, the Phase 1 review, and completed selection lists were
removed on 2026-09-15 with user approval. Tracked originals remain in Git history.
The dated release and cutover records are retained for rollback and audit context;
their counts, digests, and paths describe the original revisions.

Current guidance lives in [README](../../README.md),
[Architecture](../../ARCHITECTURE.md), [Operations](../../OPERATIONS.md), and
[Roadmap](../../ROADMAP.md). See [cleanup results](../cleanup-candidates.md).

Sensitive original inventories, process manifests, gallery sample and execution
reports, retired source, and SHA-256 records are preserved locally under
`data/history/2026-09-06-backfill/` (ignored by Git, directory mode 0700). The original
append-only acquisition manifest, resolution checkpoint, Terraform state, encryption
keys, and models remain in place. Do not delete immutable archive or gallery objects.

See `2026-09-06-teardown.md` for teardown actions and remaining gates.

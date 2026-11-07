# Historical records

Historical processing and gallery backfill were accepted as complete by the user on
2026-09-06. No new corpus reconciliation was requested or performed.

Superseded design plans, the Phase 1 review, and completed selection lists were
removed on 2026-09-15 with user approval. Tracked originals remain in Git history.
The dated release and cutover records are retained for rollback and audit context;
their counts, digests, and paths describe the original revisions.

The [within-source matching design](2026-09-15-within-source-bulk-matching-design.md)
is an archived proposal. Its implementation and current controls are documented in
[source merges](../source-merges.md).
The [platform contracts](2026-09-10-platform-contracts.md) and
[cutover review](2026-09-10-platform-cutover-review.md) describe the schema 024
release and acceptance at that time; they are not current configuration guides.

Current guidance lives in [README](../../README.md),
[Architecture](../../ARCHITECTURE.md), [Operations](../../OPERATIONS.md), and
[Roadmap](../../ROADMAP.md). See [cleanup results](../cleanup-candidates.md).

The ignored local evidence bundle formerly under
`data/history/2026-09-06-backfill/` was deleted on 2026-10-12. It contained
original inventories, process manifests, gallery samples, execution reports,
retired source, reviewed plans, and SHA-256 records; those files are no longer
available in this checkout. The dated tracked summaries remain here. The separate
acquisition manifest, resolution checkpoint, Terraform state, encryption keys, and
models were not part of that deletion. Immutable archive and gallery objects were
not deleted.

See `2026-09-06-teardown.md` for teardown actions and remaining gates.

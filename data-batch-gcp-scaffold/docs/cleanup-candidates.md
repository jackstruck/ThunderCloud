# Repository cleanup candidates

Reviewed 2026-09-15. Following user approval, the documentation and completed
selection files in the table below were deleted, along with the ignored local
acquisition plan. The Python scripts remain pending a separate code-removal decision. Evidence comes from tracked-file references, entry points,
imports, and the current run framework. An absence of repository references does
not prove a script is unused by an external operator.

## Documentation removed and remaining code candidate

| Paths (relative to this application) | Evidence | Suggested treatment |
| --- | --- | --- |
| `docs/history/IMPLEMENTATION_PLAN.md`, `docs/history/BULK_DOWNLOAD_BACKFILL_PLAN.md`, `docs/history/planning.md`, `docs/history/phase1-review.md` | The history index explicitly marks the original plans and review as historical, not current operating instructions. | Remove if Git history is sufficient; retain the index and useful release evidence. |
| `docs/history/selections/cloud-run-controlled-r4.txt`, `cloud-run-remaining-20260902.txt`, `cloud-run-termination-test-r4.txt` (all in that same directory) | Completed historical selections, explicitly not to be replayed according to `selections/README.md`. No current code references found. | Remove together if their audit value is covered by Git history; update the selection/history indexes. |
| `docs/history/source-split-20260909.py` | An archived one-off correction script. It imports `reconcile_gallery`, which is no longer defined in `worker.subject_management`. No current launcher or test references found. | Strong code-deletion candidate. Keep its historical narrative if needed; do not treat it as a supported tool. |
| `docs/phase1-review.md` | A forwarding document containing only a link to the archived review and current guides. No incoming tracked references found. | Remove if preserving old external bookmarks is unnecessary. |
| `commands.md` | A short forwarding page to `OPERATIONS.md` and `FUTURE_VIDEOS.md`. | Optional deletion; small navigation convenience rather than obsolete implementation. |

The suggested treatments above record the original review; documentation removal
is now complete. The archived Python script was retained because this approval
covered documentation.

## Candidate requiring an operator check

`scripts/select_remaining.py` compares manifest items against successful historical
`processing_job` records. Current submissions use the common run framework, whose
records live in `media_run`. The script has no tracked caller or tests and is not
listed in the current operating guide. The historical table still exists, so this
is not a broken-table claim; it can give an incomplete view of processing performed
through the current framework. Confirm no external operator still uses it before
removing it. Do not use it to decide whether current runs need to be replayed.

## Historical evidence worth retaining selectively

The dated `docs/history/2026-*.md` release notes are historical, but contain deployed
image digests, verification results, cutover decisions, and rollback context.
Keep the recent deployment/cutover records; older records can be pruned after deciding
how much operational history should remain in the checkout. The current history
index links the active architecture, operations, and roadmap documents.

The ignored local `docs/design/hotscope-acquisition.md` plan was also deleted.
It was not a runtime dependency. Unlike the tracked documents, this local plan
is not recoverable from repository history.

## Keep: old names do not mean dead code

- `migrations/001_*.sql` through `026_*.sql`, the bootstrap schema, migration tests,
  and `maintenance/`: required for reproducible database setup, checksum validation,
  upgrades, and preservation checks. Do not delete historical migrations.
- `openapi/phase1.yaml`: the filename is old, but it is the linked API contract and
  includes current matching-exclusion endpoints.
- `worker/manifest.py`: still used by submission/acquisition code; the historical
  selector being a deletion candidate does not make its dependencies obsolete.
- `scripts/run_local.sh` and `scripts/run_local_container.sh`: current documented
  wrappers. Short wrappers are not automatically dead code.
- Model/provenance checks, integration tests, and database history tables: retain
  until a separate dependency and retention review establishes a safe change.
- Ignored `.env`, `.secrets`, Terraform state, models, manifests, private receipts,
  and `data/history/`: operational or sensitive state, outside this deletion review.

## Documentation drift found

The main README and operations guide still described schema 024 as current; they
now identify schema 026. The roadmap still contains older acceptance and rollout
items, so it should be reconciled against current evidence before deleting it or
using it as a current work queue. In particular, exact duplicate enrollment and
registry cleanup are implemented in later releases.

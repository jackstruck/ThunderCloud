# Repository cleanup record

Reviewed and applied on 2026-09-15 with user approval. `.local/` and `data/`
were excluded from cleanup; their contents and retention are unchanged.

## Removed

The 2026-10-11 cleanup also removed the completed
`docs/performance-implementation-plan.md` after retaining its rollout and capacity
decisions in the dated performance release record and `OPERATIONS.md`. The duplicate
root `user-links.txt` was removed; `bulk-download/input/hotscope_users.txt` remains
the downloader input. `SESSION_HANDOFF.md` became a new-developer start guide; its
older operational detail remains in Git history. The full-sweep example gallery
repair plan remains available. The ignored within-source matching proposal moved
to `docs/history/2026-09-15-within-source-bulk-matching-design.md`, clearly marked
as historical. Current instructions remain in `docs/source-merges.md`.
Recreatable local environments, SDK files, Terraform provider downloads, and an old
partial media download were cleared. A later `.local/` cleanup removed only the
recreatable `diagnose-env/` and generated `source-merges-provider-schema.json`.
The protected CSV and other private operational records remain.

| Files | Reason |
| --- | --- |
| `docs/history/IMPLEMENTATION_PLAN.md`, `BULK_DOWNLOAD_BACKFILL_PLAN.md`, `planning.md`, `phase1-review.md` (all under `docs/history/`) | Superseded plans and initial review. Tracked originals remain in Git history. |
| The three `docs/history/selections/cloud-run-*.txt` lists | Completed historical selections, not current work queues. |
| `docs/phase1-review.md`, `commands.md` | Redundant forwarding documents. |
| The ignored root `docs/design/hotscope-acquisition.md` | Superseded local acquisition plan; it was not tracked in Git. |
| `docs/history/source-split-20260909.py` | One-off archived script importing the removed `reconcile_gallery` helper. |
| `scripts/select_remaining.py` | Historical selector using `processing_job` rather than current `media_run` records. No active repository caller, matching process, or user-cron reference found; remaining references were historical reports. |
| `FUTURE_VIDEOS.md` | Its unique immutable-upload instructions moved to `OPERATIONS.md`; receipt submission, retries, and monitoring already existed there. |
| `build/`, `thundercloud_face_batch.egg-info/`, `.pytest_cache/`, `.ruff_cache/` | Regenerable build outputs, obsolete package metadata, and caches. The current editable package metadata and environment were retained. |

The absence of local callers cannot establish whether an external operator holds a
copy of an old script. The removed tracked code remains recoverable from Git history.
Historical release notes retain their original descriptions of these scripts.

## Consolidated

- `README.md` is the starting point and links to the authoritative guides.
- `OPERATIONS.md` owns installation, uploads, submission, deployment, and recovery.
- `ARCHITECTURE.md` describes components, ownership, and current schema 026 behavior.
- `ROADMAP.md` separates completed capabilities from remaining operational work;
  duplicate enrollment and registry cleanup are no longer described as future work.

## Build context exclusions

`.dockerignore` now excludes local virtual environments, generated package/build
files, test caches, operator data and receipts, local environment files, and nested
Terraform state. Required model files and verification scripts remain included for
the GPU Dockerfile. Maintenance builds use their own explicitly staged context,
including tests, and do not copy this `.dockerignore`.

## Preserved

- `.local/`, `data/`, private receipts, manifests, models, keys, and Terraform state.
- Database migrations, bootstrap schema, maintenance tooling, and migration tests.
- `openapi/phase1.yaml`, which remains the current linked API contract.
- Shared manifest parsing and the documented local submission wrappers.
- Dated release/cutover records containing verification and rollback evidence.
- Deployed cloud resource names, database identifiers, and application behavior.

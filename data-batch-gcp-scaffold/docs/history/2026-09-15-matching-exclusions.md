# Reversible matching exclusions — 2026-09-15

Subjects can be excluded from matching and restored explicitly. Subject browsing
hides them by default; Include excluded works in the library and source/manual
pickers. Direct subject URLs remain accessible. New run matching, potential matches,
and source-group proposals omit excluded subjects. Iterative HNSW scanning continues
past filtered neighbors.

Examples can be disabled as Not a face or Unusable face, individually or in batches
of up to 1,000, and restored. Disabled examples remain reviewable with their previews
and immutable origin/embedding data, but canonical subject vectors and sample counts
use active examples only. Zero active examples makes the subject ineligible. Current
candidate/default subject galleries omit disabled examples; historical scores and
enrollment evidence are preserved.

Subject-version checks, actor-bound idempotency and audit records protect changes.
Any excluded merge member excludes the combined subject regardless of the survivor.
Moves/splits conservatively inherit exclusions, and disabled status travels with the
example. Duplicate submissions reuse this state and cannot reactivate bad content.
Maintenance preservation receipts include flags and active-only aggregate invariants.

Migration 026 applied successfully with runtime grants; manifest checksum:
74f20b258c91cf3ee627721dad89c3f18c0be6695bf04737fa69236cfa61a68a.

Validation included the non-browser subject-management regressions, dedicated
exclusion/disabling/merge/restoration/idempotency/duplicate/source-proposal checks,
migration acceptance, API origin/version/filter forwarding, database query tests,
and 16 Node DOM/import tests. The local PostgreSQL container was capped at half a CPU
and removed afterward. Production status-field reads were checked without changing
classification on any real subject or example.

Cloud Build 654dc0ed-53f3-4ce8-8ebd-689f92cc2e0a built the release remotely. Ingest,
GPU and acquisition job templates were updated before the console controls. Registry
pins and Terraform image settings track the release and rollback images. IAP and
100% latest-revision routing remain enabled.

- worker: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:d1d4768347123f6a7889093c0cc9b45d6730d833c07fdf1417a7875842f67282`
- acquisition-batch: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/acquisition-batch@sha256:010bc83b1d7fa134bfdc24e721e31cb4e44b4e978be85648525420b15f5600f2`
- console: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:c2f4e7ff6c6dddc4c4f27fadbe750db1d3e6c28b758f1892c719c6277b5dbd6e`

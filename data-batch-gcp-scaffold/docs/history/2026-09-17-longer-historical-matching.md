# Longer historical matching pass — 2026-09-17

Queued a 120-second-per-source scan of the 410 sources left as
manual_review/background_scan_required by the original historical backfill.
Cosine remains 0.5; the 400-subject and 50-member group limits remain unchanged.
Prior successful/manual merges are protected by a fresh check under the shared
lock. The original report is preserved and all examples remain retained.

Backfill: 495dc6b4-58e1-4e2a-99c9-d36120ffeea7.
Retry of: f895dca4-ffd8-4466-91b3-47381d7764e6.
Execution: face-source-merge-backfill-sc64j.
Report: gs://teak-banner-dome-bulk-videos/batch-control/hotscope-20260914/historical-matching/495dc6b4-58e1-4e2a-99c9-d36120ffeea7.json.
Image: sha256:7d5ae417c7ea8872cd72a687816e92327b6e78be12dd0dacd124b319c3825710.
The job image was updated directly, with the local Terraform image variable kept
in sync. Execution environment was read back to verify its budget and scope.

Validation: 11 focused PostgreSQL integration tests passed, including a simulated
11-second scan that times out at the default budget but succeeds on the longer
pass, idempotent replay, mismatched retry scope, and a manual merge between passes.
Ruff and whitespace checks passed. The managed execution continues independently;
its report is the authoritative progress/completion record.

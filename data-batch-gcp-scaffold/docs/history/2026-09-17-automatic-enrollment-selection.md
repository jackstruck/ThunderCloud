# Automatic enrollment selection and cosine 0.4 — 2026-09-17

New retain_and_enroll and enroll_only submissions now persist all_tracks selection
when created, including URL and file uploads. Search-only submissions keep manual
selection by default; explicitly unattended operator/batch search remains supported.
Existing paused runs retain their recorded selections. The UI explains the policy
behavior instead of claiming every enrolled group requires confirmation.

The interactive GPU job now uses FACE_SOURCE_MERGES_AUTO_THRESHOLD=0.4 for
new-source enrollment. The model image, gallery-search behavior, merge safeguards,
example preservation and historical backfill executions are unchanged.

Validation: 11 focused real PostgreSQL integration cases and 32 unit cases passed.
Coverage includes both source kinds and all three policies, idempotent creation,
automatic enrollment, search-only pausing, explicit manual enrollment overrides,
existing unattended archive imports, atomic automatic merge retries and preserving
all examples. A cosine 0.45 pair merges under the 0.4 setting while a dissimilar
third track remains separate. Ruff and git diff --check passed.

Console/ingestion image:
sha256:336142c8050743344341205dc21924ebe2d4ad8b3cd1003e0ce2902abde39468.
Console revision face-console-00049-sv4 serves 100% of traffic. Live UI text,
ingestion image and GPU threshold were verified; the GPU image was unchanged.
Terraform image and threshold inputs match deployment. Temporary test PostgreSQL
and deployment credentials were removed. No historical matching was started.

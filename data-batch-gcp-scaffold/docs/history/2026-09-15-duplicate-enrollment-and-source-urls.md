# Exact-content enrollment reuse and source URL provenance — 2026-09-15

Both enrollment paths now reuse existing examples for a verified duplicate SHA-256,
without allocating new subjects or publishing gallery crops. The global enrollment
transaction lock also serializes retained-media promotion, so simultaneous duplicate
runs do not create multiple retained copies. Retain after enroll-only can promote
media onto the existing source. This preserves the first enrollment selection rather
than adding newly selected tracks on a duplicate run. Different bytes remain distinct.
Historical duplicate subjects are not automatically merged.

Migration 025 records URL provenance by content hash, recovers source and completed
run links, and exposes a shared source_page_link view. Both source and subject APIs
return aliases; source search and hostname filters search all of them. Existing
candidate snapshots keep their rank/score but also expose current source URLs.
Duplicate results show current subject membership and galleries after manual merges.
The cloud acquisition worker records aliases even when skipping an existing handoff.

Matching-group application reloads source manual controls and source-list counts,
clears manual selections and stale survivor choices, and preserves reviewed operation
outcomes for audit/retry. Reload runs after partial and uncertain outcomes too.

Validation: the non-browser subject-management integration suite and targeted
interactive/run-navigation/console tests passed; migration acceptance passed;
additional mixed-policy, simultaneous-duplicate, and changed-content tests passed;
14 Node DOM/import tests passed. No Chromium was used. Production migration and
runtime grants applied successfully at version 25 (manifest checksum
27225bf2e537f49105737dfb360853ea33309b98be67d4143f52f68a528f7053).

A local GPU image extraction caused Codespace CPU pressure. That build was cancelled,
the test database capped then stopped and removed, and remaining images built in
Cloud Build (721a365b-f3b8-401c-a2fa-23a41651a35a). Cloud Build API enablement is
tracked in Terraform. The existing compute service account ran the build; enabling
the API provisioned the standard Cloud Build service identities.
Console revision face-console-00038-nmc retains IAP and 100% latest-revision traffic.
Ingest, GPU, and acquisition job templates were updated without launching extra runs.

Release images:

- console: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:8b52877704f2c35fb26cdda0e3fb8d4bad0c3fb525281f513f1d205f18e1a968`
- worker: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:e3ce4ed2823d0a3d52db5b7a288752538c61006eedf235c46fd77e789999980b`
- acquisition-batch: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/acquisition-batch@sha256:c8391e63912d44eb65902cde9950b71495d170d9094e34f956d88947f63d1d04`

The verified bulk checkpoint contributed 737 URL mappings; the catalog contained
3,430 mappings after backfill. Source e6a5c021-e688-5bf6-8f8c-d34bd2a5607a now
has both known URLs. Production source queries and alias searches were checked
using an IAM-authenticated database connection. Existing cloud acquisition continues; no
additional processing was launched by this release.

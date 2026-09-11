# Runtime consolidation — 2026-09-09

**User adjustment:** New migration audit copies described below were subsequently
removed at the user’s request. Migrations 014–017 now discard redundant fields
after validating retained data. No `platform_migration_record` table is created.
Earlier audit-copy statements describe the superseded intermediate implementation.

Local release preparation; no production deployment, job deletion or database
migration was executed.

Enrollment now uses the common interactive/run service for browser and operator
archive inputs. It no longer reads matching-enabled/threshold flags, accepts
threshold parameters, or records ranked candidates as enrollment decisions.
Retain-and-enroll saves search candidates independently while creating new subjects.
Migration 013 permits absent threshold labels for new enrollment records; historical
labels remain untouched. Gallery repair uses its dedicated
`FACE_GALLERY_REPAIR_MIN_SIMILARITY` setting and Terraform variable.

Removed the predecessor runtime modules: `worker.process`, `worker.queue`,
`worker.cloud_run_drain`, `worker.ingest`, and the archive rollout CLI in
`worker.cloud_run`. `Database` now provides connection/ranking functions; its
archive writer and automatic-match helper are removed. Obsolete tests for those
paths and their unused local crop export were retired. The administrative ADC
helper moved to `maintenance/gcloud.py`.

Operator entry points are `python -m worker.operator_submission`, `python -m
worker`, and the packaged `face-submit`/`face-worker` commands. Local wrapper
scripts use this receipt-based interface. Existing archive submit/rollout commands
are not part of the new release.

Terraform no longer declares the predecessor `gpu_drain` job or its queue-specific
alerts/outputs/settings. The existing execution-error alert covers the common
interactive and ingestion jobs. Shared identities, network, encryption, registry
and source/gallery storage remain. The live predecessor job currently has deletion
protection: the reviewed cutover procedure must disable that protection and drain
or transfer pending work before applying retirement. No Terraform apply was run.

Focused verification: 11 database/interactive/operator unit checks passed, along
with the operator archive and retain-and-enroll PostgreSQL checks. The checks run
without obsolete matching configuration. Terraform formatting/validation, Ruff,
CLI help, retired-import search, and `git diff --check` passed. No full image build,
full-suite rerun or intermediate production-copy rehearsal was added.

Still required: final schema ownership/provenance normalization, queue data
migration/retirement and audit mappings, remaining completed-backfill retirement,
cloud maintenance execution/reporting, integrated acceptance and reviewed cutover.
The existing protected backup and deployed image digests remain recovery evidence.

## Gallery membership consolidation

Migration 014 records every gallery's former subject, source and source-track
fields in the administrative `platform_migration_record` table before dropping
those duplicate columns. Active galleries must link to an example with matching
ownership before migration. Retired, unlinked galleries retain their historical
evidence. Runtime roles cannot write the audit table; reviewed apply checks
inherited privileges as well as explicit grants.

Gallery reads, publication, retention and repair now resolve ownership through
`subject_example`. Saved-result gallery reads keep a five-image bound per candidate.
Enrollment details likewise read current subject/source membership from examples.
The old gallery repository and completed example-backfill entry points are removed.
The completed source-split helper is retained only as inactive historical evidence
in `docs/history/source-split-20260909.py`.

Focused PostgreSQL checks verified nonempty migration preservation (including a
retired gallery without an example), equality with fresh installation, runtime
audit restrictions, reviewed-apply recovery, saved-result versions and scores,
and enrollment details despite a conflicting duplicate legacy owner. Earlier
focused checks covered moved-example gallery repair and verified historical model
labels. Ruff passed. No production migration, image rebuild or full-suite run was
performed for this intermediate change.

## Current membership columns

Migration 015 preserves `face_track.subject_id` and
`submission_enrollment.subject_id` in administrative records, then removes them.
It refuses to proceed if any assigned track or enrollment lacks matching example
membership. Corrections now update only examples, and enrollment no longer writes
a duplicate subject owner. Origin records remain available for timing, model and
processing evidence. Coverage reports count enrolled tracks through examples;
the completed-backfill `missing_tracks` / `eligible_tracks` fields are retired.

The focused migration fixture includes both track and submission origins, active
and unlinked retired galleries, and verifies identical logical receipts across
the migration plus equality with fresh installation. Focused move, merge,
retain-and-enroll and historical-model checks passed against schema 015. Saved
enrollment details follow current example membership after a merge. Ruff and
`git diff --check` passed. Source normalization, redundant enrollment evidence,
existing-destination assignment fields and old queue retirement remain unfinished.

## Enrollment assignment contract

Migration 016 archives old assignment destination/target values and removes those
columns. An unfinished existing-subject assignment stops migration for explicit
disposition; completed historical assignments keep their group membership and
audit evidence. The API now accepts only `assignment_id` and `group_ids` for an
enrollment group. Browser grouping and the worker use this same contract, with no
existing-subject branch. Moves and merges remain correction operations.

Focused checks cover grouping 28 tracks under both enrollment policies, replay,
atomic rejection of retired request fields, conflicting membership, and Chromium
group editing/confirmation. The migration fixture includes a historical assignment
and checks preservation with fresh-schema equality. Full-suite validation remains
deferred to the integrated release.

## Enrollment evidence consolidation

Migration 017 moves enrollment outcome and timestamp onto the retained face-group
record and removes `submission_enrollment`. Runtime enrollment, result details,
ranking evidence and coverage use face groups and examples. Obsolete candidate,
threshold and duplicate source fields are discarded without audit copies.
The focused migration preservation check compares retained enrollment information
and verifies that neither the old enrollment table nor a migration audit table
exists in the final schema.

## Archive queue retirement

Migration 018 drops `processing_work_item` and `processing_rollout` after checking
that no pending, leased or retry work remains. It creates no audit copies. The
existing deployed worker must drain the predecessor queue during cutover
preparation; the earlier inventory included one archive retry, so retirement is
not yet ready to apply to production. Refresh this inventory before the window.
Terminal queue records are discarded; retained processing jobs and tracks remain
origin evidence. Runtime grants no longer mention either queue table.

Reviewed apply checks the predecessor queue when it exists and supports resuming
after retirement when it does not. Focused PostgreSQL checks passed for rejection
with a retry item, preserving that item on failure, retirement after completion,
replay, and reviewed-apply checkpoint recovery. No live queue was changed.

## Explicit external attribution

Migration 019 moves source page attribution from the old metadata keys into
`source_asset.source_page_url` and removes those keys. Subject details, source
groups, examples, potential-match summaries, ranking and new enrollment use the
explicit field. The former page-url precedence is preserved during migration;
other metadata remains intact. No migration audit records are created.

The focused migration fixture verifies attribution and unrelated metadata survive
the transformation, alongside the existing retained-data checks. Grouped
enrollment under both policies and retain-and-enroll result checks passed.
Explicit storage bucket/object/generation normalization remains to be completed.

## Managed object bucket

Migration 020 adds `source_asset.object_bucket`, requiring a bucket whenever an
object name is present. New retained enrollments record the storage repository's
bucket; gallery repair reads that bucket from the source row. Existing managed
objects require the explicitly supplied `source_bucket` migration input, exposed
as `--source-bucket` by the migration script and bound into the reviewed apply plan.
Missing configuration stops migration. No migration audit copies are added.

Focused enrollment, gallery repair and interactive checks passed. Remaining work
includes normalizing archive URI/generation fields, carrying reviewed bucket
configuration through the protected-copy rehearsal, verifying bucket values in
preservation receipts, and reconciling cleanup's configured-bucket assumptions.

The reviewed bucket now passes through local rehearsal's `--source-bucket` option,
the container command, recovery replay and the rehearsal receipt. Reviewed apply
requires the same bucket in its checksum-bound plan. Source preservation includes
the actual stored bucket; baseline rows without that column use the explicitly
reviewed value. Missing configuration and changed bucket values fail verification.
Focused bucket migration/preservation, reviewed-apply recovery and backup checks
passed. No intermediate full-copy rehearsal was run.

## Retained-source cleanup bucket

Migration 021 adds the bucket to retained cleanup entries, using the corresponding
source row or the explicitly reviewed migration bucket. New source tombstones
copy the recorded bucket into cleanup work. The maintenance worker carries it
through to deletion, which refuses a different configured bucket before accessing
storage and still requires an exact positive generation and managed-media prefix.

Focused checks passed for source tombstone-to-cleanup propagation, bucket mismatch
refusal without a storage call, exact-generation deletion, and maintenance retry
behavior. Archive reference normalization and the integrated migration rehearsal
remain outstanding. No live objects were deleted.

## Explicit archive references

Migration 022 normalizes archive bucket, object path and exact generation into
the same explicit storage columns used by managed media, with `storage_kind`
distinguishing archive originals, managed copies and sources without retained
media. It rejects incomplete archive references and removes the metadata
generation fallback. Gallery repair and retained-source coverage use these
fields directly. Managed-content deduplication and retained-source deletion are
restricted to managed copies, so archive originals do not enter that cleanup path.

Focused checks passed for archive reference/metadata preservation, rejection of
archive deletion without queueing cleanup, managed enrollment under both policies,
gallery repair and cleanup. The focused test fixture's connection ownership was
corrected after a teardown-only failure. Ruff passed. No audit copies or live
storage mutations were added. Final scale/rehearsal verification remains pending.

## Normalized example models

Migration 023 applies existing verified model mappings to example model labels.
Original labels remain on retained tracks/jobs and existing verification evidence
is preserved. The migration temporarily disables the example-origin trigger only
within its atomic administrative transaction, restores it, and checks that every
normalized example model agrees with its subject representation. It creates no
new audit records and does not change embeddings or subject versions.

Normal recalculation, gallery repair and coverage now read `example.model_version`
directly, with no runtime verification-table joins or legacy-label fallback.
Focused migration checks verify normalized labels, unchanged original labels and
logical data preservation. Historical-model and moved-gallery repair checks passed,
as did Ruff and whitespace validation. Integrated scale/rehearsal validation and
cloud maintenance execution/reporting remain unfinished.

## Consolidated local checks

Migration 024 removes obsolete matching-policy/threshold fields from media runs
and retained processing-job evidence. Runtime selection no longer writes the
retired policy label, and the unused local face-output setting is removed.
The consolidated unit/migration checks and the complete subject/browser module
passed against the current local schema. Ruff and whitespace checks passed.
Registry configuration retains deployed and rollback image digests while aging
unreferenced history. Container validation and protected-copy rehearsal remain
required before any cutover claim.

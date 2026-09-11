# Platform architecture

This describes the consolidated release deployed on 2026-09-10, using schema 024.
See [cutover results and acceptance status](docs/platform-cutover-review.md).

## Inputs and processing

Browser uploads, supported links, and operator archive receipts enter the same
`media_run` and durable operation framework. Bulk submission creates multiple ordinary
runs with explicit handling and selection policies. Receipt request keys make a
retried operator handoff idempotent.

The IAP-protected `face-console` service handles browser requests. The
`face-ingest-drain` CPU job acquires media; `face-interactive-gpu` performs detection,
tracking, quality selection, embedding, and the requested search or enrollment.
Leases, retries, cancellation, and scheduled reconciliation belong to the shared
run framework. There is no separate archive writer or rollout/work-item queue in
this release.

SCRFD detection, ByteTrack tracking, and fixed AdaFace embeddings produce face
tracks and aggregate embeddings. These are inference operations, not model training.
Search compares compatible subjects and records similarity-ranked snapshots.
Enrollment creates new subjects from manually selected groups or, for unattended
`all_tracks` selection, one subject per detected track. Stable assignment IDs and
unique example origins prevent duplicate enrollment after retries. Similarity does
not decide enrollment ownership; explicit corrections move examples or merge subjects.

## Ownership and evidence

`subject_example` is the sole authority for current subject membership. Each example
retains its source, embedding, normalized model version, timing, quality, and immutable
origin in a historical `face_track` or an enrolled `submission_face_group`.
Processing jobs and original model-verification evidence remain retained records.
Runtime membership and model resolution use the normalized example directly.

`subject` holds derived counts and canonical embeddings, optional identity labels,
row versions, and merge redirects. Representations normalize each contributing
example, sum the vectors, then normalize the aggregate. Galleries link to examples
and exact object generations. Corrections change example ownership and recalculate
affected subjects transactionally. Browsing, edits, moves, merges, and the correction
CLI share the subject-management service, version checks, and idempotent correction
history.

## Sources, galleries, and retention

Sources distinguish `managed`, `archive`, and `none` storage. Stored media uses explicit
bucket, object name, and generation; external page attribution is a separate field.
Archive adapters verify the pinned generation, digest, and size. Original archive
objects are never deleted by processing or source cleanup.

`search_then_discard` removes temporary media after a terminal run. `enroll_only`
retains derived enrollment without retaining full temporary source media.
`retain_and_enroll` retains managed source media under encrypted `training-media/`.
Enrollment and search remain separate decisions; retain-and-enroll can record search
results while creating new subjects.

Enrollment publishes representative JPEGs linked to examples and preserves them
through grouping, moves, and merges. Subject and candidate summaries display up to
five images; that display limit does not retire source images. Run results show
the gallery images for their enrolled examples. Publication is transactional; search-only runs never
publish permanent gallery images. Temporary media and selection previews use
`submissions-temporary/`, terminal cleanup, and a seven-day lifecycle fail-safe.
Operational runs expire after seven days. Permanent gallery images use
`subject-gallery/`; retired generations are deleted after their grace period.
Managed-source deletion records a tombstone and queues the exact bucket/object/generation
without removing derived lineage. Embeddings and provenance remain durable pending
an approved retention policy.

## Results and subject review

Saved candidates remain read-only snapshots with their recorded IDs and scores. New
results record the compared subject version. The UI indicates a subsequent subject
change when versions or correction history establish one; otherwise older results
can report that the prior version is unavailable. Merge navigation resolves to the
survivor without assigning historical split scores to arbitrary new subjects.

Subject details retrieve potential matches on demand. The service excludes self,
merged or empty subjects, incompatible models, and current dismissals before limiting
the ordered result to ten candidates, with UUID tie-breaking. Gallery and count
reads are batched. Similarity is not an identity probability. Viewing suggestions
changes no membership; merges reuse the explicit preview and survivor confirmation.
Dismissals are shared, canonical unordered pairs bound to both subject versions,
with actor and time. Restoration is explicit and idempotent. A version change makes
the pair eligible again.

The overview supports UUID/name lookup, source filtering and sorting. **Check a run**
contains manual run lookup and the authenticated principal's recent unexpired runs.

## Ephemeral local face search

`face-probe` accepts one local JPEG/PNG image, one local MP4, or a directory of
previously detected face crops. Detection, tracking, quality selection, and embedding
run locally. The gallery query runs inside an explicitly read-only Cloud SQL
transaction.

The command returns:

- one result group per detected image face or retained video track;
- deterministic top-K compatible subjects and cosine similarities;
- the nullable operator-managed `identity.display_name`;
- every current succeeded source-video observation for each candidate; and
- private local review crops mapped to their result group.

It does not upload probe data or retain probe media, embeddings, results, request
metadata, or review crops server-side. Private local JSON and review crops remain under
the developer's control until locally deleted. It does not create or modify subjects
or identities and does not make an automatic identity decision. Review directories use
mode `0700`; files use `0600`.

Limits are JPEG/PNG up to 25 MiB and 50 million decoded pixels, or MP4 up to 250 MiB
and 15 minutes.

## Security and deployment

Browser access uses IAP and established origin, mutation, and rate protections.
Public HTTPS acquisition validates DNS answers, redirects, and connections, rejects
private/reserved destinations, and pins connections to validated addresses.

Archive, staging, retained sources, and galleries use the configured encryption
controls. CSEK material comes from Secret Manager into memory and is excluded from
logs, database rows, command arguments, and Terraform state. Storage has uniform
bucket access and Public Access Prevention. Cloud SQL uses CMEK, IAM authentication,
connector-managed TLS, and private networking. Runtime service identities and job
invocation permissions remain scoped to their tasks.

The ordered migration runner uses checksummed versions, an advisory lock, verified
baseline adoption, and the same migrations for fresh setup and upgrades. Migration
022 requires exact archive generations; absent provenance must be resolved before
cutover. The maintenance workflow rehearses on an isolated backup copy and compares
schema, grants, data preservation, and recovery. Migration audit copies of retired
fields and tables are not created. Protected backups, verification receipts, and
existing correction history retain their separate purposes.

Deployment requires pausing submissions, handling outstanding work, migrating and
deploying the final components together, and verifying access and processing before
reopening. Recovery restores matching application images and the pre-cutover data.
See [operations](OPERATIONS.md), [contracts](docs/platform-contracts.md), and
[maintenance procedures](docs/history/2026-09-09-platform-maintenance.md).

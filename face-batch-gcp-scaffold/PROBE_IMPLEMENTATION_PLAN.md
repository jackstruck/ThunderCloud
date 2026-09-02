# Persistent Local Probe Matching — Implementation Handoff Plan

> **Superseded scope:** The operator decisions recorded in `decisions.md` replace this
> document's durable-media and durable-result design. The approved implementation is a
> local-only, ephemeral, read-only gallery search: no GCS probe upload, probe database
> tables, threshold decision, request-key persistence, `show` command, probe IAM, or
> cloud write test. This document remains as historical design context; where it
> conflicts with `decisions.md`, the approved decisions control.

## Purpose

Implement a supported local command that accepts an image or short video, detects its
faces, generates embeddings with the same approved models as training, searches the
existing Cloud SQL subject gallery, and persists both the CSEK-encrypted probe media
and a versioned snapshot of ranked results.

The initial interface is local: inference runs in the developer environment using
developer ADC and the Cloud SQL Connector. Cloud SQL and GCS remain remote so the probe
and its results survive the local session and remain available to a future remote probe
service.

This plan refines the conceptual “Persistent probe search” section of
`IMPLEMENTATION_PLAN.md`. That section is a requirement source, not evidence that the
feature exists.

## Handoff status and operational boundary

As of 2026-09-02, no probe implementation exists:

- there is no `face-probe` entry point;
- there are no `probe`, `probe_face`, or `probe_match` tables;
- no code safely uploads or reads durable media beneath `face-probes/`;
- the only current database matcher, `Database._match`, returns one best candidate and
  is embedded in the training commit path, which creates subjects for unmatched faces;
- no probe-specific IAM or retention configuration has been applied.

Do not use `Database.commit_results` for probes. It writes training jobs/tracks and can
create or associate subjects, violating the read-only gallery requirement.

The production training rollout `f01c13b8-318f-4df1-944b-41daa3f67faa` was running
when this handoff was written. Do not poll, restart,
cancel, or otherwise interact with that rollout merely to implement probes. Prefer
waiting for its final reconciliation before applying even additive database changes.

Never read, print, copy, or expose the CSEK value. The existing local key path is
`/workspaces/ThunderCloud/.secrets/gcs-csek.base64`; only code that needs encryption may
read it directly. Never place its value in source, logs, shell arguments, environment
variables, Terraform, database rows, or test fixtures.

## Required behavior

The completed local interface should support:

```bash
face-probe submit --file ./person.jpg --top-k 10 --request-key local-test-001
face-probe submit --file ./short-clip.mp4 --top-k 10 --request-key local-test-002
face-probe submit \
  --gcs-uri gs://teak-banner-dome-bulk-videos/face-probes/PROBE_ID/input.jpg \
  --top-k 10 \
  --request-key local-test-003
face-probe show PROBE_ID
```

`submit` must return machine-readable JSON containing the durable `probe_id`, status,
media kind, detected face/track count, and ranked candidates. Every candidate must
include its source-video observation provenance by default; this is not an optional
flag. `show` must load the persisted match and provenance snapshots rather than
rerunning the current gallery or provenance queries.

For every image face or retained video track:

1. use the current SCRFD detection and AdaFace embedding preprocessing;
2. produce a normalized 512-dimensional embedding;
3. query only subjects with the same embedding-model version;
4. store up to the requested `top_k` candidates in deterministic score order;
5. label the first candidate `matched` only when it meets an explicitly configured,
   versioned probe threshold; otherwise label the face/track `unknown`;
6. return all persisted training observations associated with every candidate subject,
   including original video URI and track timestamps, by default;
7. never insert, update, merge, or delete a `subject`, `identity`, `face_track`, source
   asset, or training processing job;
8. never promote a probe embedding into the gallery implicitly.

Images return one result group per detected face. Videos return one result group per
retained track. A valid image with no detectable face should complete successfully with
zero faces, not create an unknown subject. Invalid or unsupported media should persist
a sanitized failed status.

## Decisions required before implementation

The implementing agent must obtain explicit operator decisions for:

1. Probe-media retention duration. Recommended initial development value: 30 days,
   with explicit audited deletion rather than inheriting the one-day staging rule.
2. Probe-record retention duration and whether it differs from probe media.
3. The probe match threshold and non-placeholder threshold-version label. Do not assume
   the training clustering threshold is calibrated for probe identification.
4. Initial allowed media types and limits. Recommended: JPEG/PNG images and MP4 videos,
   25 MiB maximum for images and the existing video maximum for short clips.
5. Whether result output may include `identity.display_name`. Source-video provenance
   is required by default, but identity names remain excluded unless separately
   authorized.
6. Whether the developer account alone is the initial probe submit/view principal.
   Recommended for the local milestone; split submit, view, delete, and promotion roles
   before exposing a service.
7. Whether local crop export is wanted for probe review. If enabled, keep it opt-in,
   mode `0600`, beneath a probe-specific local directory, and never upload crops.

Do not invent defaults for these security- and interpretation-sensitive decisions in
production. Unit-test constants may be explicit and clearly synthetic.

## Target data flow

```text
local face-probe CLI (developer ADC)
  |
  | allocate probe UUID and idempotency key
  | upload local media, or validate existing probe URI
  v
gs://teak-banner-dome-bulk-videos/face-probes/<probe-id>/input.<ext>
  |  same CSEK, immutable generation, no staging lifecycle rule
  v
shared SCRFD / tracking / quality / AdaFace components
  |
  | read-only pgvector top-k query over compatible subjects
  v
Cloud SQL through public Cloud SQL Connector + IAM database authentication
  |
  +--> probe row
  +--> one probe_face row per image face or video track
  +--> ordered probe_match snapshot rows
  +--> source-video observation snapshots for every ranked candidate
```

The local process may end after commit. GCS and Cloud SQL are the durable source of
truth. A later Cloud Run/API adapter must call the same probe-processing service rather
than duplicate detection, embedding, matching, or persistence logic.

## Database migration

Add schema changes to `scripts/db_schema.sql` and least-privilege grants to
`scripts/db_grants.sql`. Apply through the existing migration utility only after review.
Use additive tables; do not rewrite training data.

### `probe`

Recommended columns:

- `probe_id uuid primary key`;
- `request_key text not null unique` for idempotency;
- `submitter_principal text not null`;
- `media_uri text not null` and immutable `media_generation bigint not null`;
- `media_sha256 char(64) not null`, positive `media_bytes`, and `content_type`;
- `media_kind` constrained to `image` or `video`;
- worker, detector, embedding-model, and probe-threshold version snapshots;
- numeric threshold and requested `top_k` snapshots;
- status constrained to `queued`, `running`, `succeeded`, or `failed`;
- detected-result count, sanitized error code, and timestamps;
- sanitized metadata JSON with no credentials, signed URLs, key material, media bytes,
  crops, or embeddings.

### `probe_face`

Recommended columns:

- `probe_face_id uuid primary key` and `probe_id` foreign key;
- stable `local_face_id integer` unique within the probe;
- image bounding box and confidence, or video track start/end timestamps;
- observation count and quality summary;
- normalized `embedding vector(512) not null`;
- embedding model version;
- final decision (`matched` or `unknown`), best candidate subject UUID and score;
- creation timestamp.

Do not put crop bytes into Cloud SQL. Use explicit checks distinguishing image geometry
from video track timing.

### `probe_match`

Recommended columns:

- `probe_match_id uuid primary key`;
- `probe_face_id` foreign key;
- one-based rank, candidate subject UUID, cosine similarity, and decision-at-query-time;
- gallery embedding-model version and probe threshold version;
- optional non-sensitive snapshot metadata needed to interpret later gallery changes;
- unique `(probe_face_id, rank)` and `(probe_face_id, candidate_subject_id)` constraints.

Use deletion behavior that preserves audit meaning. If subjects can be deleted later,
store a candidate UUID snapshot separately or allow the live foreign key to become null
without erasing the historical identifier.

### `probe_match_observation`

Persist candidate-to-training-video provenance at probe time so later subject merges,
track changes, or source deletion cannot silently change the result returned by
`face-probe show`.

Recommended columns:

- `probe_match_observation_id uuid primary key`;
- `probe_match_id` foreign key with cascade deletion only when the containing probe is
  explicitly deleted under the approved retention policy;
- stable observation rank within the candidate;
- snapshot of `source_asset.external_source_ref` and `source_sha256`;
- snapshot of the training `face_track.track_id`, `start_ms`, and `end_ms`;
- snapshot of `max_quality`, `mean_quality`, and training-job completion timestamp;
- optional source ID and processing-job ID references using deletion behavior that does
  not erase the snapshot fields;
- unique `(probe_match_id, observation_rank)` and
  `(probe_match_id, training_track_id)` constraints.

Return every succeeded training track associated with the candidate subject, grouped by
source video and ordered deterministically by training-job completion time, video URI,
track start time, and track UUID. Include `source_video_count` and
`source_observation_count` in each candidate. Do not return signed URLs, media bytes,
crops, manifest-source URLs, or local paths.

### Transaction and idempotency contract

- Reserve or return the `probe` row by `request_key` before expensive processing.
- The same request key with different media hash, model version, threshold version, or
  `top_k` must be rejected as an idempotency conflict.
- Write all `probe_face`, `probe_match`, and `probe_match_observation` rows and mark the
  probe succeeded in one transaction.
- A retry may replace incomplete result rows only while holding the probe row lock.
- A failed processing attempt leaves a durable `failed` probe with one sanitized error
  code and no partial face/match rows.
- Persisted match rows are snapshots. `show` must never silently recompute them against
  a changed gallery.

## Read-only gallery repository

Extract a reusable repository method separate from the training commit path, for
example:

```python
rank_subjects(cursor, embedding, embedding_model_version, top_k) -> list[Candidate]
```

The SQL must:

- filter `subject.canonical_embedding IS NOT NULL`;
- filter `subject.model_version` to the probe embedding-model version;
- order by pgvector cosine distance ascending, then `subject_id` for deterministic ties;
- return at most the bounded `top_k`;
- calculate similarity as `1 - cosine_distance`;
- perform no gallery mutation.

For each ranked subject, query its provenance through
`face_track -> source_asset` and the succeeded `processing_job`. Snapshot every matching
track in `probe_match_observation` during the same probe transaction. A subject can have
many source videos; do not describe only the earliest observation as “the source.” The
earliest observation is merely the first record currently known to this system.

Do not reuse the current `_match` behavior unchanged: it discards ranks after the first
row and does not filter model versions. Training may later call the extracted ranking
helper, but changing training decisions is outside this feature and requires regression
testing against the validated worker image.

## Media and inference implementation

Create a probe service module independent of CLI parsing, such as
`worker/probe_service.py`. Keep orchestration in `worker/probe.py`.

### Local file submission

1. Resolve the file without accepting a directory, symlink escape, or special file.
2. Enforce type and byte limits before upload.
3. Generate the probe UUID and calculate SHA-256 locally by streaming.
4. Upload to `face-probes/<probe-id>/input.<validated-extension>` using the existing CSEK
   loaded directly from its file.
5. Require destination generation zero so no probe can be overwritten.
6. Reload and verify CSEK metadata, byte count, object generation, and content type.
7. Persist only the GCS URI and immutable provenance, never the local absolute path.

If database persistence fails after upload, retain the immutable probe object and print
only the probe ID plus a sanitized recovery instruction. Do not silently delete evidence
that the operator chose to persist.

### Existing GCS submission

- Accept only the configured bucket and `face-probes/` prefix.
- Require and verify CSEK metadata and immutable generation.
- Reject `videos/`, `face-staging/`, cross-project buckets, signed URLs, query strings,
  fragments, and mutable references lacking a recorded generation.
- Ensure the URI layout belongs to the supplied or allocated probe ID.

### Image processing

- Decode bytes in memory with OpenCV using strict size/type checks.
- Run SCRFD once over the image and preserve detector confidence and bounding boxes.
- Reuse the same alignment/crop preprocessing and BGR AdaFace contract as training.
- Treat each accepted detection as one probe face with a deterministic local ID.
- Do not invoke ByteTrack for a single image.

### Video processing

- Reuse the existing frame sampling, `ByteTrackTracker`, quality selection, and aggregate
  embedding code in `worker.pipeline.process_video`.
- Return templates to the probe service; do not call `VideoProcessor.process`, because
  that method commits training results, matches-or-creates subjects, and deletes staging.
- Enforce a probe-specific duration/size policy once the operator chooses it.

Model objects should be initialized once per command and shared across all faces/tracks.
CPU is acceptable for the local functional test; if `FACE_REQUIRE_CUDA=true`, preserve
the existing strict provider check.

## Storage and Terraform changes

Add `probe_prefix = "face-probes/"` as explicit configuration and validate it does not
overlap `videos/` or `face-staging/`.

Extend storage code with narrowly named probe methods rather than weakening source or
staging prefix checks:

- `upload_probe` with generation-zero precondition;
- `verify_probe` with exact generation and CSEK verification;
- `download_probe` with byte limit;
- an explicit delete method only after retention/deletion semantics are approved.

Terraform changes for the local milestone:

- add a `probe_prefix` variable/output;
- grant the developer conditional object create/read access only under `face-probes/`;
- do not grant the Cloud Run training runtime probe access;
- confirm the one-day `face-staging/` lifecycle rule cannot match `face-probes/`;
- retain bucket-level Storage audit logging;
- do not create a public endpoint or service-account key.

Before apply, require a Terraform plan with no replacement or deletion of the bucket,
Cloud SQL, KMS, CSEK secret, Cloud Run Job, VPC, or current training resources.

## CLI and output contract

Add this package entry point:

```toml
face-probe = "worker.probe:main"
```

Suggested submit options:

- exactly one of `--file` or `--gcs-uri`;
- required stable `--request-key`;
- bounded `--top-k` (recommended range 1–100);
- explicit or configured probe threshold and non-placeholder version;
- optional `--json-output` path created with mode `0600` and no overwrite;
- submitter principal defaulted only from an authenticated, verified local identity or
  required explicitly—never accepted as proof of authorization by itself.

Default stdout must contain IDs, status, ranks, subject IDs, scores, version labels, and
the complete persisted source-video observation list for every candidate. Each
observation contains the original `gs://.../videos/...` URI, source SHA-256, track start
and end milliseconds, quality summary, and training-job completion timestamp. This
provenance is mandatory rather than controlled by an `--include-provenance` option.
Output must not contain embeddings, CSEK data, tokens, media bytes, crops, signed URLs,
local credential paths, or identity names unless separately authorized.

Example candidate shape:

```json
{
  "rank": 1,
  "subject_id": "3f28...",
  "similarity": 0.81,
  "source_video_count": 2,
  "source_observation_count": 3,
  "source_observations": [
    {
      "video_uri": "gs://teak-banner-dome-bulk-videos/videos/example.mp4",
      "source_sha256": "64_HEX_CHARACTERS",
      "track_id": "8b51...",
      "start_ms": 12400,
      "end_ms": 18700,
      "max_quality": 0.92,
      "mean_quality": 0.84,
      "processing_completed_at": "2026-09-02T12:34:56Z"
    }
  ]
}
```

`show` should accept a probe UUID, read the persisted snapshot, and produce the same
safe JSON shape. A future `delete` or `promote` command is out of scope until separately
authorized and designed.

## Authentication and authorization

For the initial local milestone:

- use developer ADC; never create or download a service-account JSON key;
- use the Cloud SQL Connector with IAM authentication and the existing public connector
  route with no authorized networks;
- grant database DML only on the three probe tables to the approved developer database
  principal;
- do not give probe code PostgreSQL privileges to mutate gallery tables if a separate
  least-privilege probe database principal is feasible;
- record submitter principal and request key in every probe;
- treat result viewing as biometric-data access and ensure it is auditable.

Before any remote API or multi-user use, create a separate service account and distinct
submit/view/delete/promote authorization boundaries. Reusing the training runtime for a
larger service is explicitly deferred.

## Tests

### Unit tests

- image decode and multi-face detection mapping;
- video template mapping through the shared pipeline;
- no-face image succeeds with zero result rows;
- deterministic top-k order and threshold decision;
- model-version filtering excludes incompatible subjects;
- every candidate includes all associated succeeded training tracks grouped by source
  video in deterministic order;
- persisted provenance remains unchanged if gallery/source metadata later changes;
- subjects associated with multiple videos report accurate video and observation counts;
- malformed media, oversized files, unsupported types, and invalid prefixes fail;
- strict CSEK metadata, generation, and checksum checks;
- request-key idempotency and conflict detection;
- failed retries leave no partial face/match rows;
- `show` returns persisted rankings without querying the current gallery;
- logs and JSON output omit embeddings, keys, tokens, media, crops, and signed URLs;
- probe submission cannot call subject insert/update/delete paths.

Use synthetic embeddings and fake storage/database adapters. Unit tests must never read
the real CSEK or contact GCP.

### Disposable PostgreSQL integration tests

Extend `scripts/test_db_integration.sh` to apply the additive schema and prove:

- pgvector top-k ordering and model-version filtering;
- all probe, face, match, and match-observation rows commit atomically;
- request retry is idempotent;
- a forced transaction failure leaves a failed probe and no partial matches;
- probe submission leaves subject count and every canonical embedding unchanged;
- least-privilege grants permit probe operations but not gallery mutation.

### Authorized cloud integration test

After explicit approval and after the training rollout is reconciled:

1. apply reviewed schema, grants, prefix IAM, and configuration;
2. submit one small local image containing a known enrolled face;
3. verify the durable GCS object reports customer-supplied encryption;
4. verify Cloud SQL stores the probe, face embedding, ranked snapshot, complete
   candidate-to-video provenance snapshot, submitter, and all version fields, but no
   media/crop bytes;
5. verify the best expected subject and score manually without treating one sample as
   threshold calibration;
6. retry the same request key and prove no duplicate object or rows;
7. compare subject row count and canonical-embedding hashes before and after;
8. run `face-probe show PROBE_ID` in a fresh local process;
9. inspect logs for prohibited data;
10. retain or delete the test probe only according to the approved retention policy.

## Implementation sequence

1. Obtain the seven operator decisions above and explicit authorization to resume
   implementation work.
2. Wait for and separately reconcile the active training rollout; do not couple probe
   migration to that execution.
3. Add schema, constraints, grants, repository methods, and database integration tests.
4. Add probe-prefix configuration and CSEK storage methods with unit tests.
5. Extract/reuse inference primitives without changing training behavior; run the full
   existing worker suite to prove no regression.
6. Implement the read-only ranker and atomic probe persistence service.
7. Implement `face-probe submit` and `face-probe show` plus safe JSON output.
8. Add and review Terraform/IAM changes; apply only after a no-replacement/no-deletion
   plan is approved.
9. Run the authorized one-image cloud integration test and verify gallery immutability.
10. Update README, commands, architecture, outstanding-work, and cleanup documentation
    with actual—not planned—state and exact tested commands.

Do not rebuild or redeploy the training Cloud Run image merely to add the initial local
probe CLI. If shared code changes affect the validated training container, publish a new
immutable digest and repeat its full local, CUDA canary, and controlled-run gates before
using that digest for training.

## Acceptance criteria

The handoff is complete only when all of the following are evidenced:

- a local image and a local short video both produce durable probe IDs and ranked
  snapshots against the existing gallery;
- every probe media object is confined to `face-probes/`, CSEK-encrypted, immutable,
  and excluded from the staging lifecycle rule;
- results persist in Cloud SQL and remain viewable after the local process exits;
- rankings include deterministic top-k candidates, scores, model version, threshold,
  threshold version, and default source-video observation provenance;
- every returned subject can be correlated to all of its succeeded training videos and
  track time ranges without an additional option or live gallery query;
- no probe path can create/update subjects, identities, training tracks, or canonical
  embeddings;
- duplicate request IDs are idempotent and conflicting reuse is rejected;
- failure leaves durable sanitized status without partial match rows;
- local output and logs contain no secret or biometric vector payloads;
- schema/grant, unit, integration, and authorized cloud tests pass;
- Terraform reports no unintended replacement/deletion and returns to zero drift after
  any approved apply;
- documentation accurately distinguishes implemented local probing from any future
  remote probe service.

## Explicitly out of scope

- automatic enrollment or promotion of probe faces;
- identity assignment based solely on probe similarity;
- subject merging or deduplication;
- public HTTP/API exposure;
- remote Cloud Run probe serving;
- deleting probes before retention policy approval;
- recalibrating the training matcher as part of probe implementation;
- concurrent multi-user probe execution without a dedicated identity and authorization
  model.

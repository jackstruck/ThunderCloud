# Historical Face-Video Processing on GCP — Implementation Plan

## Goal

Build a minimal batch-processing system for selectively submitted historical video clips. The system should:

1. accept either a selectively named source object or selected entries from the existing download manifest;
2. copy the selected clip temporarily from `videos/` to a separate staging prefix in the same CSEK-encrypted Cloud Storage bucket;
3. initially run the containerized worker locally using the developer's Application Default Credentials and local CSEK file;
4. detect and track faces, select high-quality observations, generate face embeddings, and aggregate them at track level;
5. compare track-level embeddings against an authorized identity/subject gallery;
6. store only embeddings, match metadata, timestamps, hashes, and source references;
7. delete the staged video after a successful database commit;
8. accept explicit image/video probes, persist their CSEK-encrypted media and versioned
   search results, and match them against existing subjects without treating them as
   enrollment observations;
9. retain no face crops or video in the application database.

This plan intentionally avoids GKE, Pub/Sub, Redis, DeepStream, a separate vector database, and a dedicated learned face-quality model in the first version.

## Architecture

```text
Local developer CLI (developer ADC + local CSEK file)
  |
  | exact source object, or selected manifest entries
  v
gs://teak-banner-dome-bulk-videos
  |  source:  videos/
  |  staging: face-staging/
  |  probes:  face-probes/
  |  - uniform bucket-level access
  |  - public access prevention
  |  - source, staging, and probe objects use the same CSEK
  |  - soft delete disabled
  |  - lifecycle fallback deletion scoped only to face-staging/
  v
Local containerized worker
  |  - developer Application Default Credentials
  |  - local CSEK file, read into memory only
  |  - CPU or locally available GPU
  |  - same container later deployed to Batch
  |
  +--> SCRFD face detector
  +--> ByteTrack face tracking
  +--> heuristic quality scoring
  +--> AdaFace/CVLFace embedding model
  +--> best-N embedding aggregation
  +--> pgvector nearest-neighbor query
  v
Cloud SQL PostgreSQL + pgvector in us-central1
  |  - CMEK
  |  - public and private IP during local development
  |  - no authorized networks
  |  - Cloud SQL Connector + automatic IAM DB authentication
  |
  +--> subjects / identities
  +--> face_tracks
  +--> source references and hashes
  +--> processing jobs
  +--> durable probes / probe faces / ranked match snapshots

After DB commit: delete only the `face-staging/` object. Never delete or mutate the
corresponding immutable `videos/` source object.
```

## Important data-retention rule

The staging GCS URI is not a durable source reference because the object is deleted. For the current archive, persist the original `gs://teak-banner-dome-bulk-videos/videos/...` URI as `external_source_ref`, together with the manifest metadata. Other callers may supply another durable reference, for example:

- archive system object ID;
- evidence-management system URI;
- camera archive key;
- durable internal document/asset ID.

Persist the external source reference together with the SHA-256 of the exact submitted bytes and the clip-relative track timestamps. Source objects are assumed immutable for the MVP; do not add generation-pinning logic beyond retaining an available manifest `generation` as provenance.

## Phase 1 — Infrastructure

Use the Terraform in `terraform/`.

### Resources created

- required Google APIs;
- regional VPC and private subnet with Private Google Access;
- private service networking range for Cloud SQL;
- the existing `teak-banner-dome-bulk-videos` bucket is adopted as an input rather than created or destroyed by this Terraform;
- bucket IAM for the local developer workflow plus prefix-conditioned `face-staging/`
  object access for the keyless Batch runtime identity;
- a short lifecycle deletion fallback whose condition matches only `face-staging/`;
- a durable `face-probes/` prefix with separate least-privilege IAM and no staging
  lifecycle rule when the probe workflow is implemented;
- KMS key ring and CMEK for Cloud SQL only;
- Artifact Registry Docker repository;
- a dedicated keyless Batch worker service account with repository-read, Batch reporting,
  log-writing, Cloud SQL connection, and Cloud SQL IAM login permissions;
- a separate regional Secret Manager backup of the PostgreSQL administrator password,
  with no Batch runtime access and with its payload managed outside Terraform state;
- developer IAM for local GCS access, Artifact Registry image pushes, and Cloud SQL access;
- Cloud SQL PostgreSQL 17 instance with:
  - public IP for connector-only local access and private IP for the later Batch worker;
  - no authorized networks;
  - CMEK;
  - IAM database authentication;
  - pgvector-compatible PostgreSQL version;
- IAM database user representing the developer account.

The Batch API, runtime service account, Batch-specific IAM, and corresponding Cloud SQL
IAM database user are provisioned. No service-account key is created. Remote CSEK
delivery remains a separate Secret Manager step. The runtime database user has received
the DML-only grants in `scripts/db_grants.sql`: database CONNECT, schema USAGE, and
SELECT/INSERT/UPDATE/DELETE on the five application tables, without grant option.

### Inputs to decide before apply

- `project_id = "teak-banner-dome"`
- multi-region location `US` for resources that support it and `us-central1` for Cloud SQL, KMS, Artifact Registry, VPC, and future Batch resources
- `source_bucket_name = "teak-banner-dome-bulk-videos"`
- `source_prefix = "videos/"`
- `staging_prefix = "face-staging/"`
- `probe_prefix = "face-probes/"`
- `subnet_cidr`
- `staging_ttl_days` (default: 1)
- `db_tier` (default is intentionally small)
- local developer account used for Terraform, image push, ingestion, database bootstrap, and worker execution during the first milestone

### Deployment sequence

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars
terraform init
terraform plan
terraform apply
```

Record the Terraform outputs; the local application will need the existing bucket name and prefixes, Artifact Registry repo, and Cloud SQL connection name. Retain the network/subnet names and Cloud SQL private IP for the later Batch migration.

The Terraform imports the existing archive bucket with `prevent_destroy` and `force_destroy = false`. Review the import plan before apply: it must not recreate the bucket, change its default encryption, or remove unrelated lifecycle configuration.

## Phase 2 — Database bootstrap

Run `scripts/db_schema.sql` once using an administrative Cloud SQL connection.

The schema deliberately separates `subject` from `identity`:

- `subject`: biometric cluster/template known to the system;
- `identity`: authorized real-world identity association.

An observation can belong to a subject without asserting a person's identity.

Create an IAM database user for the developer account and grant it only the SQL privileges required to run the application after bootstrap. Use the administrative connection only for schema migrations. When Batch is added, create a separate IAM database user for its runtime service account with the same least-privilege application role.

## Phase 3 — Worker container

Create one Docker image and push it to the Terraform-created Artifact Registry repository.

Suggested runtime:

- Python 3.12+
- CUDA-compatible PyTorch or ONNX Runtime/TensorRT
- OpenCV or PyAV/FFmpeg
- SCRFD detector
- ByteTrack
- AdaFace/CVLFace embedding model
- NumPy
- Google Cloud Storage client
- Google Cloud SQL Python Connector (automatic IAM database authentication; public IP locally, private IP in Batch)

The remote GPU image is built from `nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04`
with Python 3.12 and pinned `onnxruntime-gpu==1.29.0`. It packages only the approved
SCRFD and AdaFace ONNX exports, verifies their recorded SHA-256 values at build time,
and preserves the AdaFace export's required BGR input order. The image verification
script rejects a CPU-only ONNX Runtime build and checks both model contracts. Its
strict mode must also execute both models with `CUDAExecutionProvider` on an L4 before
the bulk canary.

Published candidate (use this digest, never the tag, in Batch jobs):

```text
us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:5da8096026d0ed126fdd99a7f2d04e8e3b4d09fd47c87fb372495054b1f0cd69
```

This replacement digest includes Secret Manager CSEK retrieval and configurable Cloud
SQL public/private routing. Use `FACE_CSEK_SECRET=face-batch-gcs-csek` and
`FACE_CLOUD_SQL_IP_TYPE=PRIVATE` for Batch; local execution retains file-based CSEK
loading and the default `PUBLIC` connector route.
- PostgreSQL driver

### Model licensing gate

Before packaging pretrained weights, verify that the specific checkpoint and training-data-derived license permits the intended use. Treat source-code licenses and pretrained-weight licenses as separate review items.

## Phase 4 — Worker contract

Implement the container entrypoint as approximately:

```bash
python -m worker.process \
  --gcs-uri gs://BUCKET/path/object.mp4 \
  --external-source-ref "archive://camera17/2026-03-14/segment-0083" \
  --expected-sha256 "..." \
  --job-id "..."
```

Required behavior:

1. Validate job arguments.
2. Require that the URI is in `gs://teak-banner-dome-bulk-videos/face-staging/` and reject every other bucket or prefix.
3. Fetch object metadata using the CSEK and verify that the object reports customer-supplied AES-256 encryption.
4. Stream or read the video using the CSEK without writing persistent face crops.
5. Compute SHA-256 and reject on mismatch if an expected hash was supplied.
6. Decode video.
7. Detect faces at a configurable sampling rate (initial default: 8–10 detections/sec, not every 30-FPS frame).
8. Track face boxes with ByteTrack.
9. For each face observation calculate heuristic quality:
   - detector confidence;
   - face pixel size;
   - blur/sharpness;
   - yaw/pitch/roll or landmark geometry;
   - brightness/contrast;
   - landmark confidence.
10. Maintain only the best `N` candidate face observations per track (initial default: 5).
11. Generate embeddings only for candidates that improve the track's retained quality set.
12. L2-normalize each embedding.
13. Compute a track template from the best embeddings, initially a normalized quality-weighted mean.
14. Query pgvector for top-K subject candidates.
15. Apply a calibrated accept/reject threshold; `unknown` must remain a valid result.
16. Write the processing result and track records in one database transaction.
17. Commit the transaction.
18. Delete only the validated `face-staging/` object.
19. Exit 0.

If any step before successful commit fails, do not intentionally delete the staging object; the prefix-scoped bucket lifecycle policy will clean it up later and the job can be retried. The immutable `videos/` object is never a deletion target.

## Phase 5 — Suggested application modules

```text
worker/
  __main__.py
  config.py
  video.py
  detector.py
  tracker.py
  quality.py
  embedder.py
  aggregate.py
  matcher.py
  db.py
  storage.py
  models.py
  telemetry.py

tests/
  unit/
  integration/
  fixtures/
```

### `video.py`

- decode via PyAV/FFmpeg;
- expose frame timestamps;
- configurable detector sampling FPS;
- never write decoded frames to disk by default.

### `detector.py`

- SCRFD inference;
- return bounding box, detector score, and landmarks.

### `tracker.py`

- ByteTrack over face detections;
- output local track IDs;
- tolerate fragmented tracks; cross-clip identity resolution happens in embedding space.

### `quality.py`

Start with deterministic heuristics. Return a scalar score plus component metrics for later calibration. Avoid a separate quality neural network until benchmarks demonstrate it is useful.

### `embedder.py`

- aligned face crop in memory;
- AdaFace/CVLFace inference;
- output normalized 512-dimensional vector;
- expose a model-version string that is persisted with every template.

### `aggregate.py`

Maintain at most N good embeddings per track. Initial algorithm:

```text
weight_i = clamp(quality_i, 0, 1)
mean = sum(weight_i * embedding_i) / sum(weight_i)
track_embedding = L2_normalize(mean)
```

Keep aggregation behind an interface so it can later be replaced with medoid selection, robust mean, or model-specific template fusion.

### `matcher.py`

- query top-K by cosine distance;
- return candidate IDs and scores;
- never force a match;
- keep thresholds in configuration, not source code;
- log model version + threshold version with each match decision.

### `storage.py`

- read object metadata;
- stream bytes using the CSEK;
- enforce the staging bucket and prefix allowlist before every read or delete;
- delete only a `face-staging/` object after DB commit;
- treat `external_source_ref` as the durable provenance record, not the temporary GCS URI.

### `db.py`

Use automatic IAM database authentication through the Cloud SQL Python Connector. During local development, select the instance's public IP path. The instance has no authorized networks, and the connector supplies IAM authorization and TLS; do not open direct PostgreSQL access or use a static database password. When the worker moves to Batch in the VPC, change configuration to select private IP without changing the database or schema.

## Phase 6 — Local execution

Run the worker container from this workspace after staging each selected object. Mount the CSEK file read-only at a fixed container path or pass its path through configuration; never pass the key value itself through an argument or environment variable. Use developer ADC for GCS and the Cloud SQL Connector.

Local execution should exercise the production worker contract, database transaction, idempotency, and staging cleanup. CPU execution is acceptable for functional development; use a local NVIDIA GPU when available. Image builds should be pushed to Artifact Registry so the exact tested image can later be used by Batch.

The local launcher creates the processing-job identifier, runs the container synchronously, captures only non-sensitive status, and returns success only after the worker commits and deletes its staging object.

## Phase 7 — Later migration to Cloud Batch

`scripts/submit_batch.py` is a scaffold. The IDE agent should finish integration after the worker image exists.

Job defaults:

- machine type: `g2-standard-8`;
- provisioning: Spot;
- GPU drivers installed by Batch;
- one task per clip initially;
- retries enabled;
- dedicated Batch service account;
- MVP uses an ephemeral external IP because Batch-managed GPU driver installation fetches drivers at runtime;
- custom VPC/subnet with no ingress firewall rules;
- Cloud Logging.

The Batch worker will use Cloud SQL private IP through the existing VPC. Its runtime
service account, IAM database user, SQL grants, and Secret Manager CSEK delivery are
provisioned. Secret version 1 contains the existing Base64 CSEK, was uploaded outside
Terraform with CRC32C verification, and is read directly into worker memory through
`FACE_CSEK_SECRET=face-batch-gcs-csek`. The secret payload is absent from Terraform
state, images, job arguments, environment values, object metadata, logs, and database
records.

Initial remote integration may use an ephemeral external IP because Batch-managed GPU-driver installation fetches drivers at runtime. The VPC has no ingress firewall rules. After the worker is stable, build a custom Batch VM image with compatible NVIDIA drivers and switch the job to `noExternalIpAddress=true`, relying on Private Google Access for Google APIs/services.

The job payload should pass only references and non-secret configuration. Never pass the
CSEK in command-line arguments, the container image, object metadata, logs, or the Batch
job definition. The approved remote key-delivery mechanism is direct in-memory access to
the dedicated Secret Manager secret by the Batch runtime service account.

## Phase 8 — Local selection and staging CLI

Keep version 1 simple. A CLI is sufficient:

```text
face-ingest submit-object \
  gs://teak-banner-dome-bulk-videos/videos/OBJECT.mp4

face-ingest submit-manifest \
  --manifest ../bulk-download/data/manifest.jsonl \
  --select-file selected-uids.txt
```

CLI behavior:

1. Use developer Application Default Credentials and read the CSEK from `/workspaces/ThunderCloud/.secrets/gcs-csek.base64`; strictly decode it as one Base64 AES-256 key and never log, persist, or copy the key into the repository.
2. Accept either one exact `videos/` object or selected records from `bulk-download/data/manifest.jsonl`. Do not recursively submit the entire prefix by default.
3. For the JSONL manifest:
   - parse every nonblank line as one JSON object;
   - use only `status: "complete"` records with a valid `object` under the configured `videos/` prefix;
   - allow selection by `uid` and/or exact object URI;
   - carry forward `object`, `generation`, `sha256`, `bytes`, `content_type`, `uid`, `timestamp`, and available source URL fields;
   - ignore `failed` records;
   - resolve a selected `duplicate` record through `duplicate_of` to its corresponding completed object, or reject it clearly if it cannot be resolved;
   - deduplicate selected inputs by final source object URI or SHA-256 before staging.
4. Copy the exact bytes to a collision-resistant name under `face-staging/`, encrypting the destination with the same CSEK. Never overwrite an existing staging object.
5. Attach only non-secret object metadata:
   - external source ref;
   - SHA-256;
   - capture timestamp if known;
   - uploader/request ID;
   - source UID when known.
6. Verify staged object size and CSEK encryption, invoke one local worker process per selected clip initially, and return the processing job IDs.

For the first milestone the local developer account performs selection, source reads, staging writes, image pushes, Terraform, database bootstrap, Cloud SQL connections, and worker execution. Split these responsibilities into narrower identities when migrating execution to Batch. Do not create or download service-account JSON keys.

## Phase 9 — Database matching workflow

Cross-video subject matching is required for the bulk run. Set
`FACE_MATCHING_ENABLED=true` in the approved local, canary, and remote worker
configuration. The Batch submitter must reject a bulk submission when matching is
disabled; feature-extraction-only processing is not an acceptable bulk mode.

Before the canary, replace the placeholder threshold/version with values validated on
representative footage. Enabling subject matching does not authorize real-world identity
assertions: subject clustering and `identity` association remain separate operations.

For every processed face track:

1. generate track embedding;
2. query top-K current subjects;
3. if best candidate passes the calibrated threshold, attach observation to that subject;
4. otherwise create a new anonymous subject;
5. associate a subject with an `identity` only through an explicit authorized enrollment/review flow.

Do not automatically turn a weak nearest neighbor into an identity.

### Persistent probe search

Add an explicit probe workflow for answering: "which existing subjects are most
similar to the face or faces in this supplied picture or video?" A probe is a
durable search record, not an enrollment and not a new subject observation.

#### Inputs and interface

Support both images and videos through a local CLI first, with an authenticated API
as a later wrapper around the same service method:

```text
face-probe submit --file ./person.jpg --top-k 10
face-probe submit --file ./short-clip.mp4 --top-k 10
face-probe submit --gcs-uri gs://teak-banner-dome-bulk-videos/face-probes/PROBE_ID/input.mp4 --top-k 10
face-probe show PROBE_ID
```

- auto-detect supported image/video types from verified content, not only the filename;
- reject unsupported media, decompression bombs, oversized images, and videos above the configured duration/byte limits;
- for images, detect every face and return a separate ranked result set for each face;
- for videos, use the normal detector/tracker/quality/aggregation pipeline and return a separate ranked result set for each track;
- accept an optional client request ID for retry-safe submission;
- return the durable `probe_id`, processing status, detected face/track count, and ranked candidates.

#### Durable probe media

Persist the submitted media under a dedicated `face-probes/` prefix in
`teak-banner-dome-bulk-videos`. This prefix is separate from immutable training inputs
and ephemeral `face-staging/` objects.

- generate a UUID probe ID before upload;
- write a collision-resistant object such as `face-probes/<probe_id>/input.<ext>`;
- encrypt every probe object with the existing CSEK and verify the reported
  customer-supplied encryption metadata after upload;
- never overwrite an existing probe object;
- record object generation, byte length, content type, and SHA-256 in Cloud SQL;
- when the input is an existing GCS object, copy the exact generation into
  `face-probes/` so the durable probe is an immutable snapshot;
- do not put raw image/video bytes, face crops, or the CSEK in PostgreSQL;
- do not apply the one-day staging lifecycle rule to `face-probes/`;
- define and approve probe-media and probe-record retention before enabling this
  workflow beyond development. Deleting a probe must be an explicit audited action
  that removes the CSEK object and its database records according to that policy.

#### Database records

Extend the schema with durable, append-oriented records:

- `probe`: probe ID, request ID/idempotency key, submitter principal, durable GCS URI
  and generation, media type, SHA-256, byte length, status, model/threshold versions,
  requested top-K, timestamps, and non-sensitive metadata;
- `probe_face`: one detected image face or video track, bounding-box or track-time
  provenance, observation/quality counts, and the normalized 512-dimensional probe
  embedding;
- `probe_match`: the ranked candidate snapshot for each `probe_face`, including rank,
  candidate `subject_id`, optional authorized `identity_id`, similarity score,
  threshold, threshold version, and decision (`matched`, `unknown`, or `review`).

Use foreign keys with explicit deletion behavior and indexes on `probe.request_id`,
`probe.sha256`, `probe.status`, and probe-to-match relationships. Store the probe,
all probe faces, and their ranked result snapshots in one transaction. A failed search
must leave a durable `failed` probe status and sanitized error code without partial
match rows. A retry with the same request ID must not duplicate the probe.

Persisting `probe_match` is important: later subject merges, identity assignments, or
threshold changes must not silently rewrite what the search returned at that time.
A new search creates a new versioned probe result rather than mutating historical
rankings.

#### Matching semantics

For each probe face or track:

1. generate and L2-normalize an embedding using the configured model version;
2. search only compatible `subject.canonical_embedding` rows with the same embedding
   model/version;
3. retrieve the requested top-K candidates with cosine similarity;
4. persist all returned candidates and scores, not only the winner;
5. label the best candidate `matched` only when the approved, versioned probe threshold
   is met; otherwise return `unknown` or `review`;
6. never create a subject, update a canonical subject embedding, increment enrollment
   sample counts, or attach an identity merely because a probe was submitted;
7. require a separate authorized action to promote a probe face into enrollment data.

The probe threshold may differ from the ingestion/clustering threshold. Both must be
validated on representative data and versioned. Until that validation and the
authorization policy are approved, the feature may return ranked similarity candidates
for evaluation but must not assert a real-world identity match.

#### Authorization and audit

- require an authenticated principal with a dedicated probe-submit permission;
- separate probe submission, result viewing, deletion, and enrollment-promotion
  permissions;
- record the submitting principal and client request ID in Cloud SQL;
- enable audit coverage for reads/writes under `face-probes/` and for result access;
- do not emit raw embeddings, media bytes, crops, or signed media URLs in application
  logs;
- return identity fields only to principals authorized to view identity associations;
  other callers receive anonymous subject IDs and scores;
- rate-limit submissions and bound `top-k` to prevent database scraping.

## Phase 10 — Testing

### Unit tests

- quality scoring is deterministic;
- top-N retention works;
- aggregation outputs unit-length vectors;
- timestamp calculation is correct;
- storage deletion is never called before commit;
- failed DB transaction leaves staging object intact;
- source hash mismatch aborts processing;
- matcher can return unknown;
- image probes produce one result set per detected face;
- video probes produce one result set per retained track;
- a probe never creates or updates a subject;
- persisted probe rankings retain model and threshold versions;
- retrying a probe request ID is idempotent;
- unauthorized callers cannot submit, view, delete, or promote probes.

### Integration tests

- upload a synthetic/non-sensitive fixture video;
- verify the local CLI can read an immutable `videos/` source with the CSEK and create a CSEK-encrypted `face-staging/` copy;
- run the containerized worker locally using only developer ADC and the mounted local CSEK file;
- verify Cloud SQL Connector access over public IP with automatic IAM database authentication and no authorized networks;
- verify pgvector query;
- verify DB rows written;
- verify the staged object is deleted after commit and the original `videos/` object remains;
- verify no object remains recoverable through GCS soft delete because soft delete is disabled;
- verify deletion and lifecycle rules cannot target `videos/`;
- verify worker cannot enumerate unrelated buckets/resources;
- upload a CSEK-encrypted image and video probe under `face-probes/`;
- verify probe media, embeddings, ranked candidates, submitter, and versioned decisions
  remain available after the worker exits;
- verify raw probe media is stored only in GCS and no image/video bytes or crops enter
  Cloud SQL;
- verify probe submission does not change subject counts or canonical embeddings;
- verify probe deletion follows the approved object-and-record retention policy.

### Later Batch integration tests

- create a Batch job using the exact locally tested image digest;
- verify the L4 GPU and installed driver are visible;
- verify the runtime service account can retrieve only the approved CSEK secret and can read/delete only staging objects;
- verify Cloud SQL IAM connection through private IP;
- verify the Batch VM has no permissive ingress firewall path;
- verify a remote retry remains idempotent with the earlier locally generated database state.

### Security acceptance checks

- bucket Public Access Prevention = enforced;
- uniform bucket-level access = enabled;
- every source and staging video used by this workflow reports CSEK encryption;
- the CSEK is absent from source control, Terraform state, Batch arguments, object metadata, database rows, and logs;
- staging lifecycle deletion is scoped only to `face-staging/`;
- probe media is confined to `face-probes/`, uses the CSEK, and is not subject to the
  staging lifecycle rule;
- Cloud SQL public IP is enabled for local connector access, with no authorized networks or direct database access;
- local Cloud SQL access requires developer IAM, automatic IAM database authentication, PostgreSQL grants, and connector-managed TLS;
- Cloud SQL private IP is available for the later Batch worker, after which public IP can be disabled;
- MVP Batch external IP is ephemeral and no ingress firewall rules exist; document the future custom-image/no-external-IP hardening item;
- worker service account has no Owner/Editor role;
- no service-account JSON keys are created;
- IAM DB authentication is enabled;
- staged object lifecycle fallback is <= intended policy;
- soft delete is disabled on the shared bucket;
- app logs never contain face embeddings, image bytes, or raw GCS signed URLs;
- probe result access and deletion are authenticated and auditable;
- probe searches cannot enroll identities or mutate subjects without a separate
  authorized promotion action.

## Phase 11 — Benchmark before scaling

Measure these values on representative footage:

- input video hours;
- wall-clock GPU time;
- effective video-hours per GPU-hour;
- mean faces/frame;
- detector inference count;
- embedding inference count;
- percentage of detections rejected by quality gate;
- track fragmentation rate;
- false accept / false reject behavior using your own validation set.

Do not add GKE, DeepStream, a dedicated vector database, or learned quality scoring until a measured bottleneck justifies it.

## Recommended first milestone

Definition of done:

- `terraform apply` creates the environment;
- a Docker image is pushed to Artifact Registry;
- one selected `videos/` test clip is copied to `face-staging/` with its manifest provenance;
- the containerized worker processes it locally using developer ADC and the local CSEK file;
- one or more track templates are inserted in PostgreSQL;
- the database stores no image/video bytes;
- the staging object is deleted after commit;
- the result remains traceable through external source reference + SHA-256 + clip timestamps;
- rerunning the same clip is idempotent and does not duplicate observations.

### Remote-worker milestone

- create the Batch runtime service account and corresponding IAM database user;
- provision an approved Secret Manager CSEK secret without placing its value in Terraform state;
- grant the runtime service account only the required secret, staging-object, logging, Artifact Registry, Batch reporter, and Cloud SQL permissions;
- submit the exact locally tested image to a Spot `g2-standard-8` Batch job in `us-central1`;
- connect to the existing Cloud SQL database through private IP;
- confirm that remote processing is retry-safe alongside records produced locally;
- disable Cloud SQL public IP once local worker/database administration no longer requires it.

## Deferred features

Do not implement until needed:

- GKE;
- live RTSP ingest;
- DeepStream;
- Pub/Sub orchestration;
- Redis;
- Qdrant/Milvus;
- learned face-quality model;
- person-level ReID;
- long-term storage of generated crops or non-probe clips;
- automated human identity assignment;
- custom face-network training.

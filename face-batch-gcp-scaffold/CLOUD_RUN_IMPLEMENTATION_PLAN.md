# Cloud Run GPU Migration Implementation Plan

## Status and objective

This document defines the migration from one Google Cloud Batch job per video to an
unattended Cloud Run GPU Job. It supplements `IMPLEMENTATION_PLAN.md`; all existing
data-handling, matching, encryption, provenance, and retention requirements remain in
force.

The migration should:

1. process selected objects or selected records from
   `../bulk-download/data/manifest.jsonl`;
2. continue processing after the developer's local terminal or Codespace disconnects;
3. amortize GPU startup over multiple videos instead of starting one VM per video;
4. retain mandatory subject matching and idempotent database writes;
5. use the existing private Cloud SQL instance and existing CSEK-protected GCS data;
6. preserve Cloud Batch as a rollback path until Cloud Run has completed a controlled
   production rollout; and
7. preserve local execution of the same worker and processing pipeline for individual
   files and selected manifest entries.

## Decisions

| Item | Decision |
|---|---|
| Platform | Cloud Run Jobs with one NVIDIA L4 GPU per task |
| Region | `us-central1` |
| Initial concurrency | One task at a time (`parallelism = 1`) |
| Task shape | At least 4 vCPU, 16 GiB memory, one L4 GPU |
| Hard task timeout | 60 minutes |
| Work allocation | Durable Cloud SQL work queue with leases |
| Source | `gs://teak-banner-dome-bulk-videos/videos/` |
| Staging | `gs://teak-banner-dome-bulk-videos/face-staging/` |
| CSEK delivery | Read `face-batch-gcs-csek` from Secret Manager directly into memory |
| Database route | Direct VPC egress to the existing private Cloud SQL address |
| Runtime identity | Reuse `face-batch-runtime@teak-banner-dome.iam.gserviceaccount.com` initially |
| Matching | Required: `FACE_MATCHING_ENABLED=true` |
| Initial rollout | One-video canary, ten-video controlled run, then remaining manifest |
| Batch retirement | Only after Cloud Run acceptance and reconciliation |
| Local compatibility | Required; Cloud Run is an adapter around the same worker code |

Cloud Run Jobs are preferable to a Cloud Run service here: processing is finite,
does not need a public HTTP endpoint, and needs job/task retries and completion status.
The job will not expose an application endpoint.

## Local execution compatibility

Cloud Run must not become a fork of the processing implementation. Detection, tracking,
quality selection, embedding, matching, transaction handling, and staging cleanup will
remain in shared modules invoked by both entry points:

```text
Local:     python -m worker.process --gcs-uri ... --external-source-ref ...
Cloud Run: python -m worker.cloud_run_drain --rollout-id ...
```

`worker.cloud_run_drain` is orchestration only: it claims a queue item, stages it, and
passes the resulting one-video request to the same callable used by `worker.process`.
It must not contain a second inference or database-write implementation.

The existing local scripts and selective/manifest CLI remain supported. Local execution
continues to use developer ADC, the local CSEK file mounted read-only, and the Cloud SQL
Connector public route. Cloud Run uses its runtime identity, the Secret Manager copy of
the CSEK, and the private Cloud SQL route. These are configuration differences, not code
path differences. No secret value is copied between the two configurations.

Every worker change must pass the existing local test suite and a local one-video smoke
test before publishing a Cloud Run image. A processing regression found in Cloud Run
must be reproducible by invoking the shared one-video operation locally with equivalent
non-secret configuration.

## Target architecture

```text
Local enqueue command (developer ADC)
  |
  | parses manifest or exact selection; creates rollout + work_item rows
  v
Cloud SQL work queue in us-central1
  ^                                      |
  | transactional claim/lease/status     | launch once
  |                                      v
  +---------------- Cloud Run GPU Job execution
                         |
                         | task_count=N, parallelism=1 initially
                         | each task drains work for about 50–52 minutes
                         v
      gs://teak-banner-dome-bulk-videos/videos/   (immutable source)
                         |
                         | server-side CSEK copy, just in time
                         v
      gs://teak-banner-dome-bulk-videos/face-staging/
                         |
                         | CSEK read; CUDA inference; mandatory matching
                         v
             Cloud SQL over private IP
                         |
                         | commit succeeds
                         v
                delete staged object
```

The local command only enqueues work and starts the Cloud Run execution. Cloud Run and
Cloud SQL own all subsequent state, so a closed laptop, terminal, or Codespace does not
interrupt processing.

## Why a durable queue is required

Mapping one Cloud Run task to one video would retain much of the startup and minimum
billing overhead of the current Batch design. Instead, a warm GPU task should process
videos repeatedly until it approaches its timeout.

The queue is also the source of truth for retries and reconciliation. Cloud Run's task
status alone cannot distinguish a fully successful rollout from an execution that
finished after permanently rejecting some inputs.

## Database changes

Add an additive migration rather than modifying historical `processing_job` rows.

### `processing_rollout`

Recommended columns:

- `rollout_id uuid primary key`;
- human-readable `name` and optional request/idempotency key;
- immutable container image digest;
- model, worker, aggregation, and threshold versions;
- matching-enabled flag constrained to `true` for production rollouts;
- status: `queued`, `running`, `succeeded`, `failed`, or `cancelled`;
- requested, succeeded, retryable, and dead-letter counts;
- creator principal and created/started/completed timestamps;
- sanitized configuration JSON, excluding credentials and secrets.

Permit only one bulk rollout in `running` state initially. This prevents two executions
from competing to update the same evolving subject gallery.

### `processing_work_item`

Recommended columns:

- `work_item_id uuid primary key` and `rollout_id` foreign key;
- manifest UID and immutable source GCS URI;
- source SHA-256, generation provenance, byte length, content type, and capture time;
- durable `application_job_id`, created once and reused on every retry;
- state: `pending`, `leased`, `retry`, `succeeded`, or `dead_letter`;
- attempt count and maximum attempts;
- lease owner, lease acquisition time, lease expiry, and heartbeat time;
- staged URI and staged generation when present;
- sanitized last error code and timestamps.

Add:

- a unique constraint on `(rollout_id, source_uri, source_sha256)`;
- an index supporting claims by `(rollout_id, state, lease_expires_at, work_item_id)`;
- constraints preventing a success row without an application job ID and completion
  timestamp.

The claim operation must use one short transaction with `SELECT ... FOR UPDATE SKIP
LOCKED`, then change the selected row to `leased`, assign a random lease owner, increment
the attempt count, and set its expiry. A worker may update or complete only a row whose
current lease owner it holds.

Use a 15–20 minute renewable lease and heartbeat it during processing. An expired lease
is eligible for reclamation. This must be tested by terminating a task mid-video.

The existing processing idempotency key remains the final defense against duplicate
face tracks. A retry must reuse the persisted `application_job_id` and the same source,
model, aggregation, and threshold versions.

## Worker changes

Preserve the existing one-video `worker.process` entry point for local diagnostics and
Batch rollback. Add a second entry point, for example:

```text
python -m worker.cloud_run_drain --rollout-id ROLLOUT_ID
```

The drain entry point will:

1. validate mandatory configuration and fail if matching is disabled;
2. verify `CUDAExecutionProvider` and execute both model preflight inferences once;
3. connect to Cloud SQL using automatic IAM database authentication and private IP;
4. atomically claim one work item;
5. copy its immutable `videos/` source to a collision-resistant `face-staging/` object,
   using the Secret Manager CSEK for both source and destination;
6. record and verify the staged URI, size, checksum, generation, and CSEK metadata;
7. invoke the existing processing pipeline and transactional database commit;
8. mark the queue item succeeded and delete only its staged object;
9. repeat while sufficient task time remains;
10. stop claiming at approximately 50–52 minutes and exit successfully after any
    in-flight item is safely completed or released.

Before claiming, compare remaining task time with a conservative runtime estimate based
on source bytes plus a safety margin. Inputs that cannot safely fit must be left for a
fresh task. An input repeatedly unable to complete must become `dead_letter`, not loop
forever.

If a retry finds a recorded staging object, verify and reuse it. If it no longer exists
because the lifecycle rule removed it, copy the immutable source again. Never pre-stage
the complete manifest.

### Failure classes

- Retryable: API throttling, transient GCS/Cloud SQL errors, temporary resource
  exhaustion, task termination, and HTTP 5xx responses.
- Permanent: invalid manifest data, source checksum mismatch, unsupported media, or a
  policy-limit violation.
- Unknown: retry a bounded number of times and then dead-letter with a sanitized code.

No exception or diagnostic may contain the CSEK, database tokens, raw embeddings, media
bytes, face crops, or signed URLs.

## Cloud Run Job configuration

Manage a generic `google_cloud_run_v2_job` in Terraform. The exact rollout ID is supplied
as an execution override, so a normal `terraform apply` must not start data processing.

Required configuration:

- name `face-batch-gpu-drain` in `us-central1`;
- immutable Artifact Registry image digest, never a mutable tag;
- runtime service account `face-batch-runtime`;
- one `nvidia-l4` GPU;
- GPU zonal redundancy disabled;
- at least 4 vCPU and 16 GiB memory;
- task timeout of 3,600 seconds;
- maximum two task retries initially;
- Direct VPC egress through the existing `face-batch-vpc` and
  `face-batch-batch` subnet;
- private ranges routed through the VPC for Cloud SQL;
- Cloud Logging enabled;
- non-secret environment configuration, including private Cloud SQL routing,
  source/staging allowlists, and mandatory matching values;
- `FACE_CSEK_SECRET=face-batch-gcs-csek`, which is only a Secret Manager resource name.

Continue retrieving the CSEK with the Secret Manager API inside the process. Do not put
the key value in an environment variable, Cloud Run secret mount, command-line argument,
Terraform state, container layer, or job definition.

The current CUDA 13 image is compatible in principle with Cloud Run's current GPU
driver generation, but the worker changes require a new immutable digest. The new image
must pass strict SCRFD and AdaFace CUDA preflight on an L4 before rollout.

Validated queue-aware Cloud Run image:

```text
us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:eefc55e1e48a9e5d367ce1f9dac7f7be4a13c1a289ca0fc565d9aa668c91955c
```

This digest passed the build-time model hashes and packaging contracts, strict SCRFD
and AdaFace CUDA inference on an L4, a one-video canary, and a ten-video controlled
drain on 2026-09-02. Both managed rollouts reconciled without missing database commits,
source-provenance mismatches, dead-letter items, or lingering staging objects. It is
the approved technical candidate for the remaining-manifest rollout; the operator
approvals listed below still gate that launch.

## Terraform and IAM changes

### APIs and infrastructure

- enable `run.googleapis.com`;
- create the Cloud Run v2 Job;
- attach Direct VPC egress to the existing subnet;
- expose job name and region as Terraform outputs;
- retain all Batch resources during migration;
- manage Cloud Monitoring incident policies and the operator email notification channel
  in Terraform.

Check subnet address utilization before apply. Initial parallelism of one has modest
address demand, but Direct VPC egress consumes subnet addresses and future concurrency
must account for that.

### Runtime service account

The existing runtime identity already has Cloud SQL client, Cloud SQL instance-user,
Secret Manager accessor, logging, and application database grants. Add conditional
`roles/storage.objectViewer` access to only the `videos/` prefix so the remote worker
can stage its own source. Retain its conditional object-user access to
`face-staging/`.

Do not grant bucket-wide administration, project Editor/Owner, access to the PostgreSQL
administrator-password secret, or service-account key creation.

### Developer/deployer account

The developer needs narrowly scoped permission to create/update and run the Cloud Run
Job, read its executions/logs, push the image, and act as the runtime service account.
The existing Cloud SQL and Artifact Registry permissions remain. Prefer a predefined
Cloud Run developer role plus explicit service-account-user binding over project-wide
Editor.

Before applying, inspect the Terraform plan to confirm that the shared bucket, Cloud SQL
instance, CSEK secret, and existing Batch infrastructure are not replaced or destroyed.

## Enqueue and execution commands

Extend the CLI with separate preparation and launch actions:

```text
face-ingest enqueue-object gs://teak-banner-dome-bulk-videos/videos/OBJECT.mp4

face-ingest enqueue-manifest \
  --manifest ../bulk-download/data/manifest.jsonl \
  --select-file selected-uids.txt \
  --name controlled-cloud-run-01

face-cloud-run start --rollout-id ROLLOUT_ID --tasks 1 --parallelism 1
face-cloud-run status --rollout-id ROLLOUT_ID
face-cloud-run reconcile --rollout-id ROLLOUT_ID
```

Manifest parsing and deduplication must retain the rules in the main implementation
plan. Enqueueing is transactional and idempotent. Starting a job is a distinct action,
so an operator can review the item count and image/config versions first.

For the remaining corpus, calculate task count from measured drain time. Based on the
current benchmark, begin with about 30 sequential tasks. Each task can drain for roughly
50 minutes; surplus tasks discover an empty queue and exit quickly. Do not increase
parallelism above one merely to accelerate the first production run.

## Matching and concurrency safety

`FACE_MATCHING_ENABLED=true` is a deployment invariant. Enqueue, launcher, and worker
must all reject a production rollout if it is false. Persist the value and threshold
version on the rollout and processing records.

Initial `parallelism = 1` is required because concurrent unmatched tracks could both
query the same gallery state and create duplicate subjects. It also protects the small
Cloud SQL tier from connection and write pressure. Before increasing parallelism:

1. define and implement subject-creation serialization or advisory locking;
2. test concurrent attempts against the same known and unknown face;
3. establish acceptable duplicate/merge behavior;
4. load-test pgvector queries and Cloud SQL connection counts;
5. upgrade Cloud SQL if measured utilization requires it.

## Unattended operation and observability

The launcher must return the Cloud Run execution name and rollout ID once the managed
execution has started. No local polling process is required for continuity.

Emit structured, non-sensitive logs containing rollout ID, work-item ID, attempt,
state transition, sanitized error code, source byte count, and elapsed time. Do not log
the CSEK, embeddings, media, or sensitive metadata.

Create monitoring/alerting for:

- successful durable rollout completion, prompting final reconciliation;
- Cloud Run execution or task failure;
- any dead-letter item;
- a lease with no heartbeat beyond its expiry;
- no rollout progress for a defined interval;
- Cloud SQL connection saturation or storage pressure;
- GPU task count and accumulated runtime.

Cloud Run execution success is not sufficient evidence that the rollout succeeded.
The reconciliation command must verify:

- every requested unique item is accounted for;
- no item remains pending, retryable, or leased;
- no dead letters exist;
- each succeeded queue item references a committed, succeeded processing job;
- staging objects were deleted after commits;
- source objects remain unchanged;
- model, threshold, and matching configuration are uniform.

A finalizer may mark the rollout `succeeded` only after those checks. Otherwise mark it
`failed` or leave it running for retries.

## Implementation sequence

### 1. Queue schema and grants

- add the rollout and work-item migration;
- update `scripts/db_grants.sql` for the existing runtime IAM database user;
- implement transactional claim, heartbeat, completion, retry, and reconciliation;
- add unit and database integration tests.

No video processing behavior changes in this step.

### 2. Queue-aware worker

- extract the current single-video operation behind a callable interface used unchanged
  by the local CLI, Batch fallback, and Cloud Run drain loop;
- implement `worker.cloud_run_drain`;
- implement just-in-time CSEK source staging;
- add time-budget, retry classification, lease heartbeat, and clean shutdown behavior;
- preserve the existing local and Batch entry points.

### 3. Terraform and IAM

- enable Cloud Run API;
- add prefix-conditioned source read permission;
- add Cloud Run deploy/run permissions for the developer;
- define the generic GPU Job with Direct VPC egress;
- validate a plan containing no unexpected replacement or deletion.

### 4. Build and preflight

- build and publish a new immutable image digest;
- run unit tests and CPU-safe contract tests locally;
- run strict CUDA/model preflight in a one-task Cloud Run execution;
- record the verified digest in configuration and the rollout row.

### 5. Canary

- enqueue one known test video;
- run one task with parallelism one;
- verify CSEK source/staging behavior, CUDA providers, mandatory matching, private Cloud
  SQL, committed rows, staging deletion, source immutability, and idempotent rerun;
- close or disconnect the local terminal after launch and confirm completion remotely.

### 6. Controlled run

- enqueue ten unprocessed manifest items;
- run enough sequential drain tasks to finish them;
- exercise one forced task termination and verify lease recovery;
- reconcile database, storage, task logs, time, and cost;
- stop if any acceptance gate fails.

### 7. Remaining manifest

- take a database backup or verified recovery point;
- enqueue only manifest records not already successfully processed;
- review exact count, digest, matching threshold/version, and estimated ceiling;
- launch approximately 30 tasks at parallelism one, adjusted by controlled-run timing;
- rely on managed execution and alerts rather than an attached terminal;
- reconcile before declaring completion.

### 8. Cleanup after acceptance

- retain the old image digest and Batch submission path through a defined rollback
  window;
- then remove Batch-only APIs/IAM/resources in a separately reviewed Terraform change;
- reassess whether Cloud SQL's public IP is still needed for local administration;
- reassess service-account separation if probe serving or additional services are
  introduced.

## Testing requirements

In addition to existing tests, cover:

- duplicate enqueue and launch retries;
- two claimers never receiving the same live lease;
- expired lease reclamation;
- heartbeat ownership enforcement;
- task termination before staging, during inference, and after DB commit;
- retry after staging lifecycle deletion;
- checksum and CSEK metadata failures;
- maximum-attempt dead-letter behavior;
- worker refusal when matching is disabled;
- worker refusal outside the approved bucket/prefixes;
- hard timeout avoided by the soft deadline/admission guard;
- Cloud SQL only reachable through the configured private route from Cloud Run;
- local disconnection has no effect on an active execution;
- reconciliation detects missing, duplicate, or inconsistent records;
- no secrets or biometric payloads appear in logs, job configuration, or Terraform
  state.

## Cost and duration estimate

Cloud Run GPU Jobs with disabled zonal redundancy currently require a minimum of 4 vCPU
and 16 GiB with one L4. At published list rates this is approximately **$0.91 per task
hour** before ancillary logging/network/storage costs. Jobs have a one-minute minimum
instance charge.

The final ten-video controlled drain processed 185,221,782 bytes in about 194 seconds
on one warm L4, or roughly 0.95 MB/s. Applying that measured rate to the point-in-time
remaining corpus gives roughly 14–15 GPU-hours of pure work. Allowing for cold starts,
task turnover, variance in video complexity, and retry headroom, the planning range is:

- approximately **16–20 task-hours** including startup and processing overhead;
- approximately **$15–$19** for Cloud Run CPU, memory, and L4 time at list price;
- less than roughly **$0.45** if up to 30 surplus one-minute tasks encounter an empty
  queue;
- existing Cloud SQL charges and normal GCS operation/logging charges excluded.

Treat this as a planning estimate, not a guarantee. Video content, not only bytes, can
materially change inference time. Recalculate from the final generated selection before
launch. The design has configurable maximum attempts and task count; billing-budget
alerts are useful warnings but are not a hard spending cap.

Cloud Run is not necessarily cheaper per active GPU-hour than a continuously utilized
Batch VM, and it does not provide the same Spot-GPU discount. Its expected advantage
here is quicker managed startup, simpler orchestration, and independence from a local
submitter. A persistent optimized Batch worker may remain less expensive if the corpus
or steady-state workload grows substantially.

## Rollback

Do not allow Batch and Cloud Run to process the same rollout independently. To roll
back:

1. stop starting new Cloud Run executions;
2. allow running tasks to finish or cancel the execution;
3. wait for leases to expire and reconcile committed work;
4. return retryable items to the queue or derive a selection file containing only
   incomplete items;
5. submit those items through the preserved Batch path;
6. verify existing processing idempotency keys prevent duplicate observations.

Never delete committed application rows or immutable source objects as part of a
rollback.

## Acceptance criteria

The migration is ready for the remainder of the corpus when:

- a one-video and ten-video execution both pass reconciliation;
- CUDA SCRFD and AdaFace inference run on the L4;
- `FACE_MATCHING_ENABLED=true` and the approved threshold version are persisted;
- all GCS reads, staging copies, and deletions enforce the CSEK and prefix allowlists;
- Cloud SQL uses private connectivity and automatic IAM authentication;
- retries are idempotent and task termination recovery is proven;
- no face crops, raw media, credentials, or CSEK values enter Cloud SQL or logs;
- an execution demonstrably continues after the local session disconnects;
- the existing local single-file and manifest-selection commands still run the same
  processing pipeline successfully;
- alerts and a documented status/reconciliation command are available;
- controlled-run time and cost fall within the approved operational ceiling.

## Remaining operational approvals

The conditional read-only runtime grant for `videos/` has been applied and proven by
the managed canary and controlled rollout. No additional IAM permission is currently
known to be required for the selected design.

Before the final bulk launch—not before writing or testing the migration—the operator
must also approve:

- the exact image digest above (confirm that it remains the rollout candidate);
- the measured task count and spending ceiling derived from the controlled run
  (16–20 task-hours approved on 2026-09-02);
- the alert notification destination (`jack@jackstruck.info` configured on 2026-09-02);
- the final list of manifest work items.

Preparation status as of 2026-09-02: the 16–20 task-hour ceiling is approved, the
operator email notification channel is applied, and on-demand Cloud SQL backup
`1788375603542` completed successfully. The immutable digest and exact 1,859-item
selection were approved and launched as rollout
`f01c13b8-318f-4df1-944b-41daa3f67faa`, Cloud Run execution
`face-batch-gpu-drain-ktrhl`, with 30 tasks at parallelism one.

Keep initial parallelism at one and keep Cloud SQL's connector-only public IP during the
migration. Both can be reconsidered after the bulk run; neither blocks this design.

## Official references

- [Configure GPUs for Cloud Run Jobs](https://cloud.google.com/run/docs/configuring/jobs/gpu)
- [Create and execute Cloud Run Jobs](https://cloud.google.com/run/docs/create-jobs)
- [Cloud Run quotas and limits](https://cloud.google.com/run/quotas)
- [Direct VPC egress](https://cloud.google.com/run/docs/configuring/vpc-direct-vpc)
- [Cloud Run pricing](https://cloud.google.com/run/pricing)
- [Configure secrets for Cloud Run Jobs](https://cloud.google.com/run/docs/configuring/jobs/secrets)

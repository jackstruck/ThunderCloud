# Outstanding Work Before Remote Bulk Processing

> Operator handoff: after the production execution was launched, the operator directed
> that subsequent work be documentation-only. Do not poll, reconcile, restart, cancel,
> or otherwise modify Cloud Run, Cloud SQL, GCS, IAM, Monitoring, or Terraform unless
> the operator explicitly authorizes a new operational action.

> Cloud Run migration update (2026-09-02): the durable Cloud SQL queue, shared local and
> remote worker code, CSEK just-in-time staging, Direct VPC Cloud Run L4 Job, prefix-only
> runtime source access, immutable queue-aware image, and visible Monitoring incident
> policies are implemented. Final digest `sha256:eefc55e1e48a9e5d367ce1f9dac7f7be4a13c1a289ca0fc565d9aa668c91955c`
> passed a one-video L4 canary and a ten-video controlled drain. Both reconciled with
> zero missing commits, source-provenance mismatches, dead-letter items, or lingering
> staging objects. Cloud Run is now the primary orchestrator; the Batch discussion
> below is retained as rollback history.

The implementation has no known missing IAM permissions. The operator approved the
16–20 task-hour planning ceiling, immutable r4 digest, and exact 1,859-item selection
on 2026-09-02. The Cloud Monitoring email channel for `jack@jackstruck.info` is
configured and attached to the execution-failure, dead-letter, and successful-completion
policies. Parallelism remains
deliberately fixed at one. The controlled rollout
completed all ten videos on one warm L4 in about 3 minutes 14 seconds of active drain
time (about 6 minutes 14 seconds from local launch through queue completion). The
manifest/database selection currently resolves to 1,859 unique unprocessed UIDs; this
count must be regenerated and explicitly confirmed immediately before bulk enqueueing.

The prepared selection is
`selections/cloud-run-remaining-20260902.txt` (mode `0600`, 1,859 lines, SHA-256
`3a463e5ed0c786c9b81dd70c558808b2f116c798b2fbf3b59057ac78a3279ed6`).
It is a UID filter over the authoritative manifest, not a copy or replacement of that
manifest. Enqueueing resolves each UID back to its full immutable manifest record.

Cloud SQL on-demand backup `1788375603542`, description
`pre-cloud-run-remaining-20260902`, completed successfully on 2026-09-02 before the
remaining-corpus enqueue. It uses the database instance's existing customer-managed
KMS key. The intentionally interrupted recovery canary was marked cancelled and only
its exact ephemeral staging generation was removed; its immutable source remains in
the prepared selection. There were zero active rollouts immediately before production
launch.

The approved remaining-corpus rollout was enqueued and launched on 2026-09-02. Rollout
ID `f01c13b8-318f-4df1-944b-41daa3f67faa` contains 1,859 work items and is attached to
Cloud Run execution `face-batch-gpu-drain-ktrhl`, with 30 tasks and parallelism one.
This managed execution continues independently of the local terminal. Final
reconciliation and post-acceptance cleanup remain pending until it completes.
The completion policy triggers when a final `drain_finished` event reports durable
rollout status `succeeded`; its message directs the operator to run the documented
reconciliation command.

The local milestone succeeded end to end: one CSEK-encrypted video was staged,
processed locally, committed to Cloud SQL, exported to the local `data/` directory,
and removed from `face-staging/`. The manifest contains 1,875 usable completed videos
totaling 47.85 GiB, leaving 1,874 videos after the test.

The historical Batch implementation record below predates the Cloud Run migration. It
is not the current launch checklist and remains here only for rollback and audit context.

## 1. Build and validate the GPU image

Local build and publication are complete. The CUDA 13.0/cuDNN 9 image pins
`onnxruntime-gpu==1.29.0`, packages the approved SCRFD and AdaFace checkpoints, verifies
their SHA-256 values during the build, and retains BGR preprocessing. Its packaging
verification passed for both model graphs and confirmed that the ONNX Runtime build
exposes `CUDAExecutionProvider`. The candidate is published immutably as:

```text
us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:5da8096026d0ed126fdd99a7f2d04e8e3b4d09fd47c87fb372495054b1f0cd69
```

The CUDA 13 image passed strict remote execution of both models on an L4 on
2026-09-02. These validation gates remain:

- compare track counts, embeddings, and quality outputs with the successful local
  baseline;
- submit Batch jobs by the immutable digest above, not its build tag.

This replacement digest includes both Secret Manager retrieval and configurable private
Cloud SQL routing. Its packaging, model contracts, remote configuration, and in-memory
secret retrieval passed local validation.

## 2. Provision a dedicated Batch runtime identity

Terraform provisioning is complete. Batch is enabled and the keyless runtime identity
`face-batch-runtime@teak-banner-dome.iam.gserviceaccount.com` exists with only:

- Batch agent reporting and Cloud Logging write access at project scope;
- Cloud SQL client and instance-user access at project scope;
- Artifact Registry read access on `face-batch-worker`;
- `roles/storage.objectUser` conditioned to objects under `face-staging/`;
- a `CLOUD_IAM_SERVICE_ACCOUNT` user on `face-batch-pg`.

The account has zero user-managed service-account keys. Your developer account can
attach it to a Batch job but was not granted token-creation or key-management access.
Terraform reports no drift after the apply.

The database grants are complete and verified: the runtime user has CONNECT on
`face_index`, USAGE on `public`, and SELECT/INSERT/UPDATE/DELETE without grant option on
the five application tables. No ownership, DDL, role-management, or grant privileges
were assigned.

The first canary must also confirm from the runtime identity that exact staging-object
operations succeed while bucket enumeration and access outside `face-staging/` fail.

## 3. Deliver the CSEK securely to the remote worker

Secret Manager delivery is implemented. Terraform creates the regional
`face-batch-gcs-csek` secret container, DATA_READ/DATA_WRITE audit logging, and one
secret-level accessor binding for the Batch runtime account. The payload was added as
version 1 directly through the API, outside Terraform, with CRC32C and byte-for-byte
verification. Terraform state contains no secret version or payload.

The worker accepts `FACE_CSEK_SECRET=face-batch-gcs-csek`, retrieves `latest` directly
into memory, strictly validates it as a Base64 AES-256 key, and retains file-based loading
for local execution. The payload is not placed in the image, Batch arguments, environment
values, object metadata, logs, or database records.

The first canary must exercise retrieval as the actual runtime identity and confirm that
it cannot access any other secret. The developer/project administrators retain their
inherent administrative access; no additional developer secret-access binding was added.

## 4. Switch Batch database access to private IP

The worker now validates `FACE_CLOUD_SQL_IP_TYPE` as `PUBLIC` or `PRIVATE` and passes the
selected route to the Cloud SQL connector. Local execution defaults to `PUBLIC`; Batch
must set `PRIVATE`. Unit and packaged-image configuration checks pass, and the replacement
immutable image contains this change.

The remote canary still must verify IAM authentication and pgvector operations through
the Batch subnet. Keep Cloud SQL public IP enabled for connector-only local administration
until that remote path is proven; there are still no authorized networks.

## 5. Finish the Batch submission and orchestration path

`scripts/submit_batch.py` now supports a controlled manifest rollout with immutable-image
enforcement, CSEK staging, persisted job/application IDs, mandatory matching, private
Cloud SQL configuration, a strict CUDA preflight, bounded concurrency, monitoring,
sanitized failures, and stop-on-first-infrastructure-error behavior. Retry/resumption
for a larger production run remains unfinished.

- supply the complete non-secret worker configuration;
- integrate remote CSEK retrieval;
- resolve selected records from `bulk-download/data/manifest.jsonl`;
- preserve selection by UID and exact object URI;
- stage each immutable source safely under a collision-resistant `face-staging/` name;
- submit jobs using the staged generation, source provenance, and expected SHA-256;
- record submitted Batch job names and application job IDs;
- monitor terminal state and report sanitized failure reasons;
- support retry-safe resumption without duplicating successful database results;
- bound submission rate and concurrency;
- retain Spot as the default and define an explicit on-demand fallback policy;
- ensure successful jobs delete only their validated staging object.

## 6. Validate mandatory cross-video matching

Bulk and canary processing must use:

```text
FACE_MATCHING_ENABLED=true
```

The remote submitter must reject bulk jobs when matching is disabled. Before the canary:

- validate the checkpoint on representative footage;
- calibrate the subject-matching threshold;
- assign a non-placeholder `FACE_THRESHOLD_VERSION`;
- define false-accept/false-reject acceptance criteria;
- verify that matches update the intended subject rather than creating duplicates;
- implement an authorized review, split, and merge workflow for clustering errors.

Mandatory subject matching does not enable real-world identity assertions. Associating
an anonymous subject with an `identity` remains a separate authorized action.

## 7. Validate remote capacity, retry behavior, and cost

A 10-video controlled rollout was attempted on 2026-09-01 with concurrency 2. The first
two inputs were staged and their Spot L4 jobs were accepted, but no VM started. Batch
reported both `CODE_GCE_QUOTA_EXCEEDED` and
`CODE_GCE_ZONE_RESOURCE_POOL_EXHAUSTED`. Project quota inspection confirmed
`GPUS_ALL_REGIONS: limit=0`; the regional `NVIDIA_L4_GPUS` and
`PREEMPTIBLE_NVIDIA_L4_GPUS` counters each show limit 1, but the global zero is
authoritative. Both jobs were deleted, the remaining eight were not staged, and no worker
or database write occurred. The two encrypted staging objects remain available for retry
and are covered by the one-day staging lifecycle rule.

On 2026-09-02, the Cloud Quotas API was enabled and authenticated quota adjustments
were submitted for `GPUS (all regions)` from 0 to 2 and `Preemptible NVIDIA L4 GPUs`
in `us-central1` from 1 to 2. Google processed and denied both requests immediately, so
the effective limits remained 0 and 1 respectively. After the account was enabled, both
requests were resubmitted on 2026-09-02 and Google denied them again. The effective
limits still remained 0 and 1 respectively. A reduced request for one global GPU was
then approved on 2026-09-02 and verified through the Compute API
(`GPUS_ALL_REGIONS: limit=1, usage=0`). Controlled processing must use concurrency 1;
regional Spot capacity must still be confirmed at runtime.

The 10-video controlled rollout was retried with concurrency 1 on 2026-09-02. Spot L4
capacity became available and the first job reached `RUNNING`, proving that both global
and regional quota now permit the VM. The strict GPU preflight then failed on all three
task attempts because the worker image's ONNX Runtime requires CUDA 13 and could not
load `libcublasLt.so.13`. The worker never processed the video, the remaining nine jobs
were not submitted, and the failed input remains in CSEK-encrypted staging for retry or
lifecycle cleanup.

The image was rebuilt on CUDA 13.0/cuDNN 9, published under the replacement digest
above, and passed strict CUDA execution on an L4. After correcting the Batch runnable to
invoke `python -m worker` explicitly, the canary and two additional controlled videos
completed successfully, including matching, Cloud SQL commit, and staging cleanup. The
next on-demand job was stopped before allocation by `GPU resource pool exhausted`, so
six later controlled inputs were not submitted. Three of the original ten controlled
videos are complete; capacity retry policy remains outstanding.

Before submitting the full remainder:

- confirm L4 quota and Spot capacity in `us-central1`;
- benchmark `g2-standard-4` and `g2-standard-8` with the same clip;
- measure VM startup, driver initialization, image pull, decode, detection, embedding,
  database, and total wall-clock time separately;
- verify Spot interruption and automatic retry;
- verify resubmitting a successful source remains idempotent;
- verify a failed pre-commit run leaves staging for retry/lifecycle cleanup;
- verify a successful commit removes its staging object;
- load-test safe parallelism against the current `db-f1-micro` Cloud SQL instance;
- add per-job progress, runtime, failure, and cleanup metrics;
- confirm no secrets, embeddings, crops, or media bytes appear in Cloud Logging.

## 8. Define retention and recovery policy

Approve retention/deletion periods for:

- source-derived embeddings and anonymous subjects;
- match decisions and job metadata;
- Cloud SQL backups and point-in-time recovery logs;
- locally generated review crops;
- locally generated ephemeral probe review crops and JSON output.

The `face-staging/` lifecycle fallback remains restricted to that prefix. Local probe
media and results are never uploaded or persisted, so they have no server-side
retention or deletion mechanism.

## Recommended rollout

1. Complete the GPU image, runtime identity, Secret Manager delivery, private database
   path, and Batch submitter.
2. Run the same known clip remotely and compare it with local job
   `77c61231-e2b6-44bd-ba67-d034dfd295b0`.
3. Test interruption, retry, idempotency, database commit, and staging cleanup.
4. Run a canary batch of 10–20 videos with bounded concurrency.
5. Review performance, cost, track fragmentation, database load, and logs.
6. Process the remaining selected manifest records only after the canary passes.

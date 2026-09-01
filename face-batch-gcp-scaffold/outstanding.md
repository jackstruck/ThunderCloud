# Outstanding Work Before Remote Bulk Processing

The local milestone succeeded end to end: one CSEK-encrypted video was staged,
processed locally, committed to Cloud SQL, exported to the local `data/` directory,
and removed from `face-staging/`. The manifest contains 1,875 usable completed videos
totaling 47.85 GiB, leaving 1,874 videos after the test.

The remote worker is not ready for the bulk run until the following work is complete.

## 1. Build and validate the GPU image

The current image installs CPU `onnxruntime`, and the ignored ONNX checkpoints are not
packaged in the image.

- install a CUDA-compatible `onnxruntime-gpu` environment;
- package the exact approved SCRFD and AdaFace checkpoints in the image;
- pin and verify both model SHA-256 hashes during the build;
- retain the configured BGR preprocessing required by the selected AdaFace export;
- validate that ONNX Runtime selects `CUDAExecutionProvider` on an L4;
- run the known test clip and compare track counts, embeddings, and quality outputs with
  the successful local baseline;
- push the image to Artifact Registry and submit Batch jobs by immutable image digest,
  not a mutable tag.

## 2. Provision a dedicated Batch runtime identity

Terraform does not yet create the remote runtime identity or its Batch-specific IAM.

- enable the Batch API;
- create a dedicated Batch worker service account;
- grant only the required Batch job-agent/reporter permissions;
- grant Artifact Registry read access;
- grant prefix-scoped access required to create, read, and delete only
  `face-staging/` objects;
- grant Cloud SQL client and instance-user roles;
- create the corresponding Cloud SQL IAM database user;
- apply the same least-privilege table grants used by the local developer account;
- do not create or download a service-account JSON key;
- verify the runtime identity cannot enumerate unrelated buckets or resources.

## 3. Deliver the CSEK securely to the remote worker

The remote worker cannot use the local file at
`/workspaces/ThunderCloud/.secrets/gcs-csek.base64`.

- create a Secret Manager secret outside Terraform state;
- add the existing Base64 CSEK without printing it or placing it in shell history;
- grant access only to the dedicated Batch worker service account;
- retrieve it at runtime directly into memory or a protected temporary file;
- never place the value in the container image, Terraform state, Batch arguments,
  environment values, object metadata, application logs, or database records;
- verify the worker can access only the specific approved secret.

## 4. Switch Batch database access to private IP

`worker/db.py` currently forces the Cloud SQL public-IP connector path.

- make the connector IP type configurable;
- retain public connector access for local development;
- configure Batch to use the existing Cloud SQL private IP through the Batch subnet;
- verify IAM authentication and pgvector operations from the Batch worker;
- disable Cloud SQL public IP only after local administration no longer requires it.

## 5. Finish the Batch submission and orchestration path

`scripts/submit_batch.py` is an integration scaffold, not a production bulk submitter.

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
- future persistent probe media, embeddings, and ranked result snapshots.

The `face-staging/` lifecycle fallback must remain restricted to that prefix. Future
durable `face-probes/` media must use the CSEK and must not inherit the one-day staging
lifecycle rule.

## Recommended rollout

1. Complete the GPU image, runtime identity, Secret Manager delivery, private database
   path, and Batch submitter.
2. Run the same known clip remotely and compare it with local job
   `77c61231-e2b6-44bd-ba67-d034dfd295b0`.
3. Test interruption, retry, idempotency, database commit, and staging cleanup.
4. Run a canary batch of 10–20 videos with bounded concurrency.
5. Review performance, cost, track fragmentation, database load, and logs.
6. Process the remaining selected manifest records only after the canary passes.

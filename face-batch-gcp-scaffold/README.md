# Local-first historical face-video processing

This project processes explicitly selected CSEK-encrypted historical videos with the
same containerized worker locally or as an unattended Cloud Run GPU Job, while storing
durable embeddings, queue state, and provenance in Cloud SQL PostgreSQL/pgvector. See
`IMPLEMENTATION_PLAN.md` for the original architecture and
`CLOUD_RUN_IMPLEMENTATION_PLAN.md` for the managed-worker migration.
See `CLEANUP_ASSESSMENT.md` for what can be removed now versus what must remain through
bulk reconciliation and the rollback window.

## Current topology

- Project: `teak-banner-dome`
- Bucket: `gs://teak-banner-dome-bulk-videos`
- Immutable inputs: `videos/`
- Ephemeral CSEK staging: `face-staging/`
- Regional resources: `us-central1`
- Worker: local process/container or Cloud Run Job using the same processing modules
- Local review output: retained face crops under `data/<job-id>/`
- Database: Cloud SQL PostgreSQL 17, public connector path with no authorized networks, automatic IAM database authentication

The application validates prefixes again in code. It only deletes an object under
`face-staging/`, and only after a successful or previously successful idempotent
database transaction. Cloud Run obtains the CSEK from Secret Manager directly into
memory and reaches Cloud SQL over Direct VPC egress and private IP.

## Prerequisites

1. Python 3.12 and FFmpeg.
2. Google Application Default Credentials for the developer account.
3. Terraform 1.5+ and `gcloud` for provisioning/verification.
4. The CSEK file at `/workspaces/ThunderCloud/.secrets/gcs-csek.base64`, mode `0600`.
5. Licensed SCRFD and AdaFace/CVLFace ONNX checkpoints. Model files are intentionally ignored by Git and are not downloaded automatically.

Never print the CSEK, place it in `.env`, pass its value as an argument, bake it into an image, or put it in Terraform state.

## Install

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
# Edit only non-secret settings, then export them using your preferred local env loader.
```

CPU execution is supported for functional validation. For NVIDIA execution, install a CUDA-compatible ONNX Runtime environment.

## Provision cloud resources

Review the import and plan carefully because Terraform adopts the existing archive bucket to add only the prefix-scoped lifecycle and IAM configuration:

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Set developer_email and review every value.
terraform init
terraform plan -out face-batch.tfplan
terraform show face-batch.tfplan
terraform apply face-batch.tfplan
```

Do not apply a plan that deletes the bucket, changes its location/encryption, or modifies unrelated lifecycle rules. The bucket resource has `prevent_destroy`, and the staging deletion rule matches only `face-staging/`.

## Bootstrap Cloud SQL

Apply the additive schema and least-privilege grants with the connector-based migration
utility. It reads the administrator password from Secret Manager without printing or
persisting it:

```bash
python scripts/apply_db_migrations.py \
  --app-user YOUR_DEVELOPER_ACCOUNT \
  --app-user face-batch-runtime@teak-banner-dome.iam
```

The worker uses the developer IAM database user after bootstrap; it does not use a static database password.

Cross-video subject matching is required (`FACE_MATCHING_ENABLED=true`) for canary and bulk processing. A representative validation set, calibrated threshold, and non-placeholder threshold version are required before that rollout. Subject clustering does not itself authorize association with a real-world identity.

## Run locally

Select one exact object:

```bash
scripts/run_local.sh submit-object \
  gs://teak-banner-dome-bulk-videos/videos/OBJECT.mp4 \
  --sha256 HEX_DIGEST_IF_KNOWN
```

`run_local.sh` is convenient for editable development. To execute the packaged worker image instead, first build it and then use the container launcher:

```bash
docker build -t thundercloud-face-batch:local .
scripts/run_local_container.sh submit-object \
  gs://teak-banner-dome-bulk-videos/videos/OBJECT.mp4
```

The container launcher mounts only the CSEK file and developer ADC file as read-only secret files, plus read-only manifest/selection paths. It does not place either credential in the image or command line.

Or create a selection file containing one UID or exact object URI per line and use the existing append-only manifest:

```bash
scripts/run_local.sh submit-manifest \
  --manifest ../bulk-download/data/manifest.jsonl \
  --select-file selected-uids.txt
```

The CLI resolves usable manifest records, copies each selected object to a unique CSEK staging name, launches the worker, commits all track results atomically, and deletes the staging copy. A failed pre-commit run deliberately leaves staging for lifecycle cleanup/retry.

When `FACE_OUTPUT_DIR` is set, each job also writes its retained best face crops to a
job-specific directory. Each directory contains a JSON manifest with track, timestamp,
quality, and source provenance. These local biometric artifacts are mode `0600` and are
not uploaded to GCS.

## Tests

```bash
python -m pytest
python -m compileall worker tests
PYTHON_BIN=.venv/bin/python scripts/test_db_integration.sh
```

Cloud integration tests are skipped unless explicitly enabled and configured. The CSEK is never read by unit tests.

## Run with Cloud Run

Cloud Run is an orchestration adapter around the same one-video processor. Enqueueing
creates durable rollout/work-item rows but does not start compute. Remote rollout
arguments require the exact image and all behavior-affecting versions explicitly:

```bash
face-ingest enqueue-manifest \
  --manifest ../bulk-download/data/manifest.jsonl \
  --select-file selected-uids.txt \
  --name controlled-cloud-run \
  --request-key controlled-cloud-run-v1 \
  --image-digest IMAGE_DIGEST \
  --creator-principal YOUR_DEVELOPER_ACCOUNT \
  --worker-version 0.2.0-cloud-run-r4 \
  --detector-version scrfd-10g-5838f7fe \
  --embedding-model-version adaface-ir18-6b6a3577 \
  --threshold-version controlled-eval-0p55-v1

face-cloud-run status --rollout-id ROLLOUT_ID
face-cloud-run start --rollout-id ROLLOUT_ID --tasks 1 --parallelism 1
face-cloud-run reconcile --rollout-id ROLLOUT_ID
```

The start command returns after Google accepts the execution. The job continues without
the local terminal. Parallelism is deliberately locked to one until subject-creation
concurrency and the small Cloud SQL tier have been validated. The existing Batch path
is retained as rollback during this migration.

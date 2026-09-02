# Local test commands

Run these commands from `/workspaces/ThunderCloud/face-batch-gcp-scaffold`.

## One-time setup

Create the environment file if it does not already exist, then fill in the Cloud SQL connection, database user, and model paths:

```bash
cd /workspaces/ThunderCloud/face-batch-gcp-scaffold
test -f .env || cp .env.example .env
```

Authenticate the development account for Application Default Credentials if needed:

```bash
gcloud auth application-default login
```

For a direct local Python run, create and install the editable environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Process the first video

This is the first usable `status: "complete"` video in `../bulk-download/data/manifest.jsonl`:

```text
gs://teak-banner-dome-bulk-videos/videos/dd9e430f-9636-5068-9e0f-7ebdae82357e.mp4
```

Run it directly in the local Python environment:

```bash
cd /workspaces/ThunderCloud/face-batch-gcp-scaffold
. .venv/bin/activate
set -a
. ./.env
set +a
scripts/run_local.sh submit-object \
  gs://teak-banner-dome-bulk-videos/videos/dd9e430f-9636-5068-9e0f-7ebdae82357e.mp4 \
  --sha256 f5bcf982dbb4f93fd19286193eee62b39858c3eaf99944a2ba0aec0729708b1c
```

The worker reads the source with the configured CSEK, writes staging/results to the remote bucket, records progress in Cloud SQL, and exports retained face crops under `data/<job-id>/`. The secret itself must remain only at the configured local path and must never be printed or copied into `.env`.

## Container alternative

After `.env` and Application Default Credentials are configured:

```bash
cd /workspaces/ThunderCloud/face-batch-gcp-scaffold
docker build -t thundercloud-face-batch:local .
scripts/run_local_container.sh submit-object \
  gs://teak-banner-dome-bulk-videos/videos/dd9e430f-9636-5068-9e0f-7ebdae82357e.mp4 \
  --sha256 f5bcf982dbb4f93fd19286193eee62b39858c3eaf99944a2ba0aec0729708b1c
```
# Cloud Run queue migration

The local one-video commands below remain supported. Cloud Run uses the same shared
processing code through a queue-draining entry point.

Apply the additive database schema and runtime grants before creating a rollout:

```bash
cd /workspaces/ThunderCloud/face-batch-gcp-scaffold
. .venv/bin/activate
python scripts/apply_db_migrations.py \
  --app-user jack@jackstruck.info \
  --app-user face-batch-runtime@teak-banner-dome.iam
```

Enqueue selected manifest records without starting compute:

```bash
FACE_MATCHING_ENABLED=true face-ingest enqueue-manifest \
  --manifest ../bulk-download/data/manifest.jsonl \
  --select-file selected-uids.txt \
  --name cloud-run-canary \
  --request-key cloud-run-canary-v1 \
  --image-digest IMAGE_DIGEST \
  --creator-principal YOUR_ACCOUNT \
  --worker-version 0.2.0-cloud-run-r4 \
  --detector-version scrfd-10g-5838f7fe \
  --embedding-model-version adaface-ir18-6b6a3577 \
  --threshold-version controlled-eval-0p55-v1
```

Review and start the managed execution:

```bash
face-cloud-run status --rollout-id ROLLOUT_ID
face-cloud-run start --rollout-id ROLLOUT_ID --tasks 1 --parallelism 1
face-cloud-run reconcile --rollout-id ROLLOUT_ID
```

After the controlled rollout reconciles, generate the full selection with an exact
operator-reviewed count. The command refuses to overwrite an existing file or proceed
if the count has changed:

```bash
python scripts/select_remaining.py \
  --manifest ../bulk-download/data/manifest.jsonl \
  --output selections/cloud-run-remaining.txt \
  --confirm-count EXPECTED_COUNT
```

## Prepared remaining-corpus rollout

The reviewed selection candidate generated on 2026-09-02 contains 1,859 unique,
currently unprocessed manifest UIDs:

```text
selections/cloud-run-remaining-20260902.txt
SHA-256: 3a463e5ed0c786c9b81dd70c558808b2f116c798b2fbf3b59057ac78a3279ed6
```

Re-run `scripts/select_remaining.py` into a new filename immediately before launch if
any intervening processing occurs. After approving the selection and immutable image,
enqueue without starting compute:

```bash
set -a
. ./.env
set +a
face-ingest enqueue-manifest \
  --manifest ../bulk-download/data/manifest.jsonl \
  --select-file selections/cloud-run-remaining-20260902.txt \
  --name cloud-run-remaining-20260902 \
  --request-key cloud-run-remaining-20260902-v1 \
  --image-digest us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:eefc55e1e48a9e5d367ce1f9dac7f7be4a13c1a289ca0fc565d9aa668c91955c \
  --creator-principal jack@jackstruck.info \
  --max-attempts 3 \
  --worker-version 0.2.0-cloud-run-r4 \
  --detector-version scrfd-10g-5838f7fe \
  --embedding-model-version adaface-ir18-6b6a3577 \
  --threshold-version controlled-eval-0p55-v1
```

Review the returned rollout with `face-cloud-run status`. Launch remains a separate,
deliberate action and returns immediately after Cloud Run accepts it:

```bash
face-cloud-run start --rollout-id ROLLOUT_ID --tasks 30 --parallelism 1
```

The active production identifiers are:

```text
rollout:   f01c13b8-318f-4df1-944b-41daa3f67faa
execution: face-batch-gpu-drain-ktrhl
```

After Cloud Run or the configured email channel reports a terminal execution, run the
storage-and-database reconciliation once:

```bash
set -a
. ./.env
set +a
face-cloud-run reconcile \
  --rollout-id f01c13b8-318f-4df1-944b-41daa3f67faa
```

Do not declare the rollout complete unless the JSON result reports all 1,859 requested
items succeeded, zero retryable/dead-letter/active items, zero missing committed
results, zero source-provenance mismatches, zero lingering staging objects, and
`"reconciled":true`.

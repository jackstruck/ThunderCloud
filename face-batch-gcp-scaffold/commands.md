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

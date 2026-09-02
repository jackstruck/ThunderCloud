# Processing Future Videos

This runbook adds new CSEK-protected videos without changing the worker or creating a
new Cloud Run Job. It supports either a one-off object or an append-only manifest batch.
The processing path, Cloud SQL queue, mandatory matching configuration, and final
reconciliation are the same as the initial corpus.

Do not start a new rollout while another rollout is `running`. Initial parallelism is
fixed at one because subject creation and the small Cloud SQL tier have not been approved
for concurrent galleries.

## 1. Put the immutable source under `videos/`

If another trusted ingestion system already wrote the object with the same CSEK, skip
to the metadata requirements below. Otherwise, the following example uploads a local
file without printing or passing the CSEK value in an argument or environment variable.
It refuses to overwrite an existing object by requiring generation zero.

Set only the non-secret source path and destination object name:

```bash
cd /workspaces/ThunderCloud/face-batch-gcp-scaffold
. .venv/bin/activate
export NEW_VIDEO_PATH=/absolute/path/to/new-video.mp4
export NEW_VIDEO_OBJECT=videos/new-video.mp4
```

Run the upload. The script reads the existing local CSEK file directly and prints only
non-secret provenance needed for enqueueing:

```bash
python - <<'PY'
import hashlib
import json
import mimetypes
import os
from pathlib import Path

from google.cloud import storage
from worker.storage import has_customer_encryption, load_csek

project = "teak-banner-dome"
bucket_name = "teak-banner-dome-bulk-videos"
source = Path(os.environ["NEW_VIDEO_PATH"]).resolve(strict=True)
object_name = os.environ["NEW_VIDEO_OBJECT"]
if not object_name.startswith("videos/") or object_name.endswith("/"):
    raise ValueError("NEW_VIDEO_OBJECT must be a file under videos/")

digest = hashlib.sha256()
with source.open("rb") as handle:
    for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
        digest.update(chunk)

key = load_csek(Path("/workspaces/ThunderCloud/.secrets/gcs-csek.base64"))
client = storage.Client(project=project)
blob = client.bucket(bucket_name).blob(object_name, encryption_key=key)
content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
blob.upload_from_filename(
    source,
    content_type=content_type,
    if_generation_match=0,
)
blob.reload()
if not has_customer_encryption(blob):
    raise RuntimeError("uploaded object does not report customer-supplied encryption")

print(json.dumps({
    "object": f"gs://{bucket_name}/{object_name}",
    "sha256": digest.hexdigest(),
    "bytes": int(blob.size),
    "generation": int(blob.generation),
    "content_type": blob.content_type,
}, separators=(",", ":")))
PY
```

Keep the printed provenance with the ingestion record. Treat the object as immutable;
to replace content, upload it under a new object name and UID.

## 2A. Enqueue one object without changing the manifest

Use this for an isolated video. Substitute the values printed by the upload step.
The SHA-256 and byte count are mandatory for remote work:

```bash
set -a
. ./.env
set +a

face-ingest enqueue-object \
  gs://teak-banner-dome-bulk-videos/videos/NEW_OBJECT.mp4 \
  --sha256 SHA256_FROM_UPLOAD \
  --bytes BYTES_FROM_UPLOAD \
  --generation GENERATION_FROM_UPLOAD \
  --content-type video/mp4 \
  --name future-video-YYYYMMDD \
  --request-key future-video-STABLE_UNIQUE_KEY \
  --image-digest us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:eefc55e1e48a9e5d367ce1f9dac7f7be4a13c1a289ca0fc565d9aa668c91955c \
  --creator-principal jack@jackstruck.info \
  --max-attempts 3 \
  --worker-version 0.2.0-cloud-run-r4 \
  --detector-version scrfd-10g-5838f7fe \
  --embedding-model-version adaface-ir18-6b6a3577 \
  --threshold-version controlled-eval-0p55-v1
```

The command creates durable queue state but does not start compute. Save its
`rollout_id`, review `face-cloud-run status --rollout-id ROLLOUT_ID`, and follow the
launch step below.

## 2B. Append records and enqueue a manifest selection

Use this for a repeatable batch or when the video should become part of the canonical
source catalog. Append one JSON object per line to
`../bulk-download/data/manifest.jsonl`; never rewrite historical records. Each new
usable record has this shape:

```json
{"uid":"STABLE_UNIQUE_UID","status":"complete","object":"gs://teak-banner-dome-bulk-videos/videos/NEW_OBJECT.mp4","sha256":"64_HEX_CHARACTERS","bytes":12345678,"generation":1234567890123456,"content_type":"video/mp4","timestamp":"2026-09-02T00:00:00Z"}
```

Required remote fields are `uid`, `status: "complete"`, `object`, `sha256`, and a
positive `bytes` value. Recording `generation`, `content_type`, and capture `timestamp`
is strongly recommended. URLs may be recorded when provenance requires them, but the
parser removes query strings and fragments before persistence.

Create a selection text file containing exactly one new UID per line, then enqueue it:

```bash
face-ingest enqueue-manifest \
  --manifest ../bulk-download/data/manifest.jsonl \
  --select-file selections/future-videos-YYYYMMDD.txt \
  --name future-videos-YYYYMMDD \
  --request-key future-videos-YYYYMMDD-v1 \
  --image-digest us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:eefc55e1e48a9e5d367ce1f9dac7f7be4a13c1a289ca0fc565d9aa668c91955c \
  --creator-principal jack@jackstruck.info \
  --max-attempts 3 \
  --worker-version 0.2.0-cloud-run-r4 \
  --detector-version scrfd-10g-5838f7fe \
  --embedding-model-version adaface-ir18-6b6a3577 \
  --threshold-version controlled-eval-0p55-v1
```

Manifest selection deduplicates by SHA-256 and rejects selectors that do not resolve
to usable `complete` or canonicalized `duplicate` records.

When records were produced by the sibling JustPaste/Luluvid acquisition workflow, use
its `commands.md` handoff. The documented `scripts/select_remaining.py --count-only`
step compares the complete manifest with succeeded Cloud SQL jobs, so the resulting
selection includes newly acquired content without reprocessing completed content.

## 3. Launch and reconcile

For one video or a small future batch, one task can drain multiple items while warm:

```bash
face-cloud-run status --rollout-id ROLLOUT_ID
face-cloud-run start --rollout-id ROLLOUT_ID --tasks 1 --parallelism 1
```

The start command returns after Cloud Run accepts the execution; the local terminal can
disconnect. Cloud Monitoring emails `jack@jackstruck.info` on execution failure,
dead-letter, or durable rollout completion.

After the completion notification, reconcile once:

```bash
face-cloud-run reconcile --rollout-id ROLLOUT_ID
```

Require `"reconciled":true`, all requested items succeeded, and zero retryable,
dead-letter, active, missing-commit, provenance-mismatch, and lingering-staging counts.
If the result is not clean, do not create a replacement rollout blindly; inspect the
existing durable work items and execution first.

## Local alternative

The same new object can be processed locally with developer ADC, the local CSEK, and
the shared worker pipeline:

```bash
scripts/run_local.sh submit-object \
  gs://teak-banner-dome-bulk-videos/videos/NEW_OBJECT.mp4 \
  --sha256 SHA256_FROM_UPLOAD
```

For several manifest UIDs, use `scripts/run_local.sh submit-manifest` with the same
manifest and selection-file pattern. Local retained face crops are written beneath
`data/<job-id>/`; they are not uploaded to GCS.

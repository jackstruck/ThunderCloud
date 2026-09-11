# Processing future videos

This runbook describes the consolidated release deployed on 2026-09-10. New archive videos enter the same run framework as browser submissions.
The deployed CPU and GPU jobs process them; operators do not create per-batch jobs.

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

## 2. Prepare and submit a durable receipt

Use `face-submit prepare` with the exact bucket, object name, generation, SHA-256,
byte count, and content type recorded above. Supply the operator principal and
explicit handling and selection policies. The complete command is in
[Operations: archive submission](OPERATIONS.md#submit-archive-objects-through-the-common-run-framework).
Add `--page-url` for external attribution when available.

Use `--selection-policy all_tracks` for unattended processing. Enrollment creates
one new subject per detected track. Use `manual` to select and group faces in the
console. Choose `enroll_only`, `retain_and_enroll`, or `search_then_discard` according
to the desired result and retention; the original archive object is preserved.

Submit the receipt with `face-submit submit --receipt PATH`. A failed handoff is
retried with that same receipt and request key. The returned run ID is durable.
Keep the upload provenance and receipt together; do not recreate the receipt merely
because the terminal disconnected or an acknowledgement was lost.

## 3. Submit an explicitly selected batch

Keep the acquisition manifest append-only. For each selected complete upload,
prepare one receipt from its exact recorded metadata. Generation and content type
are required by the common archive adapter. Skip objects already represented by a
confirmed submission receipt unless reprocessing is deliberate.

Repeat `--receipt` to submit multiple objects:

```bash
face-submit submit \
  --receipt private-receipts/first.json \
  --receipt private-receipts/second.json \
  --active-run-limit 3
```

## 4. Follow the runs

Open **Check a run** and use the returned run IDs. Inspect failures and retry or
cancel through the common run controls. Scheduled reconciliation recovers durable
operations. A local terminal disconnect does not cancel accepted work. There is no
separate rollout launch or reconciliation command in the consolidated release.

For private local similarity search without enrollment, use `face-probe` as described
in [Operations](OPERATIONS.md). Subject moves and merges use the reviewed correction
workflow rather than an enrollment matching threshold.

# Operations

The consolidated framework is deployed on schema 026. See [architecture](ARCHITECTURE.md),
[current contracts](docs/platform-contracts.md), and [cutover results](docs/platform-cutover-review.md).
Live acceptance and browser review status are recorded in the cutover results.

## Current topology

- Project `teak-banner-dome`, regional services in `us-central1`.
- Archive `gs://teak-banner-dome-bulk-videos/videos/`; original objects are immutable.
- IAP console `face-console`; CPU acquisition/cleanup `face-ingest-drain`; CUDA processing `face-interactive-gpu`.
- Cloud SQL PostgreSQL 17/pgvector, database `face_index`, schema 026, IAM authentication and connector TLS.
- Temporary submissions `submissions-temporary/`, permanent galleries `subject-gallery/`, retained sources `training-media/`.

Cloud jobs use private Cloud SQL connectivity. Developer tools use the existing
public connector path with no authorized IP networks. CSEK material is read from
Secret Manager into memory. CPU acquisition has read access only to the archive
prefix; cleanup deletes only eligible exact generations in the managed prefixes.

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

Use the existing Terraform state and configuration. For a new installation only, initialize values from the example; never overwrite an existing `terraform.tfvars`:

```bash
cd terraform
# On a new installation only: cp terraform.tfvars.example terraform.tfvars
# Set developer_email and review every value.
terraform init
terraform plan -out face-batch.tfplan
terraform show face-batch.tfplan
terraform apply face-batch.tfplan
```

Do not apply a plan that deletes the bucket, changes its location/encryption, or modifies unrelated lifecycle rules. The bucket resource has `prevent_destroy`, and lifecycle rules cover only their explicit ephemeral prefixes.

## Bootstrap Cloud SQL

Apply the ordered schema and shared least-privilege grants with the connector-based migration
utility. Existing databases without a ledger require verified reference adoption; see
[maintenance procedures](maintenance/README.md). It reads the administrator password from Secret Manager without printing or
persisting it:

```bash
python scripts/apply_db_migrations.py \
  --app-user YOUR_DEVELOPER_ACCOUNT \
  --app-user face-batch-runtime@teak-banner-dome.iam
```

The worker uses the developer IAM database user after bootstrap; it does not use a static database password.

## Local operator commands

`scripts/run_local.sh` delegates to `data-submit`; it accepts the same `prepare`
and `submit` arguments shown below. The optional container wrapper uses the same
receipt CLI and developer ADC. Keep receipts in `private-receipts/` or set
`DATA_RECEIPT_DIR` so the wrapper can write them durably; legacy `FACE_RECEIPT_DIR`
also works. It submits work to the
managed jobs; it does not run a separate archive enrollment writer.

## Ephemeral local probes

The `data-probe` command reads a local JPEG, PNG, or MP4, runs the shared inference
pipeline, and performs a read-only search of model-compatible gallery subjects. Probe
media, embeddings, and results are never uploaded or written to Cloud SQL. Results are
ranked candidates rather than automatic identity decisions; each includes the nullable
operator-managed display name and all current succeeded training-track provenance.

The command creates a private local review directory containing `result.json` and the
exact detected face or retained track crops used for comparison:

```bash
data-probe submit --file ./person.jpg --top-k 10
data-probe submit --file ./short-clip.mp4 --top-k 10
data-probe submit --file ./person.jpg --top-k 10 --review-output-dir ./probe-review
data-probe submit --crop-dir ./existing-track-crops --top-k 10
```

Review directories are mode `0700`; JSON and JPEG files are mode `0600` and never
overwritten. Inputs are limited to JPEG/PNG at 25 MiB and 50 megapixels, or MP4 at
250 MiB and 15 minutes. Local gallery access uses the existing developer ADC and Cloud
SQL IAM permissions; there is no remote probe service.

The retained `track-000001` fixture was tested through both `--crop-dir` and a temporary
MP4 using the real CPU inference stack and live read-only gallery connection. Both
ranked its enrolled subject first (similarities `0.9776067747` and `0.9114904947`).

## Tests

Run focused unit checks in the existing development environment:

```bash
.test-venv/bin/python -m pytest tests/unit -q
.test-venv/bin/python -m ruff check worker tests scripts maintenance
node --check ui/app.js
node --check ui/subjects.js
```

Use the containerized `validate` command in [maintenance procedures](maintenance/README.md)
for the PostgreSQL and browser suite. Its fixtures require a disposable database.
`scripts/test_db_integration.sh` delegates to this workflow and requires the four
`FACE_MAINTENANCE_*` settings named in that script. Skipped integration/browser
checks do not establish acceptance. The release's completed validation is recorded
in the cutover review.

## Immutable archive uploads

If another trusted ingestion system already wrote the object with the same CSEK, skip
to the metadata requirements below. Otherwise, the following example uploads a local
file without printing or passing the CSEK value in an argument or environment variable.
It refuses to overwrite an existing object by requiring generation zero.

Set only the non-secret source path and destination object name:

```bash
cd /workspaces/ThunderCloud/data-batch-gcp-scaffold
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

Continue with [archive submission](#submit-archive-objects-through-the-common-run-framework)
using the recorded provenance. Receipt retries, batch limits, and run monitoring are
documented there.

## Submit archive objects through the common run framework

These commands submit to the deployed common framework.

After the acquisition tooling uploads an object, use its exact manifest generation,
digest, size, and content type to prepare a private durable receipt:

```bash
data-submit prepare \
  --receipt private-receipts/source.json \
  --principal YOUR_DEVELOPER_ACCOUNT \
  --bucket teak-banner-dome-bulk-videos \
  --object-name videos/OBJECT_NAME.mp4 \
  --generation GENERATION \
  --sha256 SHA256 \
  --bytes BYTE_COUNT \
  --content-type video/mp4 \
  --handling-policy enroll_only \
  --selection-policy all_tracks

data-submit submit --receipt private-receipts/source.json
```

Set `--page-url` when external attribution is available. Choose handling explicitly:
`search_then_discard`, `enroll_only`, or `retain_and_enroll`. Choose `manual` for
interactive selection or `all_tracks` for unattended processing; unattended enrollment
creates one new subject per detected track for new content. Exact-content repeats
reuse existing enrollment; see [duplicate handling](#exact-content-enrollment-reuse-and-alternate-urls).
Original archive objects are preserved under every policy.

If submission loses its acknowledgement, submit the same receipt again. Its stable
request key resolves to the same run. Do not recreate the receipt. For a selected
bulk submission, repeat `--receipt`; the default active-run limit is three. Track the
returned run IDs through **Check a run**. CPU acquisition and GPU processing use the
shared `face-ingest-drain` and `face-interactive-gpu` jobs and durable run operations.

## Managed console

The system includes the IAP-protected `face-console` service, the single-task
`face-ingest-drain` CPU job, and the `face-interactive-gpu` interactive detection and matching job.
The browser accepts JustPaste, Luluvid, guarded public HTTPS media, and direct JPEG,
PNG, or MP4 uploads. Enabled policies are `search_then_discard`, `retain_and_enroll`,
and `enroll_only`. Recent runs, run recovery, cancellation, retries, face selection,
and subject-gallery views are available through the IAP-protected console.

Retention and security design is in [Architecture](ARCHITECTURE.md).
[Cutover evidence](docs/platform-cutover-review.md) and dated records under
`docs/history/` describe completed releases.

Apply the additive schema and grants before enabling the service:

```bash
python scripts/apply_db_migrations.py \
  --app-user FACE_CONSOLE_IAM_DATABASE_USER \
  --app-user FACE_GPU_WORKER_IAM_DATABASE_USER
```

Build a CPU console/ingestion image separately from the GPU image, push both to the
existing Artifact Registry repository, and put immutable digests in Terraform:

```bash
docker build -f Dockerfile.console -t CONSOLE_IMAGE_TAG .
docker build -t GPU_IMAGE_TAG .
```

Set `enable_phase1_console`, `console_image`, `console_origin`, and
`approved_iap_member` only after reviewing the plan. Pin `interactive_gpu_image` to the released GPU image. `cloud_run_worker_image` is
only a configuration fallback; there is no permanent archive queue worker. Use an explicit `group:` IAM principal for team
launch; an explicit `user:` principal may be used for a temporary single-account
acceptance deployment. Guarded arbitrary public HTTPS fetching is enabled in the current Terraform
configuration. Each redirect and resolved address must pass the transport safeguards. Terraform enables direct
Cloud Run IAP and grants access only to the configured principal.

## Retained enrollment

Both enrollment policies publish representative gallery images, with up to five
active images per subject. Apply the complete ordered migrations and shared grants;
never initialize a current deployment from one historical migration alone.

For new content, `enroll_only` creates subjects and removes temporary media.
`retain_and_enroll` also records pre-enrollment candidates and retains the source
under CSEK-encrypted `training-media/`. Exact-content repeats reuse existing enrollment;
see [duplicate handling](#exact-content-enrollment-reuse-and-alternate-urls).
Similarity never assigns enrollment to an existing subject. Managed sources may later be tombstoned through
`DELETE /api/sources/{source_id}` without removing derived lineage. Historical gallery
repair is a separate deliberate maintenance operation.

Operators can repair clustering without direct SQL:

```bash
data-gallery-correct merge KEEP_SUBJECT_UUID MERGE_SUBJECT_UUID
data-gallery-correct split-group ENROLLED_GROUP_UUID
```

## Acquisition handoff

The sibling `bulk-download` tooling resolves, downloads, and uploads archive objects.
Prepare and submit receipts using the common run commands above. Keep the upload
manifest and receipt until the handoff is confirmed. The receipt contains a null run ID before submission and the acknowledged run ID afterward.

## Maintenance and recovery

Keep the ingestion reconciliation and daily maintenance schedulers enabled. They
recover durable work, delete terminal temporary media, and retire old gallery
generations. Bounded gallery repair code remains available for deliberate maintenance;
historical repeat-mode execution is not part of normal operations. Preserve the
append-only acquisition manifest, immutable image pins, Cloud SQL backups, Terraform
state, CSEK material, and archive/gallery objects.

Dated release evidence remains in `docs/history/`; removed historical selections
remain in Git history and must not be replayed as new work. Use
[immutable archive uploads](#immutable-archive-uploads) and
[archive submission](#submit-archive-objects-through-the-common-run-framework) above.

## Delivery evidence

- [2026-09-10 cutover result](docs/history/2026-09-10-platform-cutover.md).

- [Production cutover and recovery](docs/platform-cutover-review.md).
- [Maintenance commands and scoped cleanup](maintenance/README.md).
- [Potential matches validation](docs/history/2026-09-09-potential-matches.md).
- [Desktop/mobile UI validation](docs/history/2026-09-09-platform-ui.md).
- [Retired runtime and matching gates](docs/history/2026-09-09-runtime-consolidation.md).

The completed 2026-09-09 source separation preserved all 43,670 examples and 8,682
active gallery images. Its protected recovery records remain under `.local/source-split`.
Temporary cutover resources and retained cutover recovery storage were removed
on 2026-09-10 at the user’s request, as described in the cutover review.

## Within-source matching

See [operator workflow, CLI, bounds, and staged rollout](docs/source-merges.md).
Operator application is now enabled after the reviewed source/threshold rollout
recorded in the guide. New deployments default to proposals only; enable
`source_merges_apply_enabled` after the small-source review and explicit threshold calibration. The CLI uses IAP and the existing merge/recovery semantics.

## Submit a Hotscope video page

The URL submission path accepts `https://hotscope.tv/video/ID` (also `www`).
Paste an individual video page into the existing link form, or submit it as a
normal `source: {kind: "url", url: "https://hotscope.tv/video/ID"}`. User/profile
URLs belong to the separate bulk downloader and are rejected by this single-source
workflow.

The ingestion worker resolves the requested video's full HLS playlist from page
data, downloads the highest advertised bandwidth rendition, and remuxes it to
MP4 without re-encoding. It validates decoding and duration before uploading to
the existing encrypted temporary storage. The original page attribution and the
selected run policy follow the normal platform workflow; past bulk downloads are
not submitted automatically by this change.

Provider page requests and every playlist/segment redirect retain connection-time
DNS pinning. Hotscope acquisition is restricted to the known page/CDN hosts.
Previews and related videos are not accepted as substitutes. The fetcher sends the
generic `BulkDownloader/1.0` user agent.

Limits: the configured ingestion byte cap applies to both acquired segments and
the resulting MP4; HLS additionally requires a finite playlist of at most 10,000
segments and 15 minutes, with a 180-second acquisition/validation budget (individual
network operations may finish after the budget before it is checked again).
Encrypted HLS, separate audio renditions, fMP4 maps, byte ranges, gaps, and
discontinuities currently produce source rejection. The existing run retry path
refreshes playback descriptors after transient acquisition failures. FFmpeg is
restricted to local file/pipe protocols; all network acquisition uses the secure
Python transport.

Rollout requires rebuilding and deploying the console image used by both the web
service and `face-ingest-drain`. `Dockerfile.console` now includes FFmpeg/ffprobe.
No database migration or extra cloud service is required. Code-level verification
includes the URL API, resolver, transport restrictions, generated HLS media,
ingestion advancement, and a live acquisition of the previously tested Hotscope
video; deployment and live processing are separate rollout steps.

## Container image retention

As of 2026-09-14, Artifact Registry deletes unprotected versions older than seven
days (previously 30). KEEP rules take precedence: the newest five versions per
image package and explicitly pinned deployed/rollback digests remain protected.
The latest deployed console digest was added to the live keep list, and the
previous ingestion digest remains a rollback pin in local Terraform inputs.

Cleanup runs asynchronously, usually taking effect within about a day. Docker
child manifests remain protected while referenced by a retained parent manifest;
removing a parent can require another cleanup cycle before its children disappear.
Shared layers mean manifest sizes cannot be summed to predict reclaimed storage.
Check repository-size metrics after cleanup before reporting realized savings.

On every deployment, update the live keep policy as well as the Terraform image
pin. Updating a Cloud Run image alone does not update Artifact Registry retention.

### Submit a specific link from a text list

The New search page accepts individual HTTPS media or supported video-page URLs.
Expand **Choose a link from a text file**, select a `.txt` export with one HTTPS
URL per line, and choose the desired video from the list. The chosen URL fills
the existing link form; choose the handling policy and press **Start link search**.
Importing a list does not submit runs. Blank lines and `#` comments are ignored,
duplicates are removed, and invalid lines are reported before any list is loaded.
Lists are limited to 10,000 unique URLs and 2 MiB. The file is read in the browser;
only the explicitly submitted URL is sent to the platform. Source validation and
the existing per-user run limits still apply.

For download-only acquisition into encrypted GCS storage, run from `bulk-download`:

```bash
.venv/bin/bulk-download run --videos-file input/hotscope_videos.txt
```

This CLI mode resolves specific videos without usernames and does not submit
platform processing runs. Add `--user` or `--users-file` only when filtering a
user's listing with the supplied video selection.

### Cloud acquisition batch

The `face-acquisition-batch` Cloud Run job runs explicit video acquisition and
archive handoff without a local workspace. Its pinned image includes the bulk
CLI and `worker.acquisition_batch`. The job has one CPU, 2 GiB RAM, one task,
three task retries, and a seven-day timeout. It exits once all acquired sources
have been handed off; the ordinary processor finishes accepted runs independently.
Acquisition failures are retained in the checkpoint for review rather than
silently retried forever. New transfers respect the processor's 250 MiB media cap.

The checkpoint for the September 14 batch is
`gs://teak-banner-dome-bulk-videos/batch-control/hotscope-20260914/state.json`.
It contains the selected URLs, acquisition manifest, stable submission receipts,
explicit handling policy, operator principal, and status counters. It is encrypted
with the same CSEK as the archive. The key comes from Secret Manager at runtime;
neither the key nor the selected URLs are included in the image. Generation
preconditions protect checkpoint writes. Do not start overlapping executions or
restart the local writers after cloud cutover. Cloud task retries load the same
checkpoint and reuse the original request keys; immutable archive objects are
recovered if an upload finished before its checkpoint was saved.

For monitoring, use the Cloud Run execution and Cloud Logging. Decrypt the exact
checkpoint with the existing CSEK to inspect `status` or download updated manifests
and receipts. Local `data/manifest.jsonl` and local dispatch status stop updating
at cutover and must not be treated as the current cloud progress. On retries, use
the existing state object rather than creating a new batch or new receipts.

The cloud runner keeps the original operator principal for console visibility,
uses `all_tracks` selection, and observes the existing three-active-run quota.
It skips objects already present in the processor by bucket/object/generation.
The runtime gains archive **create** permission and access to this batch's
checkpoint prefix; it gains no new archive overwrite or delete permission.
Terraform in `acquisition_batch.tf` manages the job and scoped permissions, and
the registry cleanup policy protects the configured batch image.

### Browse controls and privacy view

Subjects support ID order, most sources, most examples, and newest first, plus
single/multiple-source and named/unnamed filters. Sources support ID order, most
subjects, most examples, and newest first, plus origin website filtering. Newest
means the platform record's creation time. Website filters accept a hostname or
URL, normalize case and the `www.` prefix, and match the exact normalized host.
Existing multiple-subject and shared-source filters remain available.

Sort/filter choices persist in the page URL. Changing them resets pagination;
count/date sorts use a numeric value plus UUID tie-breaker in their cursor.
The original ID cursor and older subject source-count cursors remain accepted.

The eye icon left of the theme control toggles privacy view. Gallery images turn
solid grey, including images loaded after the toggle. The preference is local to
the browser and is applied from a small head script before galleries render.

### Exact-content enrollment reuse and alternate URLs

Both enrollment policies now check verified SHA-256 content under the enrollment
transaction lock. If any examples already exist for the content, a new run reuses
those subjects and creates no subjects, examples, or gallery images. This applies
across policies and URLs, including concurrent submissions. Retain and enroll
promotes media only when no active managed copy exists; it can add retention to a
source previously enrolled without retaining the full media. The first enrollment's
selection remains authoritative: submitting the same video with a different selection
does not add tracks. Re-encoded content with different bytes is a separate source.
Existing duplicate subjects are not automatically merged by this change.

Migration 025 adds URL provenance by content hash and backfills source URLs and
completed enrollment-run URLs. Sources and subjects expose all known links, and
source URL/hostname searches include aliases. Duplicate run results explicitly show
existing enrollment and follow current example membership after merges. Cloud bulk
acquisition records every verified acquired URL even when an archived object is
already handed off. Apply the migration and updated db_grants.sql before updating
the console, GPU worker, ingest job, and acquisition job images.

After matching-group application, source manual selection is cleared and reloaded
from the server; source-list counts also refresh. Saved group outcomes remain
available for review and uncertain-operation retries.

### Potential-match query planning

Potential-match searches load the origin's version, model and vector once in the
same repeatable-read transaction, then rank candidates by cosine distance against
those constant values. This permits the existing subject_embedding_hnsw index;
a subject-to-subject join with descending `1 - distance` forced a full-library scan.
Normal suggestions use pgvector's approximate HNSW search with ef_search=40 and
strict ordered iterative scanning past filtered, merged, or dismissed candidates.
UUIDs break equal-distance ties. The dismissed view keeps exact filtering/ranking
so previously dismissed subjects remain visible even outside the nearest neighbors.

On subject efab0ba9-c4cc-4566-aaca-f657413f89d2, the previous ranking query exceeded
25 seconds and the UI hit its 30-second request timeout. The updated complete request
returned ten candidates, galleries and URLs in 6.72 seconds through a public IAM SQL
connection during validation. No database resizing or timeout increase was required.

### Subject exclusions and disabled examples

Migration 026 adds reversible subject matching exclusions and per-example disabling
(Not a subject / Unusable data). Excluded subjects are omitted from new matching,
potential matches, source merge proposals, and default subject browsing. Use Include
excluded in the subject library or source/manual subject picker to find them; direct
subject URLs remain accessible. Restore matching clears only the explicit subject
flag. A subject with zero active examples remains ineligible until an example is
restored. No model training or media deletion occurs.

On subject pages, individual examples offer Not a subject / Unusable data / Restore
example. Selected examples can be disabled/restored together (maximum 1,000). The
whole example/track embedding is disabled, not merely its displayed crop. Recalculation
uses only active examples; disabled examples retain their original embedding,
provenance, preview, reason, actor and timestamp. Past-run scores and enrollment
evidence remain unchanged; current candidate galleries omit disabled examples.

The operations use subject-version checks, an audit record and idempotent operation
IDs under the same transaction lock as enrollment and merges. Any excluded merge
member makes the resulting subject excluded, regardless of the survivor. Moves and
splits conservatively inherit exclusion; disabled flags travel with examples. Explicit
restoration is needed to reactivate them. Duplicate-content enrollment does not
reactivate disabled examples or excluded subjects. Maintenance invariants and
preservation receipts now account for active examples and capture exclusion flags.

Apply migration 026 before deploying the console and worker images. Existing values
default to active/not excluded. Roll out processing workers before the new UI controls.

# Operations

The documented application uses schema 027. Verify the live migration ledger and
deployed configuration before changing resources or processing work. See
[architecture](ARCHITECTURE.md) and [follow-up work](ROADMAP.md). Dated deployment
evidence is indexed in [history](docs/history/README.md).

## Current topology

- Project `teak-banner-dome`, regional services in `us-central1`.
- Archive `gs://teak-banner-dome-bulk-videos/videos/`; original objects are immutable.
- IAP console `face-console`; CPU acquisition/cleanup `face-ingest-drain`; CUDA processing `face-interactive-gpu`.
- Cloud SQL PostgreSQL 17/pgvector, database `face_index`, schema 027, IAM authentication and connector TLS.
- Temporary submissions `submissions-temporary/`, permanent galleries `subject-gallery/`, retained sources `training-media/`.

Cloud jobs use private Cloud SQL connectivity. Developer tools use the existing
public connector path with no authorized IP networks. CSEK material is read from
Secret Manager into memory. CPU acquisition has read access only to the archive
prefix; cleanup deletes only eligible exact generations in the managed prefixes.

## Prerequisites

1. Python 3.12 and FFmpeg.
2. Google Application Default Credentials for the developer account.
3. Terraform 1.6+, `gcloud`, Docker with Buildx, and GNU Make for cloud releases.
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

## Deploy the current systems

Use the existing Terraform state and `terraform.tfvars`; never copy the example over
a configured deployment. Run these commands from the repository root. Choose a new
`RELEASE` tag for each release, then authenticate Docker to the registry:

```bash
cd /workspaces/ThunderCloud
export RELEASE=YOUR_UNIQUE_RELEASE_TAG
export IMAGE_PREFIX="$(terraform -chdir=data-batch-gcp-scaffold/terraform output -raw artifact_registry_image_prefix)"
export REGION="${IMAGE_PREFIX%%-docker.pkg.dev/*}"
gcloud auth configure-docker "${REGION}-docker.pkg.dev"
```

The root [Makefile](../Makefile) only builds and pushes images. It uses the scaffold
context for the console and GPU images and the repository-root context for the
acquisition image. The acquisition Dockerfile has its own allowlisted context so
local data and credentials are not sent to the builder. Docker must be able to
push to this Artifact Registry repository. Record the digest printed by each push;
the commands below also read it back from the registry.

### Console and CPU ingest job

The same `console_image` pin deploys the `face-console` Cloud Run service and the
`face-ingest-drain` Cloud Run job. Publish the CPU image and read its immutable
digest:

```bash
make publish-console RELEASE="$RELEASE" IMAGE_PREFIX="$IMAGE_PREFIX"
CONSOLE_DIGEST="$(docker buildx imagetools inspect "$IMAGE_PREFIX/console:$RELEASE" --format '{{.Digest}}')"
printf 'console_image = "%s/console@%s"\n' "$IMAGE_PREFIX" "$CONSOLE_DIGEST"
```

Set `console_image` in `data-batch-gcp-scaffold/terraform/terraform.tfvars` to the
printed value. For a new installation, also configure `enable_phase1_console`,
`console_origin`, and `approved_iap_member` as described below. The image update
affects both the service and the ingest job.

### Interactive GPU job

The `face-interactive-gpu` job uses `interactive_gpu_image` when set, otherwise
`cloud_run_worker_image`. Build the CUDA image only after the licensed model files
and `models/SHA256SUMS` are present; its Dockerfile verifies them during the build.

```bash
make publish-gpu RELEASE="$RELEASE" IMAGE_PREFIX="$IMAGE_PREFIX"
GPU_DIGEST="$(docker buildx imagetools inspect "$IMAGE_PREFIX/worker:$RELEASE" --format '{{.Digest}}')"
printf 'interactive_gpu_image = "%s/worker@%s"\n' "$IMAGE_PREFIX" "$GPU_DIGEST"
```

Set `interactive_gpu_image` in `terraform.tfvars` to the printed value. Keep the
required `cloud_run_worker_image` pin valid; it is the fallback if the interactive
pin is null. The local [bulk downloader](../bulk-download/README.md) is a separate
CLI and has no Cloud Run service to deploy.

### Optional cloud acquisition batch

`face-acquisition-batch` exists only when `acquisition_batch_image` is set. Its
image extends the console image, so use the *currently intended* console digest
from `terraform.tfvars` as `CONSOLE_IMAGE`. If releasing both, publish the console
first and use its new digest. Do not use the Dockerfile's historical default base.

```bash
export CONSOLE_IMAGE="$IMAGE_PREFIX/console@REPLACE_WITH_CONSOLE_DIGEST"
make publish-acquisition RELEASE="$RELEASE" IMAGE_PREFIX="$IMAGE_PREFIX" CONSOLE_IMAGE="$CONSOLE_IMAGE"
ACQUISITION_DIGEST="$(docker buildx imagetools inspect "$IMAGE_PREFIX/acquisition-batch:$RELEASE" --format '{{.Digest}}')"
printf 'acquisition_batch_image = "%s/acquisition-batch@%s"\n' "$IMAGE_PREFIX" "$ACQUISITION_DIGEST"
```

Set `acquisition_batch_image` in `terraform.tfvars` to the printed value. Review
`acquisition_batch_state_object` for the selected batch before any execution.
Provisioning or updating the job does not start a batch; start one only with the
selection and checkpoint procedure under [Cloud acquisition batch](#cloud-acquisition-batch).

### Database and Terraform rollout

If the release changes the database schema, apply the ordered migrations and
grants from [Bootstrap Cloud SQL](#bootstrap-cloud-sql) before deploying its
images. Then review one Terraform plan covering the intended image pins and any
other configuration changes:

```bash
cd /workspaces/ThunderCloud/data-batch-gcp-scaffold/terraform
terraform init
terraform plan -out face-batch.tfplan
terraform show face-batch.tfplan
terraform apply face-batch.tfplan
```

Apply only after reviewing all proposed changes. Terraform also updates the
Artifact Registry KEEP policy for the configured image digests; add any rollback
digests to `rollback_image_versions` before an older version loses protection.
Check the live service and jobs after the rollout, where enabled:

```bash
gcloud run services describe face-console --project teak-banner-dome --region us-central1
gcloud run jobs describe face-ingest-drain --project teak-banner-dome --region us-central1
gcloud run jobs describe face-interactive-gpu --project teak-banner-dome --region us-central1
# Only if acquisition_batch_image is configured:
gcloud run jobs describe face-acquisition-batch --project teak-banner-dome --region us-central1
```

Compare the reported image digests with `terraform.tfvars`. Check the live
database migration ledger and run the relevant checks in [Tests](#tests) before
submitting new work. One-off maintenance and historical backfill jobs have their
own procedures; they are outside this release path.

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

## Tests

Run focused unit checks in the existing development environment:

```bash
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python -m ruff check worker tests scripts maintenance
node --check ui/app.js
node --check ui/subjects.js
```

Use the containerized `validate` command in [maintenance procedures](maintenance/README.md)
for the PostgreSQL and browser suite. Its fixtures require a disposable database.
`scripts/test_db_integration.sh` delegates to this workflow and requires the four
`FACE_MAINTENANCE_*` settings named in that script. Skipped integration/browser
checks do not establish acceptance. Dated release validation is indexed in
[history](docs/history/README.md).

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
interactive search-only selection or `all_tracks` for unattended search processing.
Enrollment policies automatically include every detected track, then combine eligible
subjects within each new source at the configured cosine threshold. Exact-content repeats
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
New retained/enroll-only submissions automatically select all detected groups;
search-only submissions retain interactive selection. New-source automatic grouping
uses the reviewed cosine threshold 0.4.

Luluvid page submissions also accept `luluvdo.com` and `luluvdoo.com` (including
`www`), resolving them through the canonical `luluvid.com` page. JustPaste links
recognize these aliases too. Static MP4 and finite MPEG-TS HLS playback are supported;
HLS acquisition keeps DNS pinning, same-media-host redirects/segments, byte and
duration limits, and complete decode validation.

Retention and security design is in [Architecture](ARCHITECTURE.md). Dated release
records are indexed in [history](docs/history/README.md).

Apply the additive schema and grants before enabling the service:

```bash
python scripts/apply_db_migrations.py \
  --app-user FACE_CONSOLE_IAM_DATABASE_USER \
  --app-user FACE_GPU_WORKER_IAM_DATABASE_USER
```

Build the CPU and GPU images separately and pin their immutable digests as shown
under [Deploy the current systems](#deploy-the-current-systems).

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

## Within-source matching

See [operator workflow, CLI, bounds, and configuration](docs/source-merges.md).
The CLI uses IAP and the existing merge/recovery semantics. Review the live value
of `source_merges_apply_enabled` before planning operator work. New-source
enrollment automatically groups and merges at the documented cosine 0.4, keeping every
example. This is independently configured by `source_merges_auto_threshold` on the
interactive job; null disables automatic matching. Existing sources and duplicate
enrollment reuse are unchanged. See the guide for bounds and manual-review exceptions.

The schema 027 import queue persists downloaded work until bounded processing
capacity is available. The documented configuration uses two batch GPU slots, one
interactive reservation, and two concurrent acquisition profiles. The GPU worker
uses two database connections so gallery cleanup receipts can commit independently
of enrollment. Keep queue and checkpoint records when changing consumers or
rolling back images. See the
[performance release record](docs/history/2026-09-19-performance-implementation.md)
for rollout evidence, throughput measurements, and capacity decisions.

## Submit a Hotscope video page

The URL submission path accepts `https://hotscope.tv/video/ID` (also `www`).
Paste an individual video page into the existing link form, or submit it as a
normal `source: {kind: "url", url: "https://hotscope.tv/video/ID"}`. User/profile
URLs use the console's separate batch flow described below; they are rejected by
the single-source API.

The ingestion worker resolves the requested video's full HLS playlist from page
data, downloads the highest advertised bandwidth rendition, and remuxes it to
MP4 without re-encoding. It validates decoding and duration before uploading to
the existing encrypted temporary storage. The original page attribution and the
selected run policy follow the normal platform workflow.

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

## Container image retention

The documented Artifact Registry policy deletes unprotected versions older than
seven days. KEEP rules take precedence: the newest five versions per image package
and explicitly pinned deployed/rollback digests remain protected. Check the live
policy and pinned digests before relying on this retention behavior.

Cleanup runs asynchronously, usually taking effect within about a day. Docker
child manifests remain protected while referenced by a retained parent manifest;
removing a parent can require another cleanup cycle before its children disappear.
Shared layers mean manifest sizes cannot be summed to predict reclaimed storage.
Check repository-size metrics after cleanup before reporting realized savings.

On every deployment, update the live keep policy as well as the Terraform image
pin. Updating a Cloud Run image alone does not update Artifact Registry retention.

## Download-only acquisition

For media that should be archived without submitting platform processing, use the
separate [bulk downloader](../bulk-download/README.md). Preserve its manifest and
checkpoint when resuming an existing acquisition.

## Cloud acquisition batch

The `face-acquisition-batch` Cloud Run job runs explicit video acquisition and
archive handoff without a local workspace. Its pinned image includes the bulk
CLI and `worker.acquisition_batch`. Check Terraform and the live job for current
resources, retries, and timeout. It exits once all acquired sources
have been handed off; the ordinary processor finishes accepted runs independently.
Acquisition failures are retained in the checkpoint for review rather than
silently retried forever. New transfers respect the processor's 250 MiB media cap.

Each batch checkpoint contains selected URLs, an acquisition manifest, stable submission receipts,
explicit handling policy, operator principal, and status counters. It is encrypted
with the same CSEK as the archive. The key comes from Secret Manager at runtime;
neither the key nor the selected URLs are included in the image. Generation
preconditions protect checkpoint writes. Do not start overlapping executions or
competing local writers for the same batch. Cloud task retries load the same
checkpoint and reuse the original request keys; immutable archive objects are
recovered if an upload finished before its checkpoint was saved.

For monitoring, use the Cloud Run execution and Cloud Logging. Decrypt the exact
checkpoint with the existing CSEK to inspect `status` or download updated manifests
and receipts. A local manifest does not report later cloud progress. On retries, use
the existing state object rather than creating a new batch or new receipts.

The cloud runner keeps the original operator principal for console visibility,
uses `all_tracks` selection, and hands off through the bounded import queue.
It skips objects already present in the processor by bucket/object/generation.
The runtime gains archive **create** permission and access to this batch's
checkpoint prefix; it gains no new archive overwrite or delete permission.
Terraform in `acquisition_batch.tf` manages the job and scoped permissions, and
the registry cleanup policy protects the configured batch image.

## Browse controls and privacy view

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

## Exact-content enrollment reuse and alternate URLs

Both enrollment policies now check verified SHA-256 content under the enrollment
transaction lock. If any examples already exist for the content, a new run reuses
those subjects and creates no subjects, examples, or gallery images. This applies
across policies and URLs, including concurrent submissions. Retain and enroll
promotes media only when no active managed copy exists; it can add retention to a
source previously enrolled without retaining the full media. The first enrollment's
selection remains authoritative: submitting the same video with a different selection
does not add tracks. Re-encoded content with different bytes is a separate source.
Existing duplicate subjects are not automatically merged by this change.

Sources and subjects expose all known links, and
source URL/hostname searches include aliases. Duplicate run results explicitly show
existing enrollment and follow current example membership after merges. Cloud bulk
acquisition records every verified acquired URL even when an archived object is
already handed off.

After matching-group application, source manual selection is cleared and reloaded
from the server; source-list counts also refresh. Saved group outcomes remain
available for review and uncertain-operation retries.

## Subject exclusions and disabled examples

Subject matching exclusions and per-example disabling are reversible. Excluded
subjects are omitted from new matching, potential matches, source merge proposals,
and default subject browsing. Use **Show excluded** in the subject library to find them; direct
subject URLs remain accessible. Restore matching clears only the explicit subject
flag. A subject with zero active examples remains ineligible until an example is
restored. No model training or media deletion occurs.

On subject pages, individual examples offer **Disable example** and **Restore
example**. Selected examples can be disabled/restored together (maximum 1,000). The
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

Existing values default to active/not excluded.

## Submit all videos from a Hotscope user in the console

The link form accepts `https://hotscope.tv/user/Mary` or `/user/Mary`. Submitting a
user queues every discoverable public video in the background, using the selected
handling policy and automatic selection of all detected groups. The batch progress
page can be bookmarked and reopened; it lists current run states and any acquisition
failures. Closing the browser does not stop processing. Durable import handoff,
bounded queue/admission, enrollment deduplication, and automatic source matching apply.

Profile checkpoints are encrypted under the existing batch checkpoint directory,
with `profiles/<batch-id>.json` filenames. `FACE_PROFILE_BATCH_PREFIX` enables the
console integration; its prefix must match the acquisition checkpoint IAM condition.
The console launches `face-acquisition-batch` with the checkpoint object override.
Repeated requests use a durable idempotency key; concurrent executions are fenced by
checkpoint generation and execution ownership. Discovery is capped at 1,000 pages;
an incomplete profile is reported as failed rather than silently accepting a partial list.

Text-file link import is local-only and is no longer offered in the console. Run
results expose the submitted source independently of candidates. “View Source” opens
the library source when available, otherwise the original HTTPS page; runs without
candidates or detections still show it. An upload without source attribution has no
external source link to show.

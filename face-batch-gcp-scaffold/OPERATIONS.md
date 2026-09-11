# Operations

The consolidated framework is deployed on schema 024. See [architecture](ARCHITECTURE.md),
[current contracts](docs/platform-contracts.md), and [cutover results](docs/platform-cutover-review.md).
Live acceptance and browser review status are recorded in the cutover results.

## Current topology

- Project `teak-banner-dome`, regional services in `us-central1`.
- Archive `gs://teak-banner-dome-bulk-videos/videos/`; original objects are immutable.
- IAP console `face-console`; CPU acquisition/cleanup `face-ingest-drain`; CUDA processing `face-interactive-gpu`.
- Cloud SQL PostgreSQL 17/pgvector, database `face_index`, schema 024, IAM authentication and connector TLS.
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

`scripts/run_local.sh` delegates to `face-submit`; it accepts the same `prepare`
and `submit` arguments shown below. The optional container wrapper uses the same
receipt CLI and developer ADC. Keep receipts in `private-receipts/` (or the explicit
`FACE_RECEIPT_DIR`) so the wrapper can write them durably. It submits work to the
managed jobs; it does not run a separate archive enrollment writer.

## Ephemeral local probes

The `face-probe` command reads a local JPEG, PNG, or MP4, runs the shared inference
pipeline, and performs a read-only search of model-compatible gallery subjects. Probe
media, embeddings, and results are never uploaded or written to Cloud SQL. Results are
ranked candidates rather than automatic identity decisions; each includes the nullable
operator-managed display name and all current succeeded training-track provenance.

The command creates a private local review directory containing `result.json` and the
exact detected face or retained track crops used for comparison:

```bash
face-probe submit --file ./person.jpg --top-k 10
face-probe submit --file ./short-clip.mp4 --top-k 10
face-probe submit --file ./person.jpg --top-k 10 --review-output-dir ./probe-review
face-probe submit --crop-dir ./existing-track-crops --top-k 10
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

## Submit archive objects through the common run framework

These commands submit to the deployed common framework.

After the acquisition tooling uploads an object, use its exact manifest generation,
digest, size, and content type to prepare a private durable receipt:

```bash
face-submit prepare \
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

face-submit submit --receipt private-receipts/source.json
```

Set `--page-url` when external attribution is available. Choose handling explicitly:
`search_then_discard`, `enroll_only`, or `retain_and_enroll`. Choose `manual` for
interactive selection or `all_tracks` for unattended processing; unattended enrollment
creates one new subject per detected track. Original archive objects are preserved
under every policy.

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

Current retention and security behavior is described in `ARCHITECTURE.md`. The initial
Phase 1 deployment evidence is archived under `docs/history/phase1-review.md`.

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

`enroll_only` creates new subjects and removes temporary media. `retain_and_enroll`
records pre-enrollment candidates, creates new subjects, and retains the exact source
under CSEK-encrypted `training-media/`. Similarity never assigns enrollment to an
existing subject. Managed sources may later be tombstoned through
`DELETE /api/sources/{source_id}` without removing derived lineage. Historical gallery
repair is a separate deliberate maintenance operation.

Operators can repair clustering without direct SQL:

```bash
face-gallery-correct merge KEEP_SUBJECT_UUID MERGE_SUBJECT_UUID
face-gallery-correct split-group ENROLLED_GROUP_UUID
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

Historical commands and selections are archived in `docs/history/`. Never replay the
completed corpus selection as a new rollout. `FUTURE_VIDEOS.md` contains the detailed
immutable upload and explicit enqueue procedure.

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

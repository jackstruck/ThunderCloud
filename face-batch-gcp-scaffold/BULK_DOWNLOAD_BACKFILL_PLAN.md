# Bulk-download backfill and local-first handoff plan

## Goal

Bring the historical videos produced by `../bulk-download` up to the current
face-batch data contract without re-downloading or re-running successful inference,
and let the long-running work finish unattended in Cloud Run. After the historical
backfill is accepted, reduce `bulk-download` to a local JustPaste/Luluvid acquisition
tool that hands newly uploaded videos directly to face-batch, then remove infrastructure
that exists only for the historical rollout.

The durable archive remains `gs://teak-banner-dome-bulk-videos/videos/`. The existing
UUIDv5 object naming and CSEK encryption remain unchanged.

## Scope and boundaries

- Reuse the existing `face-interactive-gpu` Cloud Run Job for gallery regeneration.
- Reuse the existing face-batch queue and `face-batch-gpu-drain` processing contract;
  do not create a second inference pipeline in `bulk-download`.
- Treat successful historical `face_track` rows as authoritative. Reprocess a video
  only when validation shows its committed output is absent or incompatible.
- Preserve the submitted Luluvid URL as `source_asset.metadata.page_url`. Do not expose
  JustPaste URLs or `source_adapter` in candidate attribution.
- Make every backfill step restartable and safe to run more than once.
- Do not tear down the shared console, database, archive bucket, KMS key, or current
  face-batch jobs. Teardown applies only to resources and files used solely by the old
  bulk rollout or temporary backfill orchestration.

## Stage 0 — inventory and freeze the contract

Before changing data, produce one machine-readable inventory that joins:

1. the append-only `bulk-download/data/manifest.jsonl` records;
2. the exact GCS object generation, size, content type, and encryption state;
3. `source_asset`, `processing_job`, `face_track`, `subject`, and
   `subject_representative_face` rows; and
4. active/dead-letter work from the historical rollout.

Classify every canonical video into exactly one state:

- `complete`: source provenance and compatible face results exist;
- `metadata_only`: inference is complete but current source fields need repair;
- `gallery_only`: subjects exist but have fewer than the target five active gallery
  representatives;
- `process`: no successful compatible processing result exists;
- `blocked`: manifest/GCS checksum, generation, encryption, or ownership conflicts;
- `duplicate`: canonical content points to an already processed source.

Write the inventory and its summary counts under `data/` with a timestamp. It is an
audit artifact, not a new source of truth. Stop before mutation if a manifest checksum
does not match GCS or if one content hash maps ambiguously to conflicting provenance.

Acceptance gate: every usable manifest record has one classification, totals reconcile
to the manifest and GCS inventory, and `blocked` items are reviewed explicitly.

## Stage 1 — implement the unattended historical backfill

### 1. Normalize source provenance

Add a bounded face-batch command that consumes the inventory and repairs only missing
historical fields. For each source it should upsert or verify:

- immutable GCS URI, generation, byte count, content type, and SHA-256;
- `metadata.page_url` from the submitted `luluvid_url`;
- worker, detector, embedding-model, and threshold versions from the committed job;
- terminal processing state and existing source/track relationships.

Never replace non-empty conflicting values automatically. Record those as blocked.
Use database transactions per source and emit structured counters so rerunning the
command converges without creating duplicate sources, jobs, tracks, or subjects.

This is a metadata reconciliation pass; it must not fetch Luluvid pages and must not
rewrite archive objects.

### 2. Queue only genuinely missing processing

Use the existing `face-ingest enqueue-manifest` / durable rollout mechanism for the
inventory's `process` set. Pin the current immutable worker digest and all model and
threshold versions. Start the existing `face-batch-gpu-drain` with one task and
parallelism one unless a separate concurrency validation approves more.

The launcher should return immediately after Cloud Run accepts execution. Progress and
retry state live in Cloud SQL, so loss of the operator terminal does not interrupt the
run. Reconcile the rollout before proceeding; require zero active, dead-letter,
missing-commit, provenance-mismatch, and lingering-staging items.

### 3. Generate representative gallery images

Run the existing `worker.interactive` backfill mode on `face-interactive-gpu`. Extend
its checkpoint/reporting only if the Stage 0 inventory shows it cannot cover all
historical subjects. Keep its current safety properties:

- select subjects with fewer than five active representatives;
- verify source generation and SHA-256 before inference;
- regenerate tracks with the current model and associate them unambiguously with the
  committed track rather than changing subject membership;
- upload CSEK-encrypted JPEGs under `subject-gallery/<subject-id>/` as inactive;
- atomically publish a complete representative set and queue retired generations for
  cleanup.

Run an initial bounded sample (25 subjects), review association/skipped/source-failure
metrics and gallery rendering, then execute repeatable bounded batches until the query
returns no candidates. A non-zero skipped set must be exported for review rather than
silently treated as complete.

Invocation must use execution-time overrides or a dedicated backfill job definition;
do not leave `FACE_INTERACTIVE_MODE=backfill` persisted on the normal interactive job.

### 4. Refresh derived attribution

The additive candidate-attribution migration already backfills existing
`run_candidate.page_urls`. After source metadata normalization, rerun only its
idempotent attribution update (or a small dedicated refresh command) so historical
candidate rows include every distinct Luluvid `page_url` now associated with the
subject. New ranking and gallery reads continue to derive all distinct URLs through
both `face_track` and retained-enrollment lineage.

### 5. Backfill acceptance

Generate a final report and require:

- every non-blocked archive video has verified provenance and a successful compatible
  processing result;
- every eligible subject has one to five active gallery representatives, with no
  dangling database rows or missing objects;
- all gallery objects report CSEK encryption;
- historical candidate and subject responses contain all distinct expected page URLs;
- the processing rollout reconciles cleanly and gallery backfill has no remaining
  candidates;
- a small manual UI sample shows faces, source links, and correct navigation.

Retain the before/after inventory, Cloud Run execution names, immutable image digests,
and summary metrics as the backfill record.

## Stage 2 — slim local-first acquisition after the backfill

Refactor `../bulk-download` around one responsibility:

```text
local JustPaste input -> Luluvid page -> media -> verified CSEK videos/ object
                                           -> direct face-batch enqueue handoff
```

Keep:

- exact-host URL validation, redirect/DNS protections, bounded retries and size limits;
- static Luluvid resolution and the existing supported HLS remux path;
- UUIDv5 naming from canonical Luluvid URL;
- streaming SHA-256/byte calculation, CSEK upload with generation preconditions, and
  append-only local receipts;
- content-hash deduplication and resumability.

Remove or archive from the active path:

- HeyLink discovery and HeyLink input;
- Cloud-hosted download orchestration and historical partition/front/back controls;
- recovery logic whose only purpose was reconstructing the initial manifest;
- duplicate face-processing logic or long-lived bulk rollout state.

### Direct handoff contract

After a successful upload, call a face-batch library/CLI entry point with a typed record
containing:

- canonical `page_url` (the submitted Luluvid URL);
- immutable GCS URI and generation;
- SHA-256, byte count, and content type;
- a stable idempotency key derived from the canonical URL plus object generation.

Face-batch, not bulk-download, owns creation of processing work, matching, subject
assignment, representative gallery publication, model/version fields, retries, and
reconciliation. The handoff should create durable queue state before the local command
reports success. If enqueue fails after upload, preserve a local `uploaded_not_enqueued`
receipt so the next run retries the handoff without downloading or overwriting again.

Prefer adding a first-class `face-ingest enqueue-source` command over synthesizing and
re-reading a manifest file. Keep manifest import available only as a compatibility and
disaster-recovery path during the transition.

### Local-first acceptance

Use a small set containing a new URL, an already-uploaded URL, and duplicate content.
Verify that:

- the new item uploads once and creates exactly one durable face-batch work item;
- a rerun performs neither a second upload nor duplicate processing;
- attribution stores and displays the submitted Luluvid URL;
- successful processing produces the same fields and gallery behavior as console
  retained enrollment;
- interruption between upload and enqueue is recoverable from the local receipt.

## Stage 3 — retire historical/backfill-only infrastructure

Only after both acceptance gates and a documented observation window:

1. export final rollout/backfill reports and identify resources by Terraform address;
2. remove any scheduler, job, service account binding, image, temporary staging object,
   or alert created solely for the initial bulk rollout/backfill;
3. preserve the shared `face-batch-gpu-drain`, `face-interactive-gpu`, console,
   database, Artifact Registry repository, archive/gallery prefixes, and keys used by
   ongoing face-batch operation;
4. delete obsolete local bulk selections, transient logs, locks, and recovery scripts
   only after copying required audit artifacts to the agreed durable location;
5. remove unused Terraform variables/outputs and confirm the destroy plan targets only
   the reviewed historical resources;
6. prune unreferenced historical container tags according to the repository retention
   policy while retaining deployed digests and the final backfill digest.

Final infrastructure acceptance is a Terraform plan with no unexpected drift and a
cost inventory containing no always-on or scheduled resource used only by the completed
backfill.

## Recommended implementation order

1. Build the read-only inventory/reconciliation command and review its output.
2. Add the idempotent metadata repair mode and test it on a handful of sources.
3. Enqueue and reconcile only missing video processing.
4. Run the 25-subject gallery sample, then drain the full gallery backfill unattended.
5. Refresh attribution and sign off the historical acceptance report.
6. Implement the local `JustPaste -> Luluvid -> upload -> enqueue-source` workflow.
7. Prove retry/idempotency behavior with the small acceptance set.
8. Apply a separately reviewed teardown plan for historical-only resources and files.


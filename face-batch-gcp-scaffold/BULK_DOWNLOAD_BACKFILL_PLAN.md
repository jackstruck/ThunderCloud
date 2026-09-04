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

### Historical cutoff and new URL queue

Freeze the historical backfill input at the existing completed manifest/GCS corpus.
Adding a URL to `../bulk-download/input/justpaste_urls.txt` does not put it into the
backfill: a URL is historical only if it had already resolved to a usable manifest
record and immutable GCS object at the cutoff.

At review time, the JustPaste input contains 98 entries without a completed resolution
checkpoint. This includes the three newly prepended entries:

- `https://justpaste.it/ah7w2`
- `https://justpaste.it/c3bec`
- `https://justpaste.it/6tg6b`

Treat all 98 unresolved entries as the initial post-backfill acquisition queue. Do not
resolve or upload them with the legacy historical scripts before the backfill. Preserve
their current file order, but deduplicate by canonical URL when the local-first command
loads them.

Use the three newly prepended entries as the first bounded acceptance batch for the new
local-first path. Process the remaining unresolved entries only after that batch proves
resolution, upload, durable enqueue, attribution, gallery generation, and idempotent
retry behavior.

## Stage 0 — inventory and freeze the contract

Before changing data, produce one machine-readable inventory that joins:

1. the append-only `bulk-download/data/manifest.jsonl` records;
2. the exact GCS object generation, size, content type, and encryption state;
3. `source_asset`, `processing_job`, `face_track`, `subject`, and
   `subject_representative_face` rows; and
4. active/dead-letter work from the historical rollout.

Record the cutoff timestamp and hashes of `manifest.jsonl` and the generated inventory.
Also report the unresolved JustPaste count separately; those entries must not be counted
as missing or blocked historical videos because they are outside the frozen corpus.

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

### Stage 0 completion record

Stage 0 completed successfully on 2026-09-03 using the CPU-only Cloud Run Job
`face-batch-backfill-inventory`. Execution
`face-batch-backfill-inventory-c4sxj` finished at 2026-09-03T14:32:33Z and passed the
acceptance gate. The full inventory was generated from cutoff timestamp
2026-09-03T14:09:10.687025Z with these results:

- 2,023 usable manifest records classified exactly once;
- 1,875 GCS archive objects, with no GCS-only objects;
- 1,869 `metadata_only`, 145 `duplicate`, and 9 `process` records;
- 0 `complete`, 0 `gallery_only`, and 0 `blocked` records;
- 0 manifest parse errors and reconciled manifest/GCS totals; and
- 98 unresolved JustPaste URLs reported separately, with the three acceptance URLs
  first in their preserved order.

The frozen manifest SHA-256 is
`1bfd9809344b9677e9fcbba2f3c19eb533b8bf9a90996e4fc876d591a77bc397`. The generated
inventory SHA-256 is
`0ffef0f4fcb803a7f87c2a3dca49ba4ee9314f2a0b230d4bd0da776d1b51b05d`.
The CSEK-encrypted inventory and summary are retained under
`gs://teak-banner-dome-bulk-videos/backfill-audit/results/`.

No blocked entries require review, and no entry sampling is required before beginning
Stage 1. Stage 1 must continue to use the frozen inventory as its bounded input.

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
it to use the historical track timestamps instead of performing full-video inference
by default. Historical `face_track` rows already provide `start_ms`, `end_ms`, aggregate
embedding, model version, and quality score. They do not contain the exact timestamp of
the original best-quality crop, so the worker must sample within each track interval.

For each source video, the optimized worker should:

1. collect the missing-gallery tracks selected for that source;
2. add a small configurable margin before `start_ms` and after `end_ms`, clamp the
   intervals to the video bounds, and merge overlapping windows;
3. seek to the nearest usable keyframe before each merged window and decode only until
   the end of that window;
4. run detection and embedding within the requested track intervals, selecting the
   highest-quality face whose embedding is compatible with the stored aggregate;
5. disambiguate co-occurring faces using embedding similarity and reject candidates
   below the existing association threshold or within the ambiguity margin; and
6. cache decoded windows so multiple subjects/tracks from the same source do not repeat
   the same work.

The source object may still be downloaded completely once to preserve the existing
generation and SHA-256 integrity check. Store it in a private bounded temporary file so
the decoder can seek; do not hold a large complete video in memory. Timestamp-window
decoding is intended to reduce CPU/GPU inference, not weaken source verification.

If targeted extraction finds no acceptable crop or produces an ambiguous match, record
the reason and optionally queue that source for a separate full-video fallback pass.
Do not silently invoke the expensive fallback inline. Run the fallback set only after
reviewing its count and estimated bytes/duration. Report targeted and fallback costs
separately.

Keep the backfill's current publication safety properties:

- select subjects with fewer than five active representatives;
- verify source generation and SHA-256 before inference;
- associate new crops unambiguously with the committed track rather than changing
  subject membership;
- upload CSEK-encrypted JPEGs under `subject-gallery/<subject-id>/` as inactive;
- atomically publish a complete representative set and queue retired generations for
  cleanup.

Run an initial bounded sample (25 subjects), review association/skipped/source-failure
metrics, decoded-window duration, avoided full-video duration, and gallery rendering.
Then execute repeatable bounded batches until the query returns no candidates. A
non-zero skipped set must be exported for review rather than silently treated as
complete.

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
- timestamp-window metrics and any separately approved full-video fallback executions
  are included in the final report;
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

Start with `ah7w2`, `c3bec`, and `6tg6b`, then exercise an already-uploaded URL and
duplicate content. Verify that:

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
2. Add and verify the idempotent metadata repair mode against the frozen inventory.
3. Enqueue and reconcile only missing video processing.
4. Run the 25-subject gallery sample, then drain the full gallery backfill unattended.
5. Refresh attribution and sign off the historical acceptance report.
6. Implement the local `JustPaste -> Luluvid -> upload -> enqueue-source` workflow.
7. Process `ah7w2`, `c3bec`, and `6tg6b` as the bounded acceptance batch and prove
   retry/idempotency behavior before releasing the other 95 unresolved entries.
8. Drain the remaining post-backfill acquisition queue through the accepted local-first
   workflow.
9. Apply a separately reviewed teardown plan for historical-only resources and files.

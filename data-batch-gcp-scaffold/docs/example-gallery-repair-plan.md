# Full-sweep example gallery repair

Status: implementation plan for review. Deploy as a Cloud Run Job and execute the full sweep in the background after implementation and automated verification.

## Outcome

Create a gallery image for every eligible example that currently lacks one and for which a reliable face crop can be recovered. Attach each image to the existing example so it appears on the example card and becomes available for the subject overview gallery.

The sweep preserves subject membership, stored embeddings, example enabled/disabled state, and matching behavior. The subject overview continues to show its existing small selection of images; individual examples receive their own previews.

## Starting inventory

The investigation snapshot found 34,988 missing previews among 75,939 examples, across 1,692 sources. Of these, 34,965 were historical face-track examples and 23 belonged to one older submission. Refresh these counts when the sweep starts.

The current repair path selects one example per subject/source and stops at five previews per subject. It also skips historical sources with a gallery fallback marker. The full-sweep path selects by missing example preview instead.

## 1. Select the complete workload

Add an explicit full-example-sweep mode to the gallery repair worker and repository.

- Select enabled examples with no active `subject_representative_face` record, belonging to current subjects.
- Include examples from sources with existing fallback markers and from subjects that already have gallery images.
- Use retained managed or archive sources, their pinned object generations, and recorded checksums.
- Verify that the loaded recognition model matches each example's stored model version. Count unsupported models and unavailable sources in the report.
- Capture a fixed sweep cutoff and an ordered source manifest. Include every eligible missing example present at that cutoff, grouped by source and generation.
- Record baseline counts for missing previews, eligible examples, sources, and subjects with no usable gallery image.

A fixed workload gives the sweep a clear finish while ordinary imports continue adding examples.

## 2. Generate crops by source

Refactor the repair loop to process sources rather than subjects.

1. Load the detector and embedding model once per worker execution.
2. Download each source once per uninterrupted attempt and verify its checksum.
3. Gather all eligible examples for that source, including examples belonging to different subjects.
4. Combine overlapping timestamp windows and decode those windows once. Use recorded example timestamps rather than replaying the entire enrollment pipeline.
5. Detect faces and compare their embeddings with each example's stored embedding. Use the existing repair similarity threshold and ambiguity margin as the starting configuration.
6. Select the highest-quality crop that passes the identity checks for each example. Apply the same approach to retained still images; use the original example timestamp windows for older submission videos.
7. Publish recovered crops incrementally and release source media and temporary buffers before processing the next source.

Represent an absent or invalid timestamp window explicitly and count it as unresolved. Keep crop selection tied to the example's embedding, rather than using a subject-wide average or another example's image.

## 3. Publish each example preview

Reuse the existing encrypted gallery storage and cleanup mechanism.

- Derive a stable representative ID from the example ID and source generation.
- Store the JPEG with its crop timestamp and quality score.
- Before publication, lock and re-read the example, resolve its current owner, and check whether it remains enabled and lacks a preview. This accommodates subject merges and edits during the sweep.
- Publish the gallery record atomically against the example. Make replay and concurrent publication idempotent, with unused uploads handled through existing cleanup receipts.
- Keep the existing image-serving API and example-card rendering. Newly attached images automatically become available through those paths.

Publication should use a dedicated per-example path rather than the existing subject-level five-image completion logic.

## 4. Resume through a source-level checkpoint

Store the sweep manifest and checkpoint in the existing encrypted batch-control storage. The checkpoint contains the sweep ID, cutoff, source position, aggregate counters, and completion state.

- Advance the checkpoint after each source has been examined and its successful crops published.
- Use a single sweep owner and generation-conditional checkpoint writes to prevent overlapping continuations.
- On interruption, resume the current source. Already-published previews are skipped automatically.
- Advance past unresolved examples after examining their source so the sweep can finish.
- Record progress and outcomes as source-level aggregate counts. Maintain aggregate totals for repaired, already covered, ambiguous/no qualifying face, invalid window, unavailable source, unsupported model, and examples changed during execution.

Per-example failure tracking is outside this design. A later, newly requested sweep can rediscover unresolved examples and try them again. Reconcile final coverage from the database so replay does not inflate the repaired count.

## 5. Run the full sweep

Package the repair mode in the existing GPU worker image and configure a dedicated Cloud Run Job named `face-example-gallery-repair` with one L4 GPU, 4 vCPU, and 16 GiB RAM. Use the existing runtime identity, private database access, encryption configuration, and bounded database pool. The job runs independently of the web console and the operator's terminal.

- Run one repair task at a time and retain the model across sources.
- Allocate one of the two normal batch GPU slots to repair for the sweep's duration, leaving one import slot and the existing interactive slot. Apply the temporary batch cap consistently across admission callers; restore the usual two import slots when repair finishes or is stopped.
- The current admission counter only counts `media_run` records. Implement the maintenance-slot allocation explicitly so the dedicated repair job is included in the capacity budget.
- Checkpoint regularly and continue through sequential executions when the task runtime budget is reached. Give each execution enough remaining time to finish its current source; replay a partially completed source after an execution interruption.
- Persist an active/completed/stopped sweep state. Use a lightweight scheduled recovery trigger to start a continuation when a sweep is active and its prior execution has ended. Check execution state and the sweep lease before launching; completion or an explicit stop makes the trigger a no-op. Have the worker start its next execution on normal runtime rollover, with the scheduled trigger covering interrupted handovers. Keep the trigger CPU-only so checking status does not provision a GPU.
- Launch directly against the entire captured workload after automated checks pass.
- Report source progress, examples repaired, remaining coverage, elapsed GPU time, bytes scanned, and generated image bytes. Use observed full-sweep throughput to update the completion estimate while it runs.

Import throughput will be lower while one batch slot is assigned to repair. Compute cost depends on decoded video windows and model work, so measure job billable time and report it alongside added gallery storage. Produce the final cost estimate from that measured usage.

### Deployment and operation deliverables

- Terraform-managed repair job, recovery trigger, and required invocation permissions.
- A build/deploy command that publishes the worker image and updates the repair job.
- A start command that creates the full-sweep manifest, records its sweep ID, and launches asynchronously.
- A status command that reads the checkpoint and Cloud Run execution state, plus structured progress logs in Cloud Logging.
- Stop and resume commands that update the sweep state and preserve completed previews. Resume continues the same workload; a new sweep creates a fresh inventory.
- A documented Google Cloud Console link for viewing executions and logs.

Starting a sweep is an explicit operator action. Once started, execution and continuation happen entirely in Google Cloud until the sweep completes or is stopped.

## 6. Automated verification

Extend the existing gallery repair tests to cover:

- Several missing examples for the same subject/source, subjects with five existing images, and sources with old fallback markers.
- One source serving multiple subjects with a single download and shared window decoding.
- Correct crop association, ambiguous faces, invalid windows, and unavailable source media.
- Publication replay, interrupted-source restart, checkpoint advancement past unresolved examples, and concurrent subject merges or example disabling.
- Gallery cleanup after an interrupted upload/publication and preservation of subject membership and embeddings.
- Maintenance capacity allocation and restoration of normal import capacity.

Validate the job configuration and run the relevant existing enrollment/gallery tests before deployment.

## 7. Completion report

Finish when every source in the captured workload has been examined. Reconcile the final database state against the starting inventory and report:

- Examples gaining an active preview and examples still missing one.
- Subjects gaining their first usable gallery image.
- Aggregate unresolved reasons and sources processed.
- Coverage of the original workload separately from examples created during the sweep.
- GPU execution time, estimated compute cost, and additional gallery storage.

Completion means the full workload was examined; recovery coverage reflects how many correct crops could be obtained. Save the report and update the session handoff with the sweep ID and results.

## Implementation locations

| Area | Files |
| --- | --- |
| Candidate selection and atomic publication | `worker/subject_backfill.py` |
| Source-oriented decoding and crop selection | `worker/gallery_backfill.py` |
| Sweep entry point, checkpoints, and continuation | New `worker/example_gallery_sweep.py`, existing storage/job helpers |
| Temporary repair capacity allocation | `worker/admission.py`, admission callers, sweep lifecycle |
| Job, recovery trigger, and configuration | `terraform/console.tf`, worker image configuration |
| Deployment, start, status, stop, and resume commands | Repository operational scripts and deployment documentation |
| Verification | Existing gallery/backfill tests plus sweep lifecycle tests |

The existing example and gallery tables support the image associations. Sweep progress belongs in the batch-control checkpoint; implementation should reuse existing infrastructure for execution ownership and capacity allocation where practical.

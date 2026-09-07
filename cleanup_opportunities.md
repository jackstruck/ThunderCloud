# Cleanup opportunities

Audit date: 2026-09-04

Status update: 2026-09-06.

- [x] Historical processing and gallery backfill complete, as confirmed by the user.
- [ ] Local-first transition and pilot (Steps 2–4).
- [ ] Backfill-specific teardown: removal is applied; Step 6 browser smoke acceptance
  remains open.
- [x] Documentation consolidation and updates.

The backfill itself is accepted as complete without a separate reconciliation check.
Earlier teardown/documentation completion markings were incorrect and were withdrawn.
The actual implementation and evidence are now recorded in
[the teardown report](face-batch-gcp-scaffold/docs/history/2026-09-06-teardown.md).
Terraform removal and retention changes are applied, with no drift; browser IAP smoke
acceptance remains outstanding.

The original read-only assessment below records the 2026-09-04 audit context, not
current runtime state. No cleanup actions were performed as part of that audit.

## Footprint at the original audit

The tracked source code is comparatively small. Most local workspace usage comes from
ignored, reproducible development tools, provider downloads, model files, and review
artifacts.

| Item | Approximate size | Recommendation |
| --- | ---: | --- |
| `face-batch-gcp-scaffold/.test-venv/` | 793 MB | Recreate when tests are next needed. |
| `bulk-download/.venv/` | 160 MB | Recreate when acquisition tooling is next needed. |
| `face-batch-gcp-scaffold/terraform/.terraform/` | 258 MB | Delete when space matters; `terraform init` restores it. |
| Docker build cache | 315 MB | Safe to prune when no image build is in progress. |
| Unused Docker image data | About 210 MB reclaimable | Prune after confirming no local validation depends on it. |
| `face-batch-gcp-scaffold/terraform/*.tfplan` | 1.2 MB | Delete after review; plans are stale point-in-time artifacts. |
| Python, pytest, and Ruff caches | About 1 MB | Safe to delete. |
| Local review video and face crops | About 38 MB | Delete after review; these are sensitive plaintext or biometric artifacts. |

The straightforward reproducible cleanup is approximately 1.7 GB. This estimate does
not include the Google Cloud SDK or model files.

## Retention guidance during the former active backfill

- `face-batch-gcp-scaffold/models/*.onnx` (about 108 MB), because local inference and
  GPU image builds use them.
- `face-batch-gcp-scaffold/data/bulk-backfill-inventory-*.json` (about 25 MB), because
  it is part of the active historical backfill audit trail.
- The append-only acquisition manifest, reviewed selection files, rollout identifiers,
  checksums, and final execution reports.
- Terraform state and its backup, local non-secret configuration, CSEK material, and
  deployed immutable image digests.
- Gallery-backfill code, migrations, and fallback records until final reconciliation.
- The local Google Cloud SDK (about 513 MB) while deployment and diagnostics depend on
  it.
- Known-good deployed and predecessor container digests until rollout acceptance.

Immutable archive objects under `videos/` are source records and must not be removed as
incidental codebase cleanup.

## Repository cleanup candidates

1. [x] Archive or remove `face-batch-gcp-scaffold/planning.md`. It is a 902-line superseded
   design document. First preserve any decisions that remain authoritative.
2. [x] Replace `face-batch-gcp-scaffold/CLEANUP_ASSESSMENT.md` with this current audit, or
   retain only a pointer to this file.
3. [x] Split `face-batch-gcp-scaffold/commands.md` into current operational commands and a
   short historical rollout record.
4. [x] Remove the tracked generated package metadata under
   `bulk-download/src/thundercloud_bulk_download.egg-info/`. It is reproducible and is
   already covered by the repository's ignore rules.
5. [x] Archive completed selection files with the final backfill report instead of keeping
   them in the active operational tree.
6. [x] Retire the former Google Cloud Batch path after historical acceptance. Candidates
   include `scripts/submit_batch.py`, its tests, obsolete Batch API/IAM configuration,
   outdated Terraform descriptions and outputs, and Batch-specific comments.
7. [x] Add an Artifact Registry retention policy that protects deployed digests while
   pruning unreferenced historical tags and manifests.

Do not remove Google Cloud Batch artifacts until the active backfill, its audit record,
and the continuing Cloud Run workflow have been accepted.

## Documentation findings (addressed)

The following original audit findings are addressed in the consolidated current docs;
old digests, counts, and disabled-gate descriptions remain explicitly historical:

- `face-batch-gcp-scaffold/README.md` still describes the console as search-only, but
  retained enrollment is enabled.
- The README and Phase 1 review say arbitrary-host fetching is disabled, but guarded
  arbitrary public HTTPS media fetching is enabled.
- `ARCHITECTURE.md` omits the managed console, upload/link ingestion, recent-runs UI,
  retained enrollment, and subject-gallery service.
- `docs/phase1-review.md` records old image digests, 115 passing tests, and the former
  22-subject bounded gallery sample.
- `openapi/phase1.yaml` does not include `GET /api/runs/recent`.
- `IMPLEMENTATION_PLAN.md` says subject split and merge tools remain unimplemented,
  although operator CLI correction commands now exist. Authorized identity assignment
  remains unfinished.
- Multiple documents describe the 1,859-video rollout and initial gallery work as
  future operations even though they have run or are currently running.

## Recommended documentation structure

- `README.md`: concise setup and product overview.
- `ARCHITECTURE.md`: canonical description of the deployed system.
- `OPERATIONS.md`: current acquisition, processing, console, maintenance, and recovery
  commands.
- `ROADMAP.md`: verified remaining work only.
- `openapi/phase1.yaml`: the current API contract, including recent runs and enabled
  enrollment policies.
- `docs/history/`: immutable rollout, acceptance, incident, and migration records.

After consolidating these documents, remove redundant narrative plans rather than
maintaining several competing sources of truth.

## Immediate work identified at the original audit

Historical live state observed on 2026-09-04 (superseded by the completion report):

- 18,144 subjects exist.
- 2,501 subjects currently have active gallery representatives.
- 2,895 representative images are active.
- 486 sources are recorded as `no_unambiguous_crop` gallery fallbacks.
- The gallery backfill is still running.
- The original 1,859-item rollout has 1,858 successes and one `INVALID_INPUT`
  dead-letter item.

The original immediate completion sequence was:

1. Allow the active gallery backfill to finish.
2. Diagnose and explicitly resolve or disposition the single `INVALID_INPUT`
   dead-letter item.
3. Review and approve any full-video processing for the 486-source fallback set.
4. Refresh and verify historical candidate attribution after gallery completion.
5. Produce a final backfill reconciliation and acceptance report, including immutable
   image digests, execution names, inventory checksums, fallback disposition, and final
   counts.
6. Complete real-browser IAP acceptance for link ingestion, uploads, cancellation,
   retries, cleanup, run recovery, and guarded arbitrary public URLs.

## Longer-term work

- Replace the temporary single-user IAP grant with an approved group.
- Add authorized subject lookup and identity editing or assignment.
- Calibrate matching and clustering thresholds against labeled same-person and
  different-person examples.
- Measure concurrent-run latency, GPU allocation time, database load, interruption
  behavior, safe parallelism, and per-run cost.
- Establish approved retention policies for embeddings, operational history, backups,
  and local review artifacts.
- Replace deprecated ByteTrack construction before the installed dependency removes
  it.
- Establish a private-only Cloud SQL administration route and then remove the public
  endpoint.

## Local-first transition and post-backfill catch-up

Newly added JustPaste URLs should not extend the temporary historical-backfill path.
They should be processed through the permanent local-first acquisition
workflow. The historical backfill is complete per the completion report above.

Use this transition sequence:

1. [x] Finish and formally accept the current historical processing and gallery backfill,
   including the dead-letter and fallback dispositions described above.
2. [ ] Implement the slim local-first
   `JustPaste -> Luluvid -> download -> verified CSEK upload -> durable enqueue`
   workflow. It must preserve a local `uploaded_not_enqueued` receipt when upload
   succeeds but enqueue fails, so a retry neither downloads nor uploads the object
   again.
3. [ ] Validate the permanent workflow with the bounded `ah7w2`, `c3bec`, and `6tg6b`
   URL set, plus an already-uploaded URL and a duplicate-content case.
4. [ ] Require the pilot to prove one immutable upload and one durable processing item per
   new source, idempotent retries, correct JustPaste/Luluvid attribution, successful
   face processing, and normal gallery publication.
5. Process the remaining newly added JustPaste/Luluvid catch-up queue through that
   accepted local-first workflow. The earlier planning estimate was approximately
   95–98 remaining entries; regenerate and review the exact unresolved count before
   launch rather than treating that estimate as authoritative.
6. Reconcile the catch-up processing and gallery generation to zero unexplained
   pending, retryable, dead-letter, missing-commit, attribution-mismatch, and staging
   records.
7. Operate the local-first workflow for an agreed observation period before removing
   its predecessor infrastructure.

## Backfill-specific teardown

Teardown is authorized by the user following historical backfill completion. Preserve
the permanent workflow and audit records while removing historical-only resources.

1. [x] Inventory every job, scheduler, alert, service-account binding, image, local script,
   selection, and staging prefix introduced solely for the historical backfill.
2. [x] Preserve the permanent `face-batch-gpu-drain`, `face-interactive-gpu`, console,
   Cloud SQL database, Artifact Registry repository, archive/gallery prefixes,
   monitoring needed for ongoing work, and all required encryption keys.
3. [x] Archive final inventories, checksums, execution names, image digests, reconciliation
   reports, and fallback decisions before deleting transient local records.
4. [x] Remove the retired Google Cloud Batch implementation and any other historical-only
   code after confirming that the local-first path has no dependency on it.
5. [x] Produce a narrowly targeted Terraform plan and verify that it removes only
   backfill-specific resources. Do not apply a broad teardown based on resource names
   alone.
6. [ ] Apply the reviewed teardown, verify Terraform drift, confirm permanent ingestion and
   interactive search still work, and record the resulting cost inventory.

Step 6 status: the reviewed teardown and corrected registry policy are applied, the
full refreshed Terraform plan reports no drift, and the cost-driver inventory is
recorded. Unit tests and a real read-only local gallery search pass. Browser ingestion
and interactive-search smoke acceptance through IAP remains outstanding (available
programmatic credentials return HTTP 401). No backfill reconciliation was rerun.

# Platform maintenance foundation — 2026-09-09

Implemented and verified locally. The full platform implementation remains active.
No production migration, deployment, source split, or queue mutation was performed.

## Delivered code and contracts

- `docs/platform-contracts.md` maps the final responsibilities and field/API changes,
  including canonical versioned suggestion dismissals, one membership authority,
  durable operator handoff receipts, and saved result versions.
- `maintenance/migrations.py` provides contiguous ordered migrations, SHA-256 file
  checksums, the administrative `platform_schema_migration` ledger, a target-database
  advisory lock, and transactional SQL/ledger checkpoints. Restart verifies the
  committed prefix. Untracked schema adoption requires a matching schema reference.
- Structural reference verification covers columns/defaults/types, constraints,
  indexes, triggers, functions, views, relations, policies, sequences, enums and
  extension versions. Named column definitions are independent of physical append
  order. Runtime grants use the same `scripts/db_grants.sql` on setup and upgrade.
- `scripts/apply_db_migrations.py` uses that runner. Selective `--migration` arguments
  are retired. The PostgreSQL integration fixture also uses the ordered runner.
- `Dockerfile.maintenance`, `maintenance/cli.py` and `maintenance/local.py` implement
  fixed container validation, source/build receipts, protected reports, heartbeats,
  detached start, blocking run with normal-exit cleanup, status, and scoped cleanup.
  `scripts/test_db_integration.sh` now invokes this complete current-schema procedure.
- Added six migration integration checks and ten maintenance tooling unit checks.
  Existing unit and browser coverage remains included.

The migration checks cover fresh setup versus tracked upgrade and verified adoption,
vector preservation on the synthetic upgrade, invalid adoption, schema/trigger drift,
changed checksums, newer databases, atomic rollback, restart after a committed step,
concurrent lock rejection, runtime grants, and equivalent named column definitions
with different append history. Tooling checks include missing/failed terminal
reports, export verification after container removal, cleanup ownership protection,
Docker daemon failures, and durable failure logs.

## Final build and verification

Final local immutable image ID:

```
sha256:2594bb9f4db7da07a215577aa0379ee24294e0c4342db8b1fc807de5e653aebd
```

Source/build fingerprint (includes the uncommitted workspace used by the build):

```
9b535d778c72521d4c05a21fdcc4a7cb7d5d26b4994c39cc16272d0e91df2b82
```

This is a local Docker image ID, not a published registry digest. No image was
pushed or deployed. Build inputs exclude credentials, database copies, Terraform
state, models and virtual environments. The image has maintenance purpose, build
execution and source-fingerprint labels.

Executed from `face-batch-gcp-scaffold`:

```bash
.test-venv/bin/python -u -m maintenance.local run \
  --execution-id platform-validate-20260909-final \
  --image sha256:2594bb9f4db7da07a215577aa0379ee24294e0c4342db8b1fc807de5e653aebd \
  --artifacts /workspaces/ThunderCloud/.local/platform-validate-20260909-final \
  --retention-deadline 2026-10-09
```

Outcome: **succeeded**, 161 tests passed, zero failures/errors/skips, 51.126 seconds
for the test stage. This includes 130 unit checks and 31 PostgreSQL/Chromium
integration checks. Ruff and both JavaScript syntax checks passed in the image;
`git diff --check` passed in the worktree. Fresh schema reference reached migration
010. The image's PostgreSQL is isolated on loopback without external networking or
published ports, and the procedure uses only synthetic fixtures.

Final machine/human reports, JUnit results, stage logs, four browser screenshots,
schema reference and exported terminal container state are under the artifact path
above. `cleanup.json` records artifact SHA-256 hashes. Build receipt and file hashes
are under `.local/platform-maintenance-build-20260909-r5/`.

The current migration reference is for local pgvector 0.8.6. Live is 0.8.5. The
strict verifier correctly refuses to certify those as equal. Generate the adoption
reference on a disposable PostgreSQL/pgvector version matching production before
rehearsal/adoption, or explicitly review a supported extension upgrade. Do not edit
a reference's extension fingerprint to bypass this check.

## Read-only live inventory

Captured 2026-09-09 around 12:38–12:41 UTC using existing ADC/IAM mechanisms.
Database queries used a repeatable-read, read-only transaction and statement timeout.

| Observation | Current evidence |
| --- | --- |
| Console revision | `face-console-00016-pjh`, 100% traffic |
| Jobs | `face-batch-gpu-drain`, `face-ingest-drain`, `face-interactive-gpu` |
| Current active subjects | 22,960 |
| Unified examples | 43,670 |
| Active gallery images | 8,682 |
| Multi-source active subjects | 0 at inventory time; no forced cleanup performed |
| Run states | 6 succeeded, 2 failed, 2 cancelled |
| Run operations | No queued/leased operations at inventory time |
| Archive work | 1 retry, 3 dead letters, 1,878 succeeded |
| Migration ledger | Not installed live |
| Database | PostgreSQL 17, runnable; pgvector 0.8.5 |
| Protections | IAM database authentication on, encrypted connections required, zero authorized networks, CMEK configured, backups enabled |
| Schedulers | Ingest reconciliation and daily maintenance enabled |

The remaining archive retry must be reconciled or explicitly transferred before
queue retirement. These are inventory observations, not a submission pause, fresh
recoverable backup receipt, or data-preservation rehearsal.

Protected evidence files under `.local/`:

- `platform-live-inventory-20260909.json`: cloud settings and scheduler inventory.
- `platform-live-images-20260909.json`: exact deployed console/job image references.
- `platform-live-schema-20260909.json`: schema fingerprint, aggregate counts and work states.
- `platform-schema-differences-20260909.json`: investigated schema differences.
- `platform-maintenance-20260909-resources.json`: delivery resources and evidence hashes.

The initial comparison found only extension-version and historical column-position
differences. The final fingerprint compares named columns without ordinal positions;
the extension version remains a required match. No migration history was recorded
in production based on the historical migration numbers alone.

## Cleanup and remaining scope

The final `run` automatically removed its runner and database containers after
exporting terminal evidence. An explicit cleanup replay passed, and status still
verified **succeeded** from the checksum-verified exported state/report. Development
validation containers and the separate migration-test container were also removed.
Four superseded local image IDs were removed without pruning parent images. No
execution-owned network or persistent database volume remains. Build staging
folders were removed on normal build exit. Shared Docker build cache and the
pre-existing source-split container were preserved.

Retained: final reusable local image, protected build/validation/inventory evidence
and diagnostic receipts. Owner: ThunderCloud implementation. Retention deadline:
2026-10-09. Reason: continued migration implementation, reproducible validation and
review evidence. Cloud temporary resources were not created. No procedure or test
process remains running after this milestone.

**Next action:** implement protected-copy `rehearse-migration` and guarded
`apply-migration` using the same migration engine, with data digests/invariants,
backup and exact-target checks, queue/submission cutover preconditions, durable
cloud reports, and a manually invoked Cloud Run Job. Obtain the matching pgvector
reference for verified adoption. Then implement the common processing/enrollment
framework, suggestions and predecessor retirement. Production cutover authorization,
real IAP browser acceptance, migrated-scale performance and user UI review remain
outstanding. Section 6 is not complete merely because `validate` now succeeds.


## Protected-copy and reviewed-apply increment

The read-only production copy completed at 13:22 UTC. All 21 public application
 tables were copied in one repeatable-read source transaction with foreign keys
 enforced on the isolated target, then re-exported and compared using per-table
 byte hashes. The protected custom-format backup is 289,546,977 bytes, SHA-256
 `096722a1ac60d1fd373c65e32eeabfc844460aa24469d96e49c5630e776d4fd9`.
 Receipts and backup are under `.local/platform-copy-20260909/` (owner: platform
 implementation; retain through 2026-10-09). No production data was changed.

The copy contains 43,670 examples, 22,984 total subject rows (including merged
records), 8,703 gallery records (including retired records), 1,876 sources, 1,749
correction events and 320 saved candidates. All ten baseline invariants passed,
including recomputation of canonical representations from current examples.
These are total retained-row counts, not substitutes for active inventory counts.

A production-matching PostgreSQL 17 / pgvector 0.8.5 image was pinned at
`pgvector/pgvector@sha256:815bf5378222044da3b34d98e6a5fdac37b15c428b67d09c7c2d90a038e597bf`.
The local `rehearse-migration` procedure now restores isolated migration/recovery
copies, generates a fresh reference, verifies preservation, and exercises ledger
adoption/reconnect/resume. The guarded apply entry point binds reviewed artifacts,
observes database identity, paused roles and drained sessions/queues, verifies data,
and checks runtime grants. A synthetic integration test verified refusal gates
and resume after committed DDL. Cloud execution/reporting remains unimplemented.

Image `sha256:a8aa1e7f2066573a21887218faf5aeb3597f16af23a2aae141c71efce56bd96f`
passed container validation: **166 tests, zero failures/errors/skips**, test stage
117.475 seconds; lint and JavaScript checks passed. Evidence is under
`.local/platform-validate-20260909-r6/`; its owned containers were cleaned up.
The same image's protected-copy rehearsal is execution
`platform-rehearsal-20260909-r6`, with durable progress under the matching `.local/`
directory. The terminal report now confirms **succeeded** with all ten data invariant checks
passing, matching baseline/final digests, a matching fresh schema reference, and
successful recovery/resume. Restore stages took 106.746 and 133.027 seconds;
migration/adoption plus verification took 67.679 seconds. Scoped cleanup removed
both owned containers and exported the reports. This tests baseline 010 adoption,
not the still-required final consolidation migration.

After that build, inspection found that private subprocess output was still
written despite the suppression flag. This is fixed in source and verified by a
stdout/stderr regression test. Preservation reads now emit periodic heartbeats.
Image `sha256:9cca94e63b61ac1f669f053987f1d165c89ec62c58f44f5a4591a9ee0c920157`
contains those fixes; complete container validation/rehearsal is still required.
Its build receipt is `.local/platform-maintenance-build-20260909-r7/`.

Following codespace disk/CPU warnings, only explicitly inventoried build-cache
records were pruned, and the completed copy/test containers were removed after
backup hash verification. `.local/disk-cleanup-20260909.json` and the protected
copy's `resource-cleanup.json` record ownership and retention. The active rehearsal
was capped to 0.75 CPU for PostgreSQL and 0.25 CPU for its runner, recorded in
`cpu-limits.json`. These limits are now the local orchestration defaults; remaining
heavy checks run sequentially. Shared resources and protected artifacts remain.

## Cloud artifact publishing

The maintenance CLI now accepts `--cloud-artifacts gs://BUCKET/EXECUTION_PREFIX`.
Progress updates are uploaded outside the container with generation preconditions;
a new execution cannot overwrite an existing prefix's progress, and a stale writer
cannot overwrite a newer generation. Final export uploads supporting artifacts
and a checksum/generation manifest before publishing `report.json`. Status tooling
must require the final report and terminal execution state, not infer completion
from progress alone. Local reports remain protected on disk.

Fourteen focused artifact/report tests passed, including competing writers,
interrupted artifact export, and existing maintenance report behavior. No cloud
objects or jobs were created during this implementation. The Cloud Run launcher,
input retrieval, remote resume, combined execution/report status and manifest-scoped
cleanup are still to be connected; this component alone is not the full cloud path.

## Cloud input loader and resume entry point

`python -m maintenance.cloud_runner` accepts an exact request object URI,
generation, SHA-256 and byte count. The request pins the plan, backup, backup
receipt, rehearsal receipt, schema reference and before receipt separately.
Downloads use generation preconditions, private temporary files and checksum/size
verification before invoking the existing `apply-migration` command. Request and
backup bytes are not copied into report exports.

A resumed request pins its prior progress object under the same output prefix.
The loader restores that report for the existing manifest/image resume checks and
pins the progress writer to that generation. Reports record the Cloud Run
execution identifier for later status correlation. Five focused loader/artifact
checks passed, including corrupted input preventing invocation and stale-writer
protection. CLI help, Ruff and whitespace checks passed. No cloud invocation was
performed. Launcher, execution/report status and scoped cleanup remain unfinished.

## Cloud execution/report status

`python -m maintenance.cloud status --resources /path/to/resources.json` reads the
exact execution recorded in the resource manifest and the durable reports under
its output prefix. It checks execution UID and pinned image, correlates reports
with the Cloud Run execution name, and requires both terminal task success and a
matching final success report. Missing execution/report evidence is incomplete;
a failed/cancelled execution is failed; progress alone is never completion.
Observation errors propagate and do not authorize restarting work.

The implementation uses the documented Cloud Run v2 execution fields:
https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs.executions
Focused tests passed for stale/missing reports, active/failed/missing executions,
and replaced execution identity. No cloud reads or writes were performed during
these fixture checks. Start/resume orchestration must populate the execution name
and UID in the resource manifest; that integration and scoped cleanup remain.

## Cloud job launcher

`python -m maintenance.cloud_launch prepare --config CONFIG.json --request REQUEST.json
--request-reference REQUEST-REFERENCE.json --artifacts DIRECTORY` creates a local,
reviewable job/resource manifest. Configuration requires an explicit project,
region, unique job ID, digest-pinned image, runtime service account, network,
subnetwork, CMEK, CPU/memory, measured timeout, owner and retention deadline.
The request reference pins an already-uploaded request by URI/generation/hash/size.
For an OCI index, add `--image-index PUBLISHED-INDEX.json` with the exact raw
registry manifest. Status verifies its checksum against the pinned index digest
and accepts only its Linux amd64 child as the resolved execution image.

`python -m maintenance.cloud_launch start --resources DIRECTORY/resources.json`
creates the dedicated job and submits one execution. One task and zero automatic
retries are fixed. A local launcher lock serializes use of that resource manifest.
Creation and run intent/operation handles are persisted before advancing. Lost run
acknowledgement is reconciled from the dedicated job's execution list without
repeating the run request. A job-name collision or changed observed job spec stops
execution. The resulting execution name/UID feed the existing cloud status command.

Two focused launcher tests passed, including a lost run acknowledgement without a
second submission and refusal to reuse an existing job. API fields follow the
Cloud Run v2 TaskTemplate and jobs.run references:
https://docs.cloud.google.com/run/docs/reference/rest/v2/TaskTemplate
https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs/run
No live job was created. Input upload preparation, remote resume admission,
durable resource-manifest export, multi-host submission coordination and cleanup
still require completion before the cloud workflow is ready for cutover.

## Durable launcher coordination

Launcher state is now generation-guarded in
`OUTPUT_PREFIX/control/JOB_ID/resources.json` before create/run actions. Each
advance must claim the generation recorded in its local manifest; a stale copy
cannot issue another action. The local file records the returned object generation.
If a remote write commits but its local acknowledgement is lost, restore the
remote record rather than retrying from stale state.

`python -m maintenance.cloud_launch restore --control-prefix gs://BUCKET/PREFIX/control/JOB_ID
--artifacts NEW_DIRECTORY` recovers the recorded state and exact generation without
submitting work. Then `start --resources NEW_DIRECTORY/resources.json` reconciles
the existing job/operation. Five focused launcher/status checks passed, including
stale launcher refusal and restoration after a pending create request. No live
cloud resources were created. Input staging, remote resume admission and cleanup
remain before cutover readiness.

## Remote resume admission

`python -m maintenance.cloud_resume --resources PREVIOUS/resources.json
--request ORIGINAL-request.json --artifacts NEW_DIRECTORY` reads the previous
execution and durable report/progress. It refuses missing, replaced or still
active executions and refuses an already successful migration. It preserves the
reviewed input references and image and writes a new request pinning the exact
prior progress generation and execution UID. Existing plan expiry/checksum checks
still apply; resume cannot bypass re-review of an expired plan.

Stage the new request and use a new dedicated job ID for its launcher manifest.
The launcher requires paired resume execution/progress evidence and rechecks the
prior terminal execution before advancing submission. Five focused resume/launcher
checks passed, including refusal for active/missing execution and unchanged input
references on valid resume preparation. No live resume was attempted. Input
staging and resource cleanup remain to complete the cloud command workflow.

## Cloud input staging

`python -m maintenance.cloud_stage inputs --inputs INPUTS.json --execution-id ID
--input-prefix gs://BUCKET/EXECUTION/inputs --output-prefix gs://BUCKET/EXECUTION/reports
--artifacts DIRECTORY` stages the six fixed migration inputs. `INPUTS.json` maps
`plan`, `backup`, `backup-receipt`, `rehearsal-receipt`, `schema-reference`, and
`before` to local files. The command writes `request.json`, `request-reference.json`
and `staging.json`; those files feed launcher preparation directly.

Uploads stream from files with GCS checksum validation and create-only generation
preconditions. Content-addressed names support interrupted handoff without a
second backup upload. Existing objects must match local SHA-256 metadata, CRC32C
and byte count; changed staging inputs require a new artifact directory. Exact
generations and checksums are recorded for the runner's independent verification.

For a prepared resume request, `python -m maintenance.cloud_stage request
--request NEW/request.json --input-prefix gs://BUCKET/EXECUTION/inputs
--reference-output NEW/request-reference.json` stages only that request, retaining
its existing pinned backup/input references. Four focused staging/loader checks
passed, including reuse of verified uploaded bytes and rejection of corrupted
object evidence. No cloud inputs were uploaded in these fixture checks. Scoped
cleanup and integrated cloud workflow validation remain unfinished.

## Scoped cloud job cleanup

`python -m maintenance.cloud_cleanup --resources DIRECTORY/resources.json
--exports EXPORT_DIRECTORY` verifies the exact job UID/specification and terminal
execution, refuses additional unrecorded executions, exports generation-pinned
report objects plus execution metadata, and records checksums before requesting
job deletion with its current etag. Export failure prevents deletion. Rerunning
verifies that the recorded job has disappeared; missing jobs without prior export
evidence do not imply successful cleanup. The durable launcher manifest closes
submission once cleanup begins.

Recovery request/input references, reports and image digests remain retained with
owner, reason and deadline in the export receipt. The command does not delete shared
identities, network, encryption keys, buckets or media. Six focused cleanup/launcher
checks passed, including export failure preventing deletion, running-execution
refusal and repeated cleanup verifying disappearance without another delete.
The API deletion guard follows:
https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs/delete
No live cleanup was run. Full cloud workflow verification, incomplete-launch
resource cleanup and retention-expiry disposal still need final review.

Cleanup now also handles a job whose creation completed before launch was
interrupted: it first generation-guards a closed launcher state, verifies the
observed job is settled and matches the manifest, confirms its execution list is
empty, and exports a not-submitted snapshot before deletion. An ambiguous
run-requested state still requires execution reconciliation. Seven focused
cleanup/launcher checks passed. This also prevents a stale launcher from advancing
a job while cleanup is reviewing it. No live resources were removed.

## Retention-expiry disposal

`python -m maintenance.cloud_purge --resources DIRECTORY/resources.json
--receipt purge-receipt.json [--staging STAGING/staging.json]` requires completed
job cleanup, an export receipt, no recorded recovery hold, and an expired retention
deadline. It verifies local exported bytes before deleting only listed object
generations. Supplying the matching request's staging manifest also includes its
input objects, after verifying their retained local copies. It never lists a
bucket to infer deletion targets. Replaced generations are not deletion targets.
Required deployed/rollback image digests remain under their separate registry
retention policy.

A focused check passed for deadline refusal, exact-generation deletion, and
changed local exports preventing disposal. No cloud artifacts were deleted.
Integrated command workflow and production-cutover acceptance remain outstanding.

## Integrated image validation and queue cancellation review

Image r10 (`sha256:a48a96f12c1bb4c4ebb35c81f314da4d20e66b6716e2b2975e8271170989ebad`)
passed isolated validation: 188 tests, zero skips/errors/failures, plus lint and
JavaScript checks. Execution-owned validation containers were removed after report
and screenshot checksum export. Artifacts remain in `.local/platform-validate-20260909-r10`.

A read-only inspection of the protected backup found 1,878 succeeded work items,
three dead letters, and one retry marked `OPERATOR_CANCELLED` whose parent rollout
is `cancelled`. This is deliberately cancelled work, not work to restart. Queue
retirement and apply preconditions now recognize only that exact retry/cancelled
combination as terminal disposition; other pending/leased/retry work still blocks.
A focused check verifies that the error code alone does not permit retirement.
Earlier notes requiring this particular retry to be drained are superseded.

Integration review also corrected cloud runner exit-code propagation and allowed
successful migration status to retain a separate cleanup to-do list. Focused cloud
checks passed. Image r11 refreshes these corrections before the protected-copy
rehearsal; no production work or source media was modified.

# Platform cutover review — 2026-09-09

**Status: complete. Deployed and reopened on 2026-09-10; user sign-in and UI review confirmed on 2026-09-11.**
Production migration, deployment and the archive processing/cleanup acceptance run
passed. Application logins and both schedulers are restored. Recovery export and
retired-gallery cleanup are complete; Terraform reports no drift.

## Recovery artifact disposal — 2026-09-10

At the user's request, the October 9 retention period was released early. All 25
recorded cloud recovery objects, both local cutover database snapshots, and local
recovery exports were removed. Terraform removed the dedicated recovery bucket and
its two remaining access/key bindings; no temporary migration resources remain.
The production database, archive, galleries, application images and separate
source-split records were not part of this disposal. The historical backup/export
paths and recovery-window instructions below describe artifacts that are now removed.

## Release and evidence

| Artifact | Verified result |
| --- | --- |
| Integrated maintenance validation r10 | 188 tests, zero failures/errors/skips; browser artifacts and lint/JavaScript checks passed. Subsequent migration and cloud-control changes passed their focused checks. |
| Protected-copy rehearsal r12 | Schema 010 → 024; fresh-schema fingerprint matched; all 14 preservation projections matched; ten invariant checks reported zero violations; recovery/resume succeeded. |
| Migrated-scale potential matches | Actual service calls against 22,984 subjects and 43,670 examples: three largest active subjects returned ten candidates in 0.4942–0.5877 seconds; dismissed mode returned zero in 0.0454–0.0892 seconds. CPU caps: database 0.75, runner 0.25. These are local-copy measurements, not production latency promises. |
| Console image | `sha256:f552659086a5be668d48ac92ce033cb8f0b196d522d467facda99dcfa8d457b9`; includes the focused expiry fix described below. |
| Worker image | `sha256:1dd2ac939fe43778bc08d4c2211eeda719e2babd25eac5f078c2e5cb84798859`; imports, final entrypoints, CUDA provider and packaged model hashes passed. Actual L4 model execution passed on 2026-09-10; its temporary verification job was removed. |
| Maintenance image | `sha256:e45402225f755ae0646b1b3776aa4518514f8bd517f3673c78fcf370d17a2cdf`; used for the successful r12 rehearsal. Use the current host-side cloud lifecycle commands for launch/status/cleanup. The subsequent final-control-export fix does not change this image's migration or verification procedure. |

Images reuse the deployed dependency/model layers after verifying packaging metadata
is unchanged except for entrypoints. Source fingerprints and exact build contexts
are in the private release-build directory. All three images were published and their registry manifests verified on 2026-09-10.
Production is running the reviewed console and worker images; console revision
`face-console-00018-6lp` is ready, and the predecessor GPU-drain job is gone.

Protected evidence directories, relative to `/workspaces/ThunderCloud`:

- `.local/platform-validate-20260909-r10`: integrated validation and browser artifacts.
- `.local/platform-rehearse-20260909-r12`: migration/recovery report, before/after hashes,
  schema reference, rehearsal receipt, performance measurements and cleanup receipt.
- `.local/platform-release-build-20260909`: console/worker source contexts, fingerprints,
  image IDs, build logs and packaging checks.
- `.local/platform-cutover-review-20260909`: live inventory, reviewed Terraform plans,
  migration plan/checksum, launcher configuration and staging input paths.
- `.local/platform-copy-20260909`: protected baseline dump and copy verification.

## Proposed changes

The application Terraform plan has **five updates and three deletions**:

- Update the console, CPU acquisition job and interactive GPU job to the new images.
- Update registry retention to preserve deployed/rollback images, and the execution
  error alert to cover the common jobs.
- Delete the predecessor `face-batch-gpu-drain` job and its rollout/dead-letter alerts.

The separate archive-retirement unlock plan changes only that job's Terraform
`deletion_protection` from true to false. Apply it only in the approved window, then
regenerate the main release plan against the resulting state and confirm the same
reviewed changes. Never apply a stale saved plan after another plan changes state.

The standalone [temporary infrastructure module](../maintenance/infrastructure/README.md)
adds nine resources: a dedicated migration identity, narrowly scoped access to the
admin-password secret and Cloud SQL connector, attachment permission, a protected
regional CMEK artifact bucket and its access/key bindings. It owns no application
resources. Its initial plan contains no updates or deletions.

The migration retires redundant ownership/policy fields, the old enrollment table,
and the old rollout/work-item tables. Examples remain authoritative; origin evidence,
correction history, saved result IDs/scores, galleries and sources are preserved.
No migration audit copies are created. A missing historical archive generation is
resolved only from unique successful work with the same source URI and SHA-256.

## Window sequence

1. Publish the three reviewed image digests and verify the registry manifests match.
   Apply the approved temporary infrastructure plan. Preserve its state and resource
   manifest in protected durable storage. Stage nothing into the media archive.
2. Pause `face-ingest-reconciliation` and `face-phase1-maintenance`. Pause application
   database logins for the three roles named in `migration-plan.json`, close their
   remaining sessions, and confirm no active processing remains. Do not revive the
   explicitly operator-cancelled archive retry.
3. Capture the fresh inventory and a recoverable database snapshot under the pause.
   Use administrative access from the existing secret/connector mechanism, because
   application IAM logins are paused. Verify the snapshot against the live baseline.
   Rebind the migration plan to that exact snapshot and its successful rehearsal;
   if the baseline changes, resolve the difference and rehearse before proceeding.
   Keep the reviewed schema/migration/image pins unchanged. The prepared plan is
   time-limited and bound to the earlier copy; it is not a substitute for this step.
4. Stage the fixed plan, backup and receipts into the dedicated input prefix with
   `maintenance.cloud_stage`. Prepare the unique job from `launcher-config.json` and
   the generation-pinned request reference. Launch once with `maintenance.cloud_launch`.
   The reviewed task has one CPU, 2 GiB memory, a 1,800-second timeout and zero automatic
   retries. It uses private VPC connectivity and the existing regional key.
5. Observe `maintenance.cloud status` using its exact resource manifest. Require a
   terminal successful execution and matching final verification report. A missing
   report or disconnect is not success; reconcile the same execution. Any resume
   must use `maintenance.cloud_resume` with terminal execution evidence and unchanged
   reviewed inputs. Do not deploy after failed or incomplete verification.
6. Apply the archive unlock plan, regenerate/review the release plan, and deploy the
   final console, CPU and GPU components together. Confirm exact image pins and that
   the predecessor job and alerts are gone. No source or gallery objects are deleted.
7. Restore the reviewed application logins for acceptance while schedulers remain
   paused. Check IAP browser access, all input/handling paths, GPU execution, grouping,
   retry/cancel, exact-generation cleanup, gallery publication, and subject edits/moves/
   merges. Review potential matches and the four requested UI changes with the user.
   Confirm database/encryption protections and migration counts. Re-enable schedulers
   after operational acceptance; record the user browser/UI review separately before
   declaring the full implementation complete.

At the 15:35 UTC read-only inventory, all ten runs and all 17 operations were terminal.
The only old retry carried `OPERATOR_CANCELLED` under a cancelled rollout. Counts were
22,984 subjects, 43,670 examples, 1,876 sources and 8,682 active gallery images.
Database `face_index` had OID 16565. Recheck these facts in the window.

## Recovery and cleanup

On migration/deployment failure, keep submissions paused. Recovery restores the
matching pre-cutover application **and** database together, using the fresh protected
snapshot. Do not run the predecessor application against partially migrated data.
The recorded rollback console digest is
`sha256:c1bbef99ab00c6c7eadffbdef94f0554e0967996b22626ed9284a96e2e0422d6`;
the worker digest is
`sha256:b25d5212b7b32ecc0762d5d6eddee50dfa741d492a3604f537639cdc3c7aa1c0`.
Preserve the corresponding Terraform state/configuration and old-job specification.

After acceptance, export checksummed reports and run the manifest-scoped cloud job
cleanup. Its final control generation is exported separately so later disposal also
removes that record. Disable the temporary runtime resources through their separate
Terraform state. Retain recovery artifacts, required images, bucket access and key
permission through 2026-10-09, with owner ThunderCloud implementation; extend only
for an explicit recovery hold. After expiry, purge exact recorded generations and
then remove the empty temporary bucket and remaining owned bindings.

The r11/r12 rehearsal containers and focused test container have already been removed
with receipts. The unrelated source-split container and protected source-split data
were preserved. Local release images and recovery evidence remain intentionally
retained. The terminal migration job and all six temporary runtime resources were removed.
The recovery bucket, operator access and storage-key permission, along with the
local recovery exports, were subsequently removed at the user’s request.

## Production verification — 2026-09-10

The single cloud execution `face-platform-migration-20260909-blvpd` succeeded at
07:13 UTC. Schema 024 matches the rehearsed fresh-schema fingerprint; all fourteen
preservation projections matched and all ten invariants had zero violations.
Application grants were applied for the three reviewed roles. The paused fresh
snapshot and rehearsal are in `.local/platform-copy-20260910` and
`.local/platform-rehearse-cutover-20260910`.

The release applied exactly five updates and three deletions after the separate
one-field retirement unlock. While roles were NOLOGIN, Terraform plans used the
verified pre-pause state with `-refresh=false`: Cloud SQL user refresh otherwise
misclassified paused IAM roles as absent. No IAM user was recreated.
The final image values are saved in the local `terraform.tfvars`.

Live IAM database reads preserved 22,984 subjects and 43,670 examples. The actual
potential-match service returned ten candidates in 4.35 seconds including the
Cloud SQL connection from the codespace; subject filtering and recent-run reads
also passed. The search-only archive acceptance run is
`bc5e744e-6aed-46d6-809f-54f7471d7b81`; it succeeded with 70 versioned candidate
rows. All fourteen temporary generations were deleted, original archive storage
was preserved, and subject/example counts remained unchanged. Both schedulers
were restored after these checks. The user confirmed browser sign-in and the requested UI changes on 2026-09-11.

The archive live check found missing source-read access on the CPU acquisition
identity. Terraform now grants it `roles/storage.objectViewer` only under the
configured archive prefix; the existing input generation/checksum and CSEK checks
still apply. The follow-up plan added exactly this one conditional binding. The
failed acceptance run was retried through the normal retry service with its same
run ID and input receipt.

A final permissions review also added delete-only access on `subject-gallery/` for
the existing maintenance identity. This supports the existing retired-generation
cleanup queue without granting gallery creation or update. The CPU job now receives
`FACE_SOURCE_PREFIX` from the same Terraform value used by its archive-read grant.
The follow-up plan added the custom role and conditional binding and updated only
that job environment; application image pins remain unchanged.

## Expiry fix found during live cleanup

The archive run succeeded through CUDA detection and matching. Its cleanup then
hit a predecessor expiry bug: a successful `no_faces` run could not become expired
while retaining that success-only outcome. `MaintenanceRepository.expire_runs` now
clears the outcome and retryability while retaining schema-required source attribution.
The established source media and result expiry behavior is unchanged. A focused
PostgreSQL regression covered no-face uploads and URLs, exact-generation cleanup
queueing, and repeated expiry; it passed. Its disposable database was removed.

The console/acquisition image was updated from the initial cutover image using the
same dependency layer, with only `worker/maintenance.py` changed. Image publication
was checksum-verified; the release updated the console, CPU job and registry retention.
GPU and migration images were unchanged. Protected build artifacts are in
`.local/platform-expiry-fix-20260910`. The failed cleanup execution
`face-ingest-drain-qsg2t` was terminal before launching cleanup on the fixed image.

## Final operational status

Both schedulers are enabled. The fixed cleanup execution
`face-ingest-drain-jmncb` succeeded and removed all fourteen temporary generations
from the acceptance run. A subsequent retry resolved all 21 retired-gallery cleanup
entries that had failed before cutover; all 8,682 active gallery images remain.
There are no queued or leased run operations. The final refreshed Terraform plan
reports no changes.

Migration outputs and final infrastructure state are retained in the dedicated
CMEK bucket and exported under `.local/platform-cloud-export-20260910`. The local
resource manifest `.local/platform-cloud-apply-20260910/resources.json` records
`cleaned`, all exported objects, and the last control generation separately.
The three recovery resources originally retained through 2026-10-09 were removed
on 2026-09-10 at the user’s request. No cutover or
regression-test containers are running; the unrelated source-split container and
protected recovery data were preserved. Codespace free space is 3.6 GiB (12%).

On 2026-09-11, the user confirmed that sign-in works and the requested UI changes
look right. This completes real-browser IAP acceptance and the user’s visual review.
The existing IAP configuration was preserved.

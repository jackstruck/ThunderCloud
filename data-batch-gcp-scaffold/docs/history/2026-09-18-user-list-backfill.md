# User-list acquisition backfill — 2026-09-18

The user supplied `user-links.txt` and requested copying it into the downloader
inputs and launching a cloud backfill with retain_and_enroll. Copied byte-for-byte
to `bulk-download/input/hotscope_users.txt`: 935 valid unique profile URLs.
Input SHA-256: `688efb9838c6cc32faae7b6b7dc45c293331f31567406d643a5a228e7f926dfd`.

Before launch, explained that repeated video links map to stable IDs/archive paths,
archive handoff reuses existing runs for the same object generation and policy,
and enrollment serializes the SHA-256 lookup and subject creation under the shared
advisory lock. Exact-content repeats reuse enrolled subjects and preserve alternate
URLs. Different encodings/container bytes can have different SHA values; this is
not perceptual video deduplication. The downloader's manifest hash index is local
to its checkpoint; SHA enrollment reuse is library-wide. Distinct provider IDs may
still create distinct acquisition archive objects before enrollment reuse.

The user then explicitly requested skipping tests and launching. No further tests
were started after that instruction. Already-started checks were not a launch gate.
The deployed acquisition module was confirmed byte-identical to the reviewed local
module. The new task wrapper inherits the current acquisition image and adds only
`worker/user_backfill.py` and its profile-validation dependency.

Job: `face-user-backfill-20260918`
Execution: `face-user-backfill-20260918-kbxd6`
Backfill ID: `ffdd46ef-ffe6-4550-ab88-a7923e858251`
Image: `acquisition-batch@sha256:8557fed9d0dd8ab94fe1a17e24b7c8c1731d4b4575c73f0bcd5b21ff2d7cee90`
Artifact repository: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/`.

935 tasks, parallelism 1, three retries per task, inherited seven-day per-task
acquisition timeout. Each task runs the existing checkpointed acquisition path for
one profile. All use principal `jack@jackstruck.info` and retain_and_enroll; the
existing three-active-run database quota limits handoff. GPU new-source automatic
matching remains 0.4. A completed task means acquisition/handoff has finished,
not necessarily that every GPU run succeeded. Per-video failures remain in the
profile checkpoint even if the acquisition task exits successfully.

Pinned CSEK-encrypted input in bucket `teak-banner-dome-bulk-videos`:
`batch-control/hotscope-20260914/user-backfills/ffdd46ef-ffe6-4550-ab88-a7923e858251/user-links.txt`
Generation: `1789711768014960`.
Each task verifies the generation, SHA and unique-user/task counts.

Per-user batch ID is UUIDv5(backfill UUID, `user:<username>`). Checkpoints live at
`batch-control/hotscope-20260914/profiles/<batch-id>.json`, using the standard batch
schema, automatic all-track selection and stable submission receipts. Task retries
in the same execution resume checkpoints. Another execution cannot take ownership
silently: inspect and explicitly prepare resumption rather than blindly running the
job again. CAS writes protect checkpoint updates. A failed task does not alter
another profile's checkpoint.

Private `.local/user-backfill/launch.json` contains the task-index/batch-ID mapping;
that directory also has the exact creation script, job template, deployment logs,
and execution snapshots. The dedicated job is operational/ad hoc, not currently a
Terraform resource. Existing face-acquisition-batch configuration was not replaced.
Custom Docker login was logged out and the temporary test database was removed.

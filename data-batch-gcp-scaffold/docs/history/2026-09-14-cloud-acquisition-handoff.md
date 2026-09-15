# Cloud acquisition handoff — 2026-09-14

The operator requested moving the ongoing 747-link batch off the workspace.
Handling remains `retain_and_enroll` with `all_tracks`; the original operator
principal and existing receipt keys were preserved.

- Local cutover: 144 completed downloads, one recorded acquisition failure,
  nine durable processor receipts. The local downloader and dispatcher were stopped.
- Checkpoint: `gs://teak-banner-dome-bulk-videos/batch-control/hotscope-20260914/state.json`,
  CSEK encrypted, initial generation `1789385657096448`.
- Job: `face-acquisition-batch`, `us-central1`, one CPU / 2 GiB, one task,
  three retries, seven-day task timeout.
- Execution: `face-acquisition-batch-lcnbk`.
- Image: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/acquisition-batch@sha256:0698549725ab28226b2b984b9daf83d09ecb6c1c2a87266463a95bfedd853f80`.
- Runtime identity: existing console/ingest service account; added narrowly scoped
  archive-create and batch-checkpoint permissions. CSEK is read from Secret Manager.
- Verification: cloud checkpoint advanced to 148 acquired and eleven submitted,
  with eight succeeded, one matching, and two fetching at observation time.
  Both local runner PIDs were absent. This is progress evidence, not batch completion.
- Validation: four focused acquisition/handoff tests, container import smoke check,
  Terraform validation, and live cloud download/checkpoint/submission progress.

Original archive objects remain immutable. Task restarts use encrypted checkpoints
and stable receipt keys. New acquisitions obey the processor's 250 MiB per-video cap.
Local manifests and status files are now historical snapshots; consult the cloud
checkpoint and Cloud Run logs for ongoing status.

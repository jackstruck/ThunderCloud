# Start here: ThunderCloud

This file is an entry point for a new developer or agent. It is not a live status
report. Read the current guides before using dated release records: [project
README](data-batch-gcp-scaffold/README.md), [operations](data-batch-gcp-scaffold/OPERATIONS.md),
[architecture](data-batch-gcp-scaffold/ARCHITECTURE.md), and
[roadmap](data-batch-gcp-scaffold/ROADMAP.md). The downloader has its own
[README](bulk-download/README.md). Historical design and deployment evidence is
indexed in [docs/history](data-batch-gcp-scaffold/docs/history/README.md).

## First steps

1. Run `git status --short` and review existing diffs. Preserve work already in
   the tree; do not reset it during setup or cleanup.
2. Follow `data-batch-gcp-scaffold/OPERATIONS.md` for prerequisites, local setup,
   tests, deployment, and recovery. Apply the ordered migrations for a new or
   upgraded database; do not pick individual SQL files from `migrations/`.
3. Check the live console, Cloud Run jobs, database migration ledger, and Terraform
   plan before treating the documented deployment as current. The guides describe
   schema 027 and the durable import queue, but dated observations are not proof of
   today's run state. Keep existing Terraform state, CSEK material, model files,
   manifests, private receipts, and immutable archive/gallery data.
4. Choose work from the roadmap after the live check. Confirm the original profile
   import and pending queue outcomes before starting new acquisition or changing
   capacity. Review the latest private failure export and recorded recovery decisions
   before retrying failures; workspace copies live under `.local/exports/`. Earlier
   failures were explicitly marked for discard, so do not run a blanket retry.

## Current operating model

`data-batch-gcp-scaffold/` owns the console, processing workers, database schema,
and Terraform deployment. `bulk-download/` is the separate acquisition package.
New enrollment selects detected groups automatically and groups eligible subjects
within each source at cosine 0.4. Search-only submissions retain manual selection
by default. The import queue separates acquisition handoff from bounded processing;
the documented pilot uses two batch GPU slots and one interactive reservation.
Check the live configuration before changing these limits. See
[source matching](data-batch-gcp-scaffold/docs/source-merges.md) and
[operations](data-batch-gcp-scaffold/OPERATIONS.md) for the actual controls.

## Working rules

Keep current instructions in the maintained guides and actionable work in the
roadmap. Put completed rollout details in dated history only when they provide
useful recovery or verification evidence. Keep secrets, private diagnostics, and
operator data out of tracked files. Do not infer that an old plan, handoff snapshot,
or failed run is still active without checking its current state.

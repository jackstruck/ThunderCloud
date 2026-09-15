# Operator inputs on the run framework — 2026-09-09

Local implementation, not deployed. Migration 012 adds an explicit archive object
reference (bucket, object, generation, checksum) and manual/all-track selection
policy to ordinary runs. Browser inputs continue through their existing adapters.

`worker.operator_submission` prepares a protected, fsynced receipt before handoff.
It retains the operator, exact input, explicit handling/selection policies and
idempotency key. Submit retries use that same key after a lost acknowledgement;
repeated receipt arguments create a selected bulk batch of ordinary runs. The
shared run transaction inserts the fetch operation. Receipts record acknowledged
run IDs before the optional ingestion invocation. Scheduler reconciliation can
recover queued work after an invocation failure.

The CPU fetch adapter reads only the configured archive scope at the exact source
generation and verifies its checksum/size. It creates a temporary input for the
existing GPU path. If an earlier acquisition uploaded successfully but lost its
handoff, retry reuses the temporary generation only after verifying identical
bytes. It does not delete the original archive object.

All-track selection queues every detected track through the same transactional
selection helper as manual selection. Enrollment receives one new-subject
assignment per track. GPU detection can immediately process that queued matching
step; the durable operation remains authoritative for recovery. Manual inputs
still stop for user selection. Normal interactive enrollment and correction no
longer execute the completed example backfill.

Example preparation (substitute an already verified object reference):

```bash
.test-venv/bin/python -m worker.operator_submission prepare \
  --receipt /protected/submission.json --principal OPERATOR \
  --bucket BUCKET --object-name sources/OBJECT.mp4 --generation GENERATION \
  --sha256 SHA256 --bytes BYTES --content-type video/mp4 \
  --handling-policy enroll_only --selection-policy all_tracks
.test-venv/bin/python -m worker.operator_submission submit \
  --receipt /protected/submission.json
```

Use `manual` for reviewed grouping and the accepted `search_then_discard`,
`enroll_only`, or `retain_and_enroll` handling policy. Repeat `--receipt` for bulk;
`--active-run-limit` explicitly controls the operator batch quota. Failed bulk
handoffs can reuse all original receipts. Runtime credentials use the existing
settings and ADC mechanism; receipts contain no credentials.

## Focused verification

Following the user's request to accelerate implementation, no new image rebuild,
full-suite run, or intermediate production-copy rehearsal was performed here.

- PostgreSQL archive integration passed: commit followed by lost acknowledgement,
  exact run reuse, one fetch operation, CPU-to-GPU handoff, automatic selection,
  two tracks creating two independent subjects, detection replay rejection, and
  no archive work-item insertion.
- Two receipt/storage unit checks passed, including rejection of a changed receipt
  and refusal to reuse different temporary bytes.
- Three existing ingestion-drain checks passed.
- Existing grouped-enrollment/replay (both enrollment policies) and audited move
  checks passed after removing runtime backfill calls (three checks).
- Ruff and `git diff --check` passed.

The reusable focused PostgreSQL container is manifest-owned under
`.local/platform-focused-20260909/resources.json`, capped at 0.5 CPU on localhost
port 55440. It is retained for ongoing consolidation checks; remove its exact ID
at that work's cleanup milestone. Preserve the unrelated source-split container.

The archive writer/queue and deployment have not yet been retired. Final shared
enrollment, schema ownership cleanup, deployment consistency and integrated
validation remain required before production cutover.

# Platform maintenance tooling

Current implementation: ordered migration tracking, reference generation, local
containerized `validate` and `rehearse-migration`, guarded `apply-migration`, and
build/start/status/cleanup orchestration. Durable cloud staging, execution, status,
resume and scoped cleanup are implemented; production apply passed on 2026-09-10.
Local validation and production acceptance remain distinct. See
[cutover results](../docs/platform-cutover-review.md).

Run from the application directory using the development Python environment:

```bash
.test-venv/bin/python -m maintenance.local build \
  --artifacts /workspaces/ThunderCloud/.local/maintenance-build-EXECUTION
```

The build uses an explicit source snapshot, including uncommitted changes, and
excludes credentials, models, database copies, Terraform state and virtualenvs.
`build-manifest.json` records file hashes/modes and a combined fingerprint.
`image-id` and `build-receipt.json` record the immutable local image ID. A local
image ID is not a published registry digest; publication and its registry digest
must be recorded separately when preparing cloud execution.

Start a detached validation, supplying the exact ID printed by the build:

```bash
.test-venv/bin/python -m maintenance.local start \
  --execution-id EXECUTION --image sha256:IMAGE_ID \
  --artifacts /workspaces/ThunderCloud/.local/EXECUTION \
  --retention-deadline YYYY-MM-DD
.test-venv/bin/python -m maintenance.local status \
  --artifacts /workspaces/ThunderCloud/.local/EXECUTION
.test-venv/bin/python -m maintenance.local cleanup \
  --artifacts /workspaces/ThunderCloud/.local/EXECUTION
```

Use a lowercase/digit/hyphen execution ID and a new artifact directory. `run`
instead of `start` waits for the fixed procedure and performs scoped cleanup on
normal completion, including failed verification. A disconnected wrapper leaves
the Docker execution inspectable with the same status/cleanup commands; never
start another execution merely because observation timed out. Cleanup refuses to
interrupt a running validation. Validation reruns use a new empty disposable
PostgreSQL environment and new execution ID; migration-step resume is tested
inside validation on separate execution-owned databases.

Local runs cap PostgreSQL at 0.75 CPU and the runner at 0.25 CPU to preserve
codespace responsiveness. Run validation and rehearsal sequentially.

PostgreSQL has no published ports and no external network. The maintenance
container shares its loopback network, so both tests and reference generation use
localhost. Source artifacts are baked into the image; only the protected evidence
directory is mounted. No Docker socket or credentials are passed into the runner.
The PostgreSQL filesystem is disposable tmpfs, not a production or rehearsal copy.

Every run writes `resources.json` before creating containers. It records exact
names, image IDs and returned container IDs, with execution ownership labels.
`progress.json` is atomically replaced and fsynced during stages and every 15
seconds during long test commands. Terminal evidence is `report.json`, `report.md`,
`tests.xml`, stage logs, `schema-reference.json` and browser screenshots. A zero
container exit without a matching successful terminal report is incomplete.
Missing or failed containers cannot be inferred successful from progress counts.

Cleanup exports container state/logs and artifact checksums to `cleanup.json`
before removing only manifest-owned resources, after verifying their labels and
IDs. It is idempotent. Shared containers/images are preserved. The local image and
evidence are retained with the explicitly supplied deadline and implementation
owner; no broad Docker pruning is used. Cloud resource cleanup and image-retention
execution will be added with the production migration command.

## One ordered migration process

`maintenance.migrations` discovers base schema version 000 and contiguous numbered
SQL files. It takes a PostgreSQL advisory lock scoped to the target database,
rejects changed applied checksums or schema drift, then commits each migration and
its ledger row atomically. A failure rolls back only the uncommitted step and
stops. Restart checks the entire committed prefix before continuing. There are no
nontransactional migration steps in the current sequence.

An untracked existing schema requires an explicit `--adopt-through` and a
`--schema-reference` produced by running the same SQL sequence against an empty
isolated database. The installed schema must match that version's structural
fingerprint before any prior versions are recorded. Columns/defaults, constraints,
indexes, functions, triggers, relations, views, policies, sequences, enums and
extension versions are checked. Physical column positions are excluded; column names and definitions are checked independently of append history. Ownership/ACLs are handled by the shared grants
procedure, not by the structural fingerprint. Data invariants require separate
rehearsal verification.

The connector-based administrative entry point remains
`scripts/apply_db_migrations.py`. It now applies the full ordered sequence; the old
selective `--migration` flags are removed. Both fresh setup and upgrades execute
`scripts/db_grants.sql`. Application principals cannot write provenance evidence
or the migration ledger. Do not adopt production until the reviewed rehearsal,
backup, target/queue preconditions and cutover authorization are ready.


## Protected-copy rehearsal

Use a custom-format PostgreSQL backup and its checksum receipt. Match the target
PostgreSQL and extension versions to the source; a structural mismatch fails
before baseline adoption. Rehearsal restores two isolated copies, verifies logical
data receipts and representation invariants, compares the migrated schema to a
fresh installation, and exercises a committed baseline/reconnect/resume. It runs
no destructive test fixtures against these copies.

```bash
.test-venv/bin/python -m maintenance.local run \
  --procedure rehearse-migration --execution-id EXECUTION \
  --image sha256:IMAGE_ID --postgres-image pgvector/pgvector@sha256:PG_DIGEST \
  --backup /protected/backup.dump --backup-receipt /protected/backup-receipt.json \
  --artifacts /protected/EXECUTION --retention-deadline YYYY-MM-DD
```

The two input files are mounted read-only. Before/after hashes, the reference,
rehearsal receipt and recovery checks are exported to the artifact directory.
Status and cleanup use the same commands as validation.

## Reviewed apply contract

The `apply-migration` CLI takes `--plan`, `--plan-checksum`, `--backup`,
`--backup-receipt`, `--rehearsal-receipt`, `--schema-reference`, and `--before`,
in addition to the execution, image and artifact arguments. The reviewed plan
binds the database name/OID, role inventory, expiration, image/source fingerprint,
migration manifest, grants, backup, reference and successful rehearsal receipts.
It requires application roles to be NOLOGIN, no other database clients and drained
queues. It verifies preservation before and after applying the ordered migrations,
then applies and checks runtime grants. It never pauses or reopens submissions itself.

An explicit `--resume` retains the prior attempt and requires the same execution,
image, migration manifest and reviewed plan. Committed migrations are verified and
skipped. A refused precondition or failed verification stops the procedure. The cloud execution and durable reporting procedure passed production migration
on 2026-09-10. See [cutover results](../docs/platform-cutover-review.md) and the
[cloud launch/status/cleanup instructions](../docs/history/2026-09-09-platform-maintenance.md).

For code-only image refreshes, `build --base-build /protected/PREVIOUS_BUILD`
reuses an image whose source label and dependency fingerprints match its build
receipt. Dependency or Dockerfile changes require a full build. This avoids
recompiling the same dependencies in disk-constrained workspaces.

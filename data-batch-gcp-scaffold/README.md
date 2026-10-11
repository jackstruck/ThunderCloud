# ThunderCloud data processing

ThunderCloud stores encrypted source media and processes uploads, supported links,
and archive submissions through a shared durable run framework. The protected
console provides run management and subject galleries. Current schema: **027**.

## Start here

- [New developer and agent start](../SESSION_HANDOFF.md): first checks and how to
  choose the next task.
- [Operations](OPERATIONS.md): prerequisites, installation, uploads, submission,
  deployment, testing, and recovery. This is the primary operating guide.
- [Architecture](ARCHITECTURE.md): components, ownership, data flow, and retention.
- [Roadmap](ROADMAP.md): current follow-up work and completed capabilities.
- [API contract](openapi/phase1.yaml): console endpoints and policies.
- [Naming compatibility](docs/naming-compatibility.md): directory and command names,
  plus retained database, API, and deployment identifiers.
- [History](docs/history/README.md): dated rollout and cutover evidence.

Run local commands from `data-batch-gcp-scaffold/`. Follow
[installation](OPERATIONS.md#install) to configure a development environment;
[prerequisites](OPERATIONS.md#prerequisites) lists required tools and credentials.

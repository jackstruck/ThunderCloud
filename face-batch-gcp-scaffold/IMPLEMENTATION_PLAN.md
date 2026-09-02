# Face-video system implementation plan

`ARCHITECTURE.md` is the canonical current-state description. This plan records the
implemented milestones and the remaining work without preserving superseded designs.

## Goal

Operate three connected workflows:

1. acquire selected videos locally and upload immutable CSEK-encrypted source objects;
2. process selected sources with a managed Cloud Run GPU Job and maintain a Cloud SQL
   subject gallery with complete source provenance; and
3. search that gallery from a local, ephemeral, read-only image/video probe tool.

## Implemented milestones

### Infrastructure and security

- Existing GCS archive adopted with Public Access Prevention, uniform bucket access,
  `prevent_destroy`, and a lifecycle rule confined to `face-staging/`.
- Cloud SQL PostgreSQL 17 with pgvector, CMEK, IAM authentication, public connector
  access for local development, and private VPC access for Cloud Run.
- Artifact Registry, Direct VPC egress, keyless runtime identity, Secret Manager CSEK
  delivery, audit logging, and Cloud Monitoring policies.
- No service-account JSON keys and no CSEK material in Terraform state or arguments.

### Acquisition and ingestion

- Local downloader and append-only manifest in `bulk-download/`.
- CSEK upload of immutable source objects beneath `videos/`.
- Exact-object and manifest-selection ingestion with SHA-256 and generation provenance.
- Durable Cloud SQL rollout/work-item queue and idempotent enqueueing.

### Worker and gallery

- Generation-pinned CSEK staging beneath `face-staging/`.
- PyAV sampling, SCRFD detection, ByteTrack tracking, heuristic quality scoring,
  AdaFace embedding, and best-N track aggregation.
- Version-compatible pgvector matching and atomic source/job/track/subject commits.
- Exact staging deletion only after a successful or previously successful commit.
- Local CPU execution and validated Cloud Run L4 execution from an immutable image.
- Lease renewal, retry classification, dead-letter state, reconciliation, and
  monitoring for unattended Cloud Run drains.

### Ephemeral local probing

- `face-probe submit` for JPEG, PNG, MP4, and pre-detected crop directories.
- Shared SCRFD/AdaFace and video tracking/aggregation behavior.
- Explicitly read-only, model-version-filtered gallery transaction.
- Deterministic top-K subjects, similarities, nullable display names, and complete live
  succeeded-track provenance.
- No threshold-based identity assertion and no gallery mutation.
- No server-side probe retention; private local JSON and review crops remain under the
  developer's control until locally deleted.

## Verification completed

- Unit and disposable PostgreSQL integration suites pass.
- The integration suite proves pgvector ranking and that probe transactions reject
  attempted gallery mutation.
- Terraform validates and reports zero drift.
- A Cloud Run L4 canary and controlled ten-video drain completed and reconciled.
- The retained `track-000001` crop set and a temporary MP4 both ranked the expected
  enrolled subject first through the real local models and Cloud SQL gallery.

## Remaining work

1. Reconcile and verify the final state of the documented 1,859-video production
   Cloud Run rollout.
2. Calibrate the training clustering threshold against representative same-person and
   different-person examples and record false-accept/false-reject targets.
3. Implement authorized subject review, split, merge, and identity-assignment tools.
4. Complete performance, cost, interruption, retry, and safe-parallelism measurement.
5. Approve retention and cleanup policies for embeddings, job metadata, backups, and
   local review artifacts.
6. Disable Cloud SQL public IP when a private-only administration path is available.
7. Replace deprecated ByteTrack construction before the installed supervision release
   removes it.

## Explicitly deferred

- A public or shared probe API.
- Durable or server-side probe media or results.
- Automatic probe identity decisions.
- Automatic enrollment or identity assignment from probe similarity.
- GKE, Pub/Sub, Redis, DeepStream, or a separate vector database without measured need.

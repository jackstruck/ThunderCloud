# Current architecture

This is the canonical description of the running face-video system. Implementation
plans describe how components were built; this document describes what operators and
developers should use now.

## System flow

```text
Local acquisition and manifest tooling
  |
  | download selected source videos
  | calculate SHA-256 and record immutable object metadata
  | upload with the customer-supplied encryption key (CSEK)
  v
GCS archive: gs://teak-banner-dome-bulk-videos/videos/
  |
  | selected records are added to the durable Cloud SQL work queue
  v
Cloud Run GPU Job: face-batch-gpu-drain
  |
  | make a generation-pinned CSEK staging copy under face-staging/
  | SCRFD detection → ByteTrack → quality selection
  | AdaFace embedding → best-N track aggregation → subject matching
  v
Cloud SQL PostgreSQL + pgvector
  | subjects, optional identities, tracks, source provenance, jobs, rollouts
  |
  +<── ephemeral read-only search from the local face-probe command
```

Google Cloud Batch is not part of the active architecture. It was evaluated before
Cloud Run, but reliable GPU reservation and startup capacity were problematic. Cloud
Run GPU Jobs were adopted instead.

## 1. Local acquisition and archive upload

The acquisition workflow lives in the sibling `bulk-download/` project. It resolves
selected video links, downloads content locally, calculates SHA-256, and uploads the
immutable source object beneath the archive's `videos/` prefix using the CSEK. The
append-only manifest records the source URI, digest, byte length, content type, object
generation, source reference, and status.

Source objects are durable inputs. Processing must never overwrite or delete them.

## 2. Cloud Run video processing

`face-ingest` resolves explicitly selected manifest records and writes a rollout plus
work items to Cloud SQL. `face-cloud-run start` launches the Cloud Run GPU Job. A warm
task leases queued work items one at a time and renews each lease while processing.

For each video, the worker:

1. verifies the immutable source generation and CSEK metadata;
2. creates a generation-pinned CSEK copy beneath `face-staging/`;
3. samples frames and runs SCRFD face detection;
4. associates detections with ByteTrack;
5. retains the best-quality observations;
6. creates normalized AdaFace embeddings and a track aggregate;
7. compares the aggregate with compatible existing subjects;
8. atomically commits source, job, track, subject, and provenance data; and
9. deletes only the exact staging generation after a successful commit.

The pretrained SCRFD and AdaFace models are fixed inference models. Processing creates
embeddings and subject clusters; it does not train new neural-network weights.

Cloud Run obtains the CSEK from Secret Manager directly into memory and connects to
Cloud SQL using automatic IAM database authentication over Direct VPC egress. The
durable queue supports retries, lease recovery, reconciliation, and unattended runs.

## 3. Ephemeral local face search

`face-probe` accepts one local JPEG/PNG image, one local MP4, or a directory of
previously detected face crops. Detection, tracking, quality selection, and embedding
run locally. The gallery query runs inside an explicitly read-only Cloud SQL
transaction.

The command returns:

- one result group per detected image face or retained video track;
- deterministic top-K compatible subjects and cosine similarities;
- the nullable operator-managed `identity.display_name`;
- every current succeeded source-video observation for each candidate; and
- private local review crops mapped to their result group.

It does not upload probe data or retain probe media, embeddings, results, request
metadata, or review crops server-side. Private local JSON and review crops remain under
the developer's control until locally deleted. It does not create or modify subjects
or identities and does not make an automatic identity decision. Review directories use
mode `0700`; files use `0600`.

Limits are JPEG/PNG up to 25 MiB and 50 million decoded pixels, or MP4 up to 250 MiB
and 15 minutes.

## Data model

- `source_asset`: immutable source URI, digest, and metadata.
- `processing_job`: one processing attempt and its version/status snapshots.
- `face_track`: track timing, quality, embedding, subject, and decision provenance.
- `subject`: anonymous biometric cluster and canonical embedding.
- `identity`: optional operator-authorized label associated with a subject.
- `processing_rollout` and `processing_work_item`: durable Cloud Run orchestration.

There are intentionally no probe tables.

## Security boundaries

- Source and staging objects use the CSEK; the key is never logged or stored in
  Terraform state, database rows, or command arguments.
- GCS Public Access Prevention and uniform bucket-level access are enabled.
- The staging lifecycle rule applies only to `face-staging/`.
- Cloud SQL uses pgvector, CMEK, IAM authentication, and connector-managed TLS.
- Cloud Run uses a keyless least-privilege runtime identity.
- Local probing relies on developer ADC and read-only transactions.
- Identity assignment, subject merge/split, and automatic probe decisions require
  separate authorized workflows.

## Operational commands

Use `commands.md` for current local, enqueue, start, status, reconciliation, and probe
commands. Use `FUTURE_VIDEOS.md` when adding newly acquired source videos.

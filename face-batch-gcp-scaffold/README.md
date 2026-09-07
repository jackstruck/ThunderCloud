# ThunderCloud face-video processing

ThunderCloud acquires videos locally, stores immutable CSEK-encrypted source objects,
and processes selected sources through a durable Cloud SQL queue and Cloud Run GPU
workers. The IAP-protected console supports link ingestion, JPEG/PNG/MP4 uploads,
recent runs, recovery, cancellation, retry, face selection, and subject galleries.

The console supports `search_then_discard`, `retain_and_enroll`, and `enroll_only`.
Retained enrollment and guarded arbitrary public HTTPS media fetching are enabled in
the current Terraform configuration. Matching returns candidate subjects; identity
assignment requires a separate authorized operator workflow.

Historical processing and gallery backfill are complete by user acceptance. The
cleanup checklist records actual teardown progress independently of that acceptance.
The automatic local-first upload-to-enqueue handoff and its pilot remain pending.

## Start here

- [Architecture](ARCHITECTURE.md): components, data flow, retention, and security.
- [Operations](OPERATIONS.md): setup, local processing/search, cloud queue, maintenance.
- [Future videos](FUTURE_VIDEOS.md): immutable upload and explicit enqueue procedure.
- [Roadmap](ROADMAP.md): remaining implementation and acceptance work.
- [API contract](openapi/phase1.yaml): managed console endpoints and policies.
- [History](docs/history/README.md): original plans, selections, and rollout evidence.
- [Cleanup status](../cleanup_opportunities.md): completed and outstanding cleanup.

Development requires Python 3.12+, FFmpeg, developer application credentials, the
existing protected CSEK file, and licensed SCRFD/AdaFace ONNX checkpoints. From this
directory, create a virtual environment and install `python -m pip install -e '.[dev]'`.
Copy `.env.example` to `.env` and set only non-secret configuration. See Operations
before provisioning or launching work. Never put encryption keys in configuration,
command arguments, logs, images, or Terraform state.

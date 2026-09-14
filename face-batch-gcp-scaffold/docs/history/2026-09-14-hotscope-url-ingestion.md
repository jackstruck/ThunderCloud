# Hotscope URL ingestion release — 2026-09-14

Deployed the individual Hotscope video-page adapter and secure HLS-to-MP4
acquisition path to the existing console service and ingestion job.
The UI label is **HTTPS media or video link**.

- Project/region: `teak-banner-dome` / `us-central1`.
- Final shared image: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:34bc2ff7f78592d70904ae1fd06e808fbd925084189798b3e4bf12a255d66de8`.
- Console revision: `face-console-00034-zsh`; ready with 100% traffic.
- Ingestion job: `face-ingest-drain`; ready on the same final image.
- Verification execution: `face-ingest-drain-f67kf` succeeded on the Hotscope release before the final UI-only image layer.
- Local `terraform/terraform.tfvars` pins the final shared digest.

Validation: 57 focused tests passed. The built container successfully acquired and
decoded the previously tested Hotscope video with a 1 GiB memory/1 CPU limit.
Video and audio packet SHA-256 values matched the local acquisition. MP4 file
hashes differed because the container and development host use different FFmpeg
versions; the encoded streams matched. The final container was checked for the
exact generic UI label. No new source-processing submission was created for the
release smoke test; the existing ingestion drain was executed normally.

Deployment changed only the image references in the live templates. Runtime
settings, service identities, environment, IAP, ingress and traffic policy were
verified preserved. No schema or GPU image changes were needed.

Previous images, for rollback:

- Console: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:d39cdd89882e97f770c42ae7420b519b7123f46e210ee457b6f224fba693813a`.
- Ingestion: `us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/console@sha256:faf5536458ec4309144250a6cb5109a8b5095dc46be7183033d6dc0d49f7c177`.

# Cleanup Assessment for the Settled Cloud Run Shape

## Safe local cleanup now

- Delete ignored `terraform/*.tfplan` files after the production launch review. They
  are reproducible point-in-time plans, not state. They currently use less than 1 MiB.
- Remove `.test-venv/` when local development is finished. It is reproducible from
  `pyproject.toml` and currently uses about 131 MiB.
- Remove Terraform's `.terraform/` provider cache only if roughly 259 MiB matters; run
  `terraform init` before the next plan afterward.
- Delete reviewed face-crop directories under `data/` according to the biometric-data
  retention decision. These are local sensitive artifacts and are not required by the
  Cloud Run worker or Cloud SQL.
- Prune unused Docker test images only when disk pressure returns. Keep the validated
  `thundercloud-face-batch:cloud-run-r4` image until the bulk rollout and rollback
  window are complete. The PostgreSQL/pgvector and Terraform images are reproducible
  but useful for tests and local administration.

Do not delete `terraform.tfstate`, `terraform.tfstate.backup`, `.env`, the model files,
or either CSEK copy. Local Terraform state is currently authoritative, the non-secret
environment and model files support local execution, the local CSEK supports local
processing, and the Secret Manager CSEK supports Cloud Run.

## Retain through the production rollout and rollback window

- Keep the Cloud Batch API, Batch IAM bindings, Batch submission code, and last known
  good Batch image as a rollback path.
- Keep all rollout/work-item rows, including cancelled and superseded canaries, as an
  operational audit trail.
- Keep the controlled and production selection files and their checksums so each
  enqueue can be reproduced and audited.
- Keep prior Artifact Registry digests until the production rollout reconciles and a
  rollback window has been explicitly closed. Never delete the deployed r4 digest
  while the Cloud Run Job references it.
- Keep Cloud SQL's connector-only public IP while local processing and administration
  are requirements. It has no authorized networks and local clients still need the
  public Cloud SQL Connector route.
- Keep the `face-staging/` lifecycle policy. It remains the last-resort cleanup for an
  object left behind by process termination before transactional completion.

## Cleanup after successful bulk reconciliation

Perform these as a separate reviewed Terraform change with a no-destroy check for the
source bucket, Cloud SQL, KMS key, CSEK secret, and Cloud Run resources:

1. Remove Batch-only IAM roles (`roles/batch.agentReporter` and
   `roles/batch.jobsEditor`), Batch-only resources, and eventually
   `batch.googleapis.com` after the rollback window.
2. Remove or archive `scripts/submit_batch.py` and Batch-specific documentation after
   confirming no operator still uses that path.
3. Apply an Artifact Registry retention policy that preserves the deployed digest and
   a chosen number or age of known-good rollback images.
4. Rename legacy Terraform identifiers such as `batch_worker` and `batch_subnet_name`
   only with Terraform `moved` blocks; cosmetic renaming must not recreate identities
   or networking.
5. Reconsider Cloud SQL's public IP only if local access is replaced by a private route
   such as VPN or an approved bastion. Removing it now would break the required local
   workflow.
6. Reassess whether probe matching should receive its own runtime identity when it is
   deployed as a separate service.

## Items that are not cleanup

- Immutable source objects under `videos/` are records of input and must not be
  deleted by this project.
- Cloud SQL biometric results are application data, not disposable orchestration
  residue. Any retention or erasure operation needs a separate data-governance decision.
- The CSEK and database KMS key must remain available for as long as their encrypted
  data or backups must be recoverable.
- Monitoring policies (including completion, execution failure, and dead letter) and
  the operator email channel are part of unattended operation, not temporary migration
  scaffolding.

# Cleanup Assessment

This assessment applies to the current local-acquisition, Cloud Run GPU processing,
Cloud SQL gallery, and local ephemeral-probe architecture described in
`ARCHITECTURE.md`.

## Safe local cleanup

- Delete ignored `terraform/*.tfplan` files after reviewing or applying them.
- Remove `.test-venv/` when it is no longer needed; it is reproducible from
  `pyproject.toml`.
- Remove Terraform's `.terraform/` provider cache when disk space matters, then run
  `terraform init` before the next plan.
- Delete local face-crop and probe-review directories after review. They contain
  sensitive biometric artifacts and are not required by Cloud SQL or Cloud Run.
- Prune unused local Docker images when they are no longer needed for validation.

Do not delete `terraform.tfstate`, `terraform.tfstate.backup`, `.env`, model files, or
either CSEK copy. They support current infrastructure, local execution, or recovery.

## Retain for current operation

- Keep rollout and work-item rows as the processing audit trail.
- Keep selection files and checksums needed to reproduce an enqueue operation.
- Keep the deployed Artifact Registry digest and any deliberately selected known-good
  predecessor until the current rollout is reconciled.
- Keep Cloud SQL's connector-only public-IP configuration while developer-local access
  remains required. It has no authorized networks.
- Keep the `face-staging/` lifecycle policy as last-resort cleanup for interrupted work.
- Keep monitoring for job completion, execution failure, and dead-letter conditions.

## Follow-up cleanup and hardening

1. Add an Artifact Registry retention policy that preserves deployed digests.
2. Rename legacy Terraform identifiers only with Terraform
   `moved` blocks so resources are not recreated.
3. Reconsider Cloud SQL public IP only after an approved private route replaces local
   connector access.
4. Apply explicit retention policies to local review artifacts and durable Cloud SQL
   application data; these are governance decisions, not incidental cleanup.

Immutable source objects under `videos/` are input records and must not be deleted by
this project. The CSEK and database KMS key must remain recoverable for as long as their
encrypted data or backups must be recoverable.

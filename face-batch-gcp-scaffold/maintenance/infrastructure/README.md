# Temporary migration infrastructure

This standalone Terraform configuration owns only the migration service account,
its temporary permissions, and the protected artifact bucket. It does not manage
application deployments, the database, network, encryption key, or archive media.
The Cloud Run job is owned by `maintenance.cloud_launch` and its resource manifest.

Use a private directory outside the repository for state, variables, and saved plans.
The prepared 2026-09-09 review is in
`/workspaces/ThunderCloud/.local/platform-cutover-review-20260909`.
Its plan adds nine resources and makes no changes to application infrastructure.
No resources have been applied as part of preparation.

Before applying, review the named project, execution, developer, existing encryption
key and database-password secret. Check existing key IAM first: a binding that
already belongs to shared infrastructure must not become temporary state owned by
this module. The recorded key policy initially contained only the Cloud SQL service
agent. The migration account receives Cloud SQL connector access and access to the
single admin-password secret; application identities receive neither permission.
The dedicated regional bucket enforces uniform access, Public Access Prevention,
and the existing regional CMEK. It contains no archive or gallery media.

From the application directory:

```bash
terraform -chdir=maintenance/infrastructure init \
  -backend-config=path=/workspaces/ThunderCloud/.local/platform-cutover-review-20260909/maintenance.tfstate
terraform -chdir=maintenance/infrastructure plan \
  -var-file=/workspaces/ThunderCloud/.local/platform-cutover-review-20260909/infrastructure.tfvars.json \
  -out=/workspaces/ThunderCloud/.local/platform-cutover-review-20260909/maintenance.tfplan
```

Applying the reviewed infrastructure plan is part of the authorized cutover
preparation. Preserve the state and plan in protected durable storage before
launching the migration. Configure the launcher with the output service account,
the existing `face-batch-vpc` / `face-batch-batch` network, and the reviewed CMEK.
Stage fixed inputs and reports into separate execution prefixes in the output
bucket using `maintenance.cloud_stage`; no credentials go into uploaded requests.
Set `GOOGLE_CLOUD_PROJECT` for local commands if ADC has no default project.

After the migration job is terminal, export its reports and run
`maintenance.cloud_cleanup`. Then plan and apply `runtime_enabled=false` with the
same state and variable file. This removes the temporary runtime identity, password
access, connector permission, attach permission, runtime bucket binding and Cloud Run
key binding. Retain the bucket, operator access and storage-agent key permission for
the recorded recovery period. Keep them listed in the cleanup receipt with owner,
reason, and deadline.

After the deadline and any explicit recovery hold, use `maintenance.cloud_purge`
to delete only the generations in the exported manifest. Verify the bucket is empty,
then review and apply `terraform destroy` against this module's same private state.
`force_destroy=false` prevents Terraform from silently deleting retained objects.
The shared encryption key, application identities, database, and network remain.

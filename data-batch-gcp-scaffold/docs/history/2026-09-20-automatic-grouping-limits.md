# Automatic enrollment grouping limits — 2026-09-20

Automatic enrollment at the configured 0.4 threshold now plans sources containing up to 1,000 subjects, with a 60-second planning budget. Previously, the bounds were 400 subjects and 10 seconds, leaving large sources enrolled without grouping. The new bound allows up to 499,500 pair comparisons.

`SourceMerges.auto_merge` supplies a separate automatic subject limit to the shared loader. Interactive planning retains its existing 400-subject and 20-second limits. Existing group-size, identity, dismissal, model, and source-membership checks still apply. Planning timeout or overflow retains the existing manual-review outcome. The 60-second budget covers planning; merge application can take longer.

Validation: all 16 source-grouping unit tests passed, including the 1,000/1,001 boundary and simulated 45/61-second planning cases. A focused unit/integration run against isolated PostgreSQL passed all 11 selected tests, including actual grouping beyond the interactive limit, preservation of examples, automatic overflow, and identity/dismissal behavior. The built worker image passed an import/configuration check. The isolated test database was removed afterward.

Deployed `face-interactive-gpu` generation 31 and verified its image by reading back the Cloud Run job configuration:

`us-central1-docker.pkg.dev/teak-banner-dome/face-batch-worker/worker@sha256:1691c00f5a7353e8343888f60d754e4ade3b3ee83bed1bc7a33514972e53918f`

The release layers the changed matching module onto the prior deployed worker image. The local Terraform image pin matches the deployed digest. The new configuration applies to new executions. Existing executions and historical manual-review sources are not rerun by this deployment.

Private build, test, rollback configuration, and deployment evidence: `.local/automatic-grouping-limits/` at the repository root.

locals {
  developer_project_roles = toset([
    "roles/batch.jobsEditor",
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/serviceusage.serviceUsageConsumer",
  ])
}

resource "google_project_iam_member" "developer_project_roles" {
  for_each = local.developer_project_roles

  project = var.project_id
  role    = each.value
  member  = "user:${var.developer_email}"
}

resource "google_service_account" "batch_worker" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-runtime"
  display_name = "Face Batch worker runtime"
  description  = "Keyless runtime identity for remote face-processing Batch jobs."
}

locals {
  batch_worker_project_roles = toset([
    "roles/batch.agentReporter",
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/logging.logWriter",
  ])
}

resource "google_project_iam_member" "batch_worker_project_roles" {
  for_each = local.batch_worker_project_roles

  project = var.project_id
  role    = each.value
  member  = google_service_account.batch_worker.member
}

# Job submitters must be able to attach the selected runtime identity. This does not
# grant the developer permission to mint or download a service-account key.
resource "google_service_account_iam_member" "developer_can_attach_batch_worker" {
  service_account_id = google_service_account.batch_worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "user:${var.developer_email}"
}

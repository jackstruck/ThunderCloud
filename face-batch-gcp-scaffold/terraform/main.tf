data "google_project" "current" {
  project_id = var.project_id
}

locals {
  required_apis = toset([
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudscheduler.googleapis.com",
    "run.googleapis.com",
    "servicenetworking.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
  ])
}

resource "google_project_service" "apis" {
  for_each = local.required_apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

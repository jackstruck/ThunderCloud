terraform {
  required_version = ">= 1.6.0"
  backend "local" {}
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.45"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" { type = string }
variable "region" { type = string }
variable "execution_id" { type = string }
variable "developer_email" { type = string }
variable "encryption_key" { type = string }
variable "admin_password_secret" { type = string }
variable "retention_deadline" { type = string }
variable "runtime_enabled" {
  type    = bool
  default = true
}

data "google_project" "current" { project_id = var.project_id }
data "google_storage_project_service_account" "storage" { project = var.project_id }

resource "google_storage_bucket" "artifacts" {
  name                        = "${var.project_id}-${var.execution_id}"
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels = {
    purpose            = "platform-maintenance"
    execution          = var.execution_id
    retention_deadline = var.retention_deadline
  }
  encryption { default_kms_key_name = var.encryption_key }
  soft_delete_policy { retention_duration_seconds = 0 }
  depends_on = [google_kms_crypto_key_iam_member.storage]
}

# The key is shared existing infrastructure; only these additive bindings are owned.
resource "google_kms_crypto_key_iam_member" "storage" {
  crypto_key_id = var.encryption_key
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.storage.email_address}"
}

resource "google_kms_crypto_key_iam_member" "run" {
  count         = var.runtime_enabled ? 1 : 0
  crypto_key_id = var.encryption_key
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.current.number}@serverless-robot-prod.iam.gserviceaccount.com"
}

resource "google_service_account" "migration" {
  count        = var.runtime_enabled ? 1 : 0
  project      = var.project_id
  account_id   = "face-platform-migration"
  display_name = "Temporary platform migration"
}

resource "google_project_iam_member" "database_connector" {
  count   = var.runtime_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = google_service_account.migration[0].member
}

resource "google_secret_manager_secret_iam_member" "database_password" {
  count     = var.runtime_enabled ? 1 : 0
  project   = var.project_id
  secret_id = var.admin_password_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.migration[0].member
}

resource "google_service_account_iam_member" "attach" {
  count              = var.runtime_enabled ? 1 : 0
  service_account_id = google_service_account.migration[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "user:${var.developer_email}"
}

resource "google_storage_bucket_iam_member" "migration" {
  count  = var.runtime_enabled ? 1 : 0
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectUser"
  member = google_service_account.migration[0].member
}

resource "google_storage_bucket_iam_member" "operator" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "user:${var.developer_email}"
}

output "artifact_bucket" { value = google_storage_bucket.artifacts.name }
output "service_account" { value = try(google_service_account.migration[0].email, null) }

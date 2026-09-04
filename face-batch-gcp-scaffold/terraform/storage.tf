resource "google_storage_bucket" "archive" {
  name                        = var.source_bucket_name
  project                     = var.project_id
  location                    = "US"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = var.labels

  # Staged media is intentionally ephemeral. GCS otherwise defaults new buckets
  # to a soft-delete retention window, which would keep deleted objects recoverable.
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = var.staging_ttl_days
      matches_prefix = [var.staging_prefix]
    }
  }

  dynamic "cors" {
    for_each = var.console_origin == null ? [] : [var.console_origin]
    content {
      origin          = [cors.value]
      method          = ["POST", "PUT", "DELETE", "OPTIONS"]
      response_header = ["Content-Type", "Content-Range", "Range", "X-Upload-Content-Length", "X-Upload-Content-Type"]
      max_age_seconds = 3600
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = 7
      matches_prefix = ["submissions-temporary/"]
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = google_storage_bucket.archive
  id = var.source_bucket_name
}

resource "google_storage_bucket_iam_member" "developer_source_reader" {
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectViewer"
  member = "user:${var.developer_email}"

  condition {
    title       = "Read immutable face-video sources"
    description = "Restrict developer source reads to the archive prefix."
    expression  = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${var.source_prefix}')"
  }
}

resource "google_storage_bucket_iam_member" "developer_staging_user" {
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectUser"
  member = "user:${var.developer_email}"

  condition {
    title       = "Manage ephemeral face-video staging objects"
    description = "Restrict developer object operations to the staging prefix."
    expression  = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${var.staging_prefix}')"
  }
}

resource "google_storage_bucket_iam_member" "batch_worker_staging_user" {
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectUser"
  member = google_service_account.batch_worker.member

  condition {
    title       = "Manage remote face-video staging objects"
    description = "Restrict Batch worker object operations to the ephemeral staging prefix."
    expression  = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${var.staging_prefix}')"
  }
}

resource "google_storage_bucket_iam_member" "runtime_source_reader" {
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.batch_worker.member

  condition {
    title       = "Read immutable sources for remote processing"
    description = "Restrict the Cloud Run and Batch runtime to the immutable video prefix."
    expression  = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${var.source_prefix}')"
  }
}

# Object listing is authorized at bucket scope and cannot be expressed with the
# object-name condition used by runtime_source_reader. This legacy bucket role grants
# list/get-bucket-metadata only; object contents remain prefix-scoped above.
resource "google_storage_bucket_iam_member" "runtime_archive_lister" {
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.legacyBucketReader"
  member = google_service_account.batch_worker.member
}

# Temporary Stage 0 input snapshots and result artifacts. This prefix is deliberately
# separate from immutable videos and processing staging so it can be retired after the
# historical backfill acceptance window.
resource "google_storage_bucket_iam_member" "developer_backfill_audit_user" {
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectUser"
  member = "user:${var.developer_email}"

  condition {
    title       = "Manage historical backfill audit artifacts"
    description = "Restrict developer audit artifact access to the backfill prefix."
    expression  = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/backfill-audit/')"
  }
}

resource "google_storage_bucket_iam_member" "runtime_backfill_audit_user" {
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectUser"
  member = google_service_account.batch_worker.member

  condition {
    title       = "Manage remote historical backfill audit artifacts"
    description = "Restrict runtime audit artifact access to the backfill prefix."
    expression  = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/backfill-audit/')"
  }
}

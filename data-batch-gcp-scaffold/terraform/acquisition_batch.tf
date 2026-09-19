variable "acquisition_batch_image" {
  description = "Pinned image for the checkpointed cloud acquisition job; null disables it."
  type        = string
  default     = null
}

variable "acquisition_batch_state_object" {
  description = "Encrypted checkpoint object for the explicitly selected batch."
  type        = string
  default     = "batch-control/hotscope-20260914/state.json"
}

locals {
  acquisition_batch_enabled = local.phase1_enabled && var.acquisition_batch_image != null
}

resource "google_project_iam_custom_role" "acquisition_archive_uploader" {
  count       = local.acquisition_batch_enabled ? 1 : 0
  project     = var.project_id
  role_id     = "faceBatchArchiveUploader"
  title       = "Immutable batch archive upload"
  description = "Create immutable archive objects and read bucket metadata for acquisition."
  permissions = ["storage.buckets.get", "storage.objects.create"]
}

resource "google_storage_bucket_iam_member" "acquisition_archive_uploader" {
  count  = local.acquisition_batch_enabled ? 1 : 0
  bucket = google_storage_bucket.archive.name
  role   = google_project_iam_custom_role.acquisition_archive_uploader[0].name
  member = google_service_account.console[0].member
  condition {
    title      = "Cloud acquisition immutable uploads"
    expression = "resource.name == 'projects/_/buckets/${var.source_bucket_name}' || resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/videos/')"
  }
}

resource "google_storage_bucket_iam_member" "acquisition_checkpoint" {
  count  = local.acquisition_batch_enabled ? 1 : 0
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectUser"
  member = google_service_account.console[0].member
  condition {
    title      = "Cloud acquisition checkpoint"
    expression = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${dirname(var.acquisition_batch_state_object)}/')"
  }
}

resource "google_cloud_run_v2_job" "acquisition_batch" {
  count               = local.acquisition_batch_enabled ? 1 : 0
  project             = var.project_id
  location            = var.region
  name                = "face-acquisition-batch"
  deletion_protection = false
  labels              = { purpose = "acquisition-batch" }
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.console[0].email
      timeout         = "604800s"
      max_retries     = 3
      containers {
        name    = "worker"
        image   = var.acquisition_batch_image
        command = ["/opt/venv/bin/python"]
        args    = ["-u", "-m", "worker.acquisition_batch"]
        resources { limits = { cpu = "1", memory = "2Gi" } }
        dynamic "env" {
          for_each = merge(local.processing_capacity_env, {
            FACE_PROJECT_ID            = var.project_id
            FACE_BUCKET                = var.source_bucket_name
            FACE_CSEK_SECRET           = google_secret_manager_secret.gcs_csek.secret_id
            FACE_SOURCE_PREFIX         = var.source_prefix
            FACE_CLOUD_SQL_INSTANCE    = google_sql_database_instance.postgres.connection_name
            FACE_CLOUD_SQL_IP_TYPE     = "PRIVATE"
            FACE_DB_USER               = google_sql_user.console_iam[0].name
            FACE_DB_NAME               = google_sql_database.app.name
            FACE_REGION                = var.region
            FACE_INGEST_CONCURRENCY    = "1"
            FACE_INTERACTIVE_JOB       = "face-interactive-gpu"
            FACE_BATCH_STATE_OBJECT    = var.acquisition_batch_state_object
            FACE_BATCH_MAX_VIDEO_BYTES = "262144000"
          })
          content {
            name  = env.key
            value = env.value
          }
        }
      }
      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.main.name
          subnetwork = google_compute_subnetwork.batch.name
        }
      }
    }
  }
  depends_on = [google_storage_bucket_iam_member.acquisition_archive_uploader, google_storage_bucket_iam_member.acquisition_checkpoint]
}

variable "source_merge_backfill_image" {
  description = "Pinned CPU image for explicitly scoped historical source matching; null disables the job."
  type        = string
  default     = null
}

resource "google_cloud_run_v2_job" "source_merge_backfill" {
  count               = local.phase1_enabled && var.source_merge_backfill_image != null ? 1 : 0
  project             = var.project_id
  location            = var.region
  name                = "face-source-merge-backfill"
  deletion_protection = false
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.console[0].email
      timeout         = "86400s"
      max_retries     = 2
      containers {
        name    = "worker"
        image   = var.source_merge_backfill_image
        command = ["python", "-m", "worker.source_merge_backfill"]
        resources { limits = { cpu = "1", memory = "1Gi" } }
        dynamic "env" {
          for_each = {
            FACE_PROJECT_ID         = var.project_id
            FACE_BUCKET             = var.source_bucket_name
            FACE_CSEK_SECRET        = google_secret_manager_secret.gcs_csek.secret_id
            FACE_CLOUD_SQL_INSTANCE = google_sql_database_instance.postgres.connection_name
            FACE_CLOUD_SQL_IP_TYPE  = "PRIVATE"
            FACE_DB_USER            = google_sql_user.console_iam[0].name
            FACE_DB_NAME            = google_sql_database.app.name
          }
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
}

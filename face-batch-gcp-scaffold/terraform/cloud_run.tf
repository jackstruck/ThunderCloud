resource "google_cloud_run_v2_job" "gpu_drain" {
  name                = "${var.name_prefix}-gpu-drain"
  project             = var.project_id
  location            = var.region
  labels              = var.labels
  deletion_protection = true

  template {
    task_count  = var.cloud_run_task_count
    parallelism = 1

    template {
      service_account               = google_service_account.batch_worker.email
      timeout                       = "3600s"
      max_retries                   = 2
      execution_environment         = "EXECUTION_ENVIRONMENT_GEN2"
      gpu_zonal_redundancy_disabled = true

      node_selector {
        accelerator = "nvidia-l4"
      }

      containers {
        name    = "worker"
        image   = var.cloud_run_worker_image
        command = ["python", "-m", "worker.cloud_run_drain"]

        resources {
          limits = {
            cpu              = "4"
            memory           = "16Gi"
            "nvidia.com/gpu" = "1"
          }
        }

        env {
          name  = "FACE_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "FACE_BUCKET"
          value = var.source_bucket_name
        }
        env {
          name  = "FACE_SOURCE_PREFIX"
          value = var.source_prefix
        }
        env {
          name  = "FACE_STAGING_PREFIX"
          value = var.staging_prefix
        }
        env {
          name  = "FACE_CSEK_SECRET"
          value = google_secret_manager_secret.gcs_csek.secret_id
        }
        env {
          name  = "FACE_CLOUD_SQL_INSTANCE"
          value = google_sql_database_instance.postgres.connection_name
        }
        env {
          name  = "FACE_CLOUD_SQL_IP_TYPE"
          value = "PRIVATE"
        }
        env {
          name  = "FACE_DB_USER"
          value = google_sql_user.batch_worker_iam.name
        }
        env {
          name  = "FACE_DB_NAME"
          value = google_sql_database.app.name
        }
        env {
          name  = "FACE_MATCHING_ENABLED"
          value = "true"
        }
        env {
          name  = "FACE_MATCH_THRESHOLD"
          value = tostring(var.match_threshold)
        }
        env {
          name  = "FACE_THRESHOLD_VERSION"
          value = var.threshold_version
        }
        env {
          name  = "FACE_WORKER_VERSION"
          value = "0.2.0-cloud-run-r4"
        }
        env {
          name  = "FACE_REQUIRE_CUDA"
          value = "true"
        }
        env {
          name  = "FACE_SOFT_DEADLINE_SECONDS"
          value = "3120"
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

  depends_on = [
    google_project_service.apis["run.googleapis.com"],
    google_project_iam_member.batch_worker_project_roles,
    google_secret_manager_secret_iam_member.batch_worker_csek_accessor,
    google_storage_bucket_iam_member.runtime_archive_lister,
    google_storage_bucket_iam_member.runtime_source_reader,
    google_storage_bucket_iam_member.batch_worker_staging_user,
  ]
}

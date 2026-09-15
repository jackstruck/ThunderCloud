resource "google_secret_manager_secret" "gcs_csek" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-gcs-csek"
  labels    = var.labels

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_iam_member" "batch_worker_csek_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.gcs_csek.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.batch_worker.member
}

resource "google_secret_manager_secret" "postgres_admin_password" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-postgres-admin-password"
  labels    = var.labels

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

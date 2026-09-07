resource "google_artifact_registry_repository" "worker" {
  project       = var.project_id
  location      = var.region
  repository_id = "${var.name_prefix}-worker"
  description   = "Container images for permanent face-processing and console services"
  format        = "DOCKER"
  labels        = var.labels

  cleanup_policy_dry_run = false

  # Pin protection follows Terraform's permanent deployment image variables.
  cleanup_policies {
    id     = "keep-deployed-and-rollback"
    action = "KEEP"
    condition {
      tag_state = "ANY"
      # Artifact Registry limits each version prefix to 64 characters.
      version_name_prefixes = [for version in distinct(concat(
        [for image in compact([var.cloud_run_worker_image, var.interactive_gpu_image, var.console_image]) : split("@", image)[1]],
        var.rollback_image_versions,
      )) : substr(version, 0, 64)]
    }
  }

  cleanup_policies {
    id     = "keep-five-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  cleanup_policies {
    id     = "delete-unreferenced-history"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "2592000s"
    }
  }

  depends_on = [google_project_service.apis["artifactregistry.googleapis.com"]]
}

resource "google_artifact_registry_repository_iam_member" "developer_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.worker.location
  repository = google_artifact_registry_repository.worker.name
  role       = "roles/artifactregistry.writer"
  member     = "user:${var.developer_email}"
}

resource "google_artifact_registry_repository_iam_member" "batch_worker_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.worker.location
  repository = google_artifact_registry_repository.worker.name
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.batch_worker.member
}

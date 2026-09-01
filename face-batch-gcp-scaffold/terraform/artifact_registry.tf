resource "google_artifact_registry_repository" "worker" {
  project       = var.project_id
  location      = var.region
  repository_id = "${var.name_prefix}-worker"
  description   = "Container images for historical video Batch workers"
  format        = "DOCKER"
  labels        = var.labels

  depends_on = [google_project_service.apis["artifactregistry.googleapis.com"]]
}

resource "google_artifact_registry_repository_iam_member" "developer_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.worker.location
  repository = google_artifact_registry_repository.worker.name
  role       = "roles/artifactregistry.writer"
  member     = "user:${var.developer_email}"
}

resource "google_logging_project_bucket_config" "default_retention" {
  # The Logging API and provider import normalize this field to the full project
  # resource name. Match that representation so adopting `_Default` is in-place.
  project        = "projects/${var.project_id}"
  location       = "global"
  retention_days = 30
  bucket_id      = "_Default"
}

import {
  to = google_logging_project_bucket_config.default_retention
  id = "projects/${var.project_id}/locations/global/buckets/_Default"
}

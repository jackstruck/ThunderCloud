resource "google_monitoring_notification_channel" "operator_email" {
  project      = var.project_id
  display_name = "Face batch operator email"
  type         = "email"
  enabled      = true
  labels = {
    email_address = var.monitoring_email_address
  }
  user_labels = var.labels

  depends_on = [google_project_service.apis["monitoring.googleapis.com"]]
}

locals {
  face_batch_notification_channels = concat(
    var.monitoring_notification_channels,
    [google_monitoring_notification_channel.operator_email.name],
  )
}

resource "google_monitoring_alert_policy" "cloud_run_execution_error" {
  project               = var.project_id
  display_name          = "Face processing Cloud Run execution error"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.face_batch_notification_channels
  user_labels           = var.labels

  conditions {
    display_name = "Cloud Run GPU job emitted an error"
    condition_matched_log {
      filter = <<-EOT
        resource.type="cloud_run_job"
        resource.labels.job_name=("face-interactive-gpu" OR "face-ingest-drain")
        severity>=ERROR
      EOT
    }
  }

  alert_strategy {
    auto_close = "86400s"
    notification_rate_limit {
      period = "300s"
    }
  }

  depends_on = [google_project_service.apis["monitoring.googleapis.com"]]
}

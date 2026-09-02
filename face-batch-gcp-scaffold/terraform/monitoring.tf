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
  display_name          = "Face batch Cloud Run execution error"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.face_batch_notification_channels
  user_labels           = var.labels

  conditions {
    display_name = "Cloud Run GPU job emitted an error"
    condition_matched_log {
      filter = <<-EOT
        resource.type="cloud_run_job"
        resource.labels.job_name="${google_cloud_run_v2_job.gpu_drain.name}"
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

resource "google_monitoring_alert_policy" "cloud_run_dead_letter" {
  project               = var.project_id
  display_name          = "Face batch work item dead-lettered"
  combiner              = "OR"
  severity              = "CRITICAL"
  notification_channels = local.face_batch_notification_channels
  user_labels           = var.labels

  conditions {
    display_name = "Queue worker exhausted retries"
    condition_matched_log {
      filter = <<-EOT
        resource.type="cloud_run_job"
        resource.labels.job_name="${google_cloud_run_v2_job.gpu_drain.name}"
        jsonPayload.event="work_item_dead_letter"
      EOT
    }
  }

  alert_strategy {
    auto_close = "604800s"
    notification_rate_limit {
      period = "300s"
    }
  }

  depends_on = [google_project_service.apis["monitoring.googleapis.com"]]
}

resource "google_monitoring_alert_policy" "cloud_run_rollout_succeeded" {
  project               = var.project_id
  display_name          = "Face batch Cloud Run rollout succeeded"
  combiner              = "OR"
  notification_channels = local.face_batch_notification_channels
  user_labels           = var.labels

  conditions {
    display_name = "Queue worker completed the rollout"
    condition_matched_log {
      filter = <<-EOT
        resource.type="cloud_run_job"
        resource.labels.job_name="${google_cloud_run_v2_job.gpu_drain.name}"
        jsonPayload.event="drain_finished"
        jsonPayload.status="succeeded"
      EOT
    }
  }

  documentation {
    content   = "The durable face-batch queue reached succeeded. Run the documented face-cloud-run reconciliation command before declaring the rollout complete."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "86400s"
    notification_rate_limit {
      period = "300s"
    }
  }

  depends_on = [google_project_service.apis["monitoring.googleapis.com"]]
}

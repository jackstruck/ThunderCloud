locals {
  phase1_enabled              = var.enable_phase1_console && var.console_image != null && var.console_origin != null && var.approved_iap_member != null
  temporary_submission_prefix = "submissions-temporary/"
  gallery_prefix              = "subject-gallery/"
  training_media_prefix       = "training-media/"
}

resource "google_service_account" "console" {
  count        = local.phase1_enabled ? 1 : 0
  project      = var.project_id
  account_id   = "${var.name_prefix}-console"
  display_name = "Face console and ingestion runtime"
  description  = "Keyless Phase 1 API and CPU ingestion identity."
}

resource "google_sql_user" "console_iam" {
  count    = local.phase1_enabled ? 1 : 0
  project  = var.project_id
  instance = google_sql_database_instance.postgres.name
  name     = trimsuffix(google_service_account.console[0].email, ".gserviceaccount.com")
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

locals {
  console_project_roles = toset([
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/logging.logWriter",
  ])
}

resource "google_project_iam_member" "console_roles" {
  for_each = local.phase1_enabled ? local.console_project_roles : toset([])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.console[0].member
}

# The console and GPU worker launch Cloud Run jobs with per-run environment
# overrides. roles/run.invoker omits run.jobs.runWithOverrides, while the
# predefined developer role grants substantially more access than either
# runtime needs.
resource "google_project_iam_custom_role" "phase1_job_invoker" {
  count       = local.phase1_enabled ? 1 : 0
  project     = var.project_id
  role_id     = "facePhase1JobInvoker"
  title       = "Face Phase 1 Job Invoker"
  description = "Launch Face Phase 1 Cloud Run jobs, including scoped runtime overrides."
  permissions = [
    "run.jobs.run",
    "run.jobs.runWithOverrides",
  ]
}

resource "google_project_iam_member" "console_job_invoker" {
  count   = local.phase1_enabled ? 1 : 0
  project = var.project_id
  role    = google_project_iam_custom_role.phase1_job_invoker[0].id
  member  = google_service_account.console[0].member
}

resource "google_project_iam_member" "batch_worker_job_invoker" {
  count   = local.phase1_enabled ? 1 : 0
  project = var.project_id
  role    = google_project_iam_custom_role.phase1_job_invoker[0].id
  member  = google_service_account.batch_worker.member
}

resource "google_secret_manager_secret_iam_member" "console_csek_accessor" {
  count     = local.phase1_enabled ? 1 : 0
  project   = var.project_id
  secret_id = google_secret_manager_secret.gcs_csek.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.console[0].member
}

resource "google_storage_bucket_iam_member" "console_temporary_user" {
  count  = local.phase1_enabled ? 1 : 0
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectUser"
  member = google_service_account.console[0].member
  condition {
    title      = "Manage Phase 1 temporary submissions"
    expression = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${local.temporary_submission_prefix}')"
  }
}

resource "google_storage_bucket_iam_member" "console_source_reader" {
  count  = local.phase1_enabled ? 1 : 0
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.console[0].member
  condition {
    title       = "Read operator-submitted archive sources"
    description = "Allow CPU acquisition to read exact objects within the archive prefix."
    expression  = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${var.source_prefix}')"
  }
}

resource "google_storage_bucket_iam_member" "console_gallery_viewer" {
  count  = local.phase1_enabled ? 1 : 0
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.console[0].member
  condition {
    title      = "Read representative gallery crops"
    expression = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${local.gallery_prefix}')"
  }
}

resource "google_project_iam_custom_role" "gallery_cleanup" {
  count       = local.phase1_enabled ? 1 : 0
  project     = var.project_id
  role_id     = "faceGalleryCleanup"
  title       = "Face gallery generation cleanup"
  description = "Delete retired gallery object generations."
  permissions = ["storage.objects.delete"]
}

resource "google_storage_bucket_iam_member" "console_gallery_cleanup" {
  count  = local.phase1_enabled ? 1 : 0
  bucket = google_storage_bucket.archive.name
  role   = google_project_iam_custom_role.gallery_cleanup[0].name
  member = google_service_account.console[0].member
  condition {
    title      = "Delete retired gallery generations"
    expression = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${local.gallery_prefix}')"
  }
}

resource "google_storage_bucket_iam_member" "interactive_temporary_user" {
  count  = local.phase1_enabled ? 1 : 0
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectUser"
  member = google_service_account.batch_worker.member
  condition {
    title      = "Process Phase 1 temporary submissions"
    expression = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${local.temporary_submission_prefix}')"
  }
}

resource "google_storage_bucket_iam_member" "interactive_gallery_user" {
  count  = local.phase1_enabled ? 1 : 0
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectUser"
  member = google_service_account.batch_worker.member
  condition {
    title      = "Manage representative gallery crops"
    expression = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${local.gallery_prefix}')"
  }
}

resource "google_storage_bucket_iam_member" "interactive_training_user" {
  count  = local.phase1_enabled && var.enable_retained_enrollment ? 1 : 0
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectUser"
  member = google_service_account.batch_worker.member
  condition {
    title      = "Manage retained training submissions"
    expression = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${local.training_media_prefix}')"
  }
}

resource "google_storage_bucket_iam_member" "console_training_user" {
  count  = local.phase1_enabled && var.enable_retained_enrollment ? 1 : 0
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectUser"
  member = google_service_account.console[0].member
  condition {
    title      = "Delete tombstoned training submissions"
    expression = "resource.name.startsWith('projects/_/buckets/${var.source_bucket_name}/objects/${local.training_media_prefix}')"
  }
}

resource "google_cloud_run_v2_service" "console" {
  count               = local.phase1_enabled ? 1 : 0
  name                = "face-console"
  project             = var.project_id
  location            = var.region
  labels              = var.labels
  ingress             = "INGRESS_TRAFFIC_ALL"
  iap_enabled         = true
  deletion_protection = true

  template {
    service_account                  = google_service_account.console[0].email
    timeout                          = "30s"
    max_instance_request_concurrency = 20
    scaling {
      max_instance_count = 3
    }
    containers {
      image   = var.console_image
      command = ["gunicorn"]
      args    = ["--bind=0.0.0.0:8080", "--workers=2", "--access-logfile=-", "worker.console:create_app()"]
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
      env {
        name  = "FACE_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "FACE_REGION"
        value = var.region
      }
      env {
        name  = "FACE_INGEST_JOB"
        value = google_cloud_run_v2_job.ingest_drain[0].name
      }
      env {
        name  = "FACE_INTERACTIVE_JOB"
        value = google_cloud_run_v2_job.interactive_gpu[0].name
      }
      env {
        name  = "FACE_CONSOLE_ORIGIN"
        value = var.console_origin
      }
      env {
        name  = "FACE_BUCKET"
        value = var.source_bucket_name
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
        name  = "FACE_DB_USER"
        value = google_sql_user.console_iam[0].name
      }
      env {
        name  = "FACE_DB_NAME"
        value = google_sql_database.app.name
      }
      env {
        name  = "FACE_RESULT_TTL_DAYS"
        value = "7"
      }
      env {
        name  = "FACE_ACTIVE_RUN_LIMIT"
        value = "3"
      }
      env {
        name  = "FACE_ALLOW_ARBITRARY_HOSTS"
        value = tostring(var.enable_arbitrary_host_fetch)
      }
      env {
        name  = "FACE_SUBJECT_MANAGEMENT_ENABLED"
        value = tostring(var.subject_management_enabled)
      }
      env {
        name  = "FACE_RETAINED_ENROLLMENT_ENABLED"
        value = tostring(var.enable_retained_enrollment)
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

  depends_on = [
    google_project_iam_member.console_roles,
    google_secret_manager_secret_iam_member.console_csek_accessor,
    google_storage_bucket_iam_member.console_temporary_user,
    google_storage_bucket_iam_member.console_source_reader,
    google_storage_bucket_iam_member.console_gallery_viewer,
    google_storage_bucket_iam_member.console_training_user,
    google_storage_bucket_iam_member.console_gallery_cleanup,
  ]
}

resource "google_project_service_identity" "iap" {
  count    = local.phase1_enabled ? 1 : 0
  provider = google-beta
  project  = var.project_id
  service  = "iap.googleapis.com"
}

resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  count    = local.phase1_enabled ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.console[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_project_service_identity.iap[0].email}"
}

resource "google_iap_web_cloud_run_service_iam_binding" "approved_team" {
  count                  = local.phase1_enabled ? 1 : 0
  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.console[0].name
  role                   = "roles/iap.httpsResourceAccessor"
  members                = [var.approved_iap_member]
}

resource "google_cloud_run_v2_job" "ingest_drain" {
  count               = local.phase1_enabled ? 1 : 0
  name                = "face-ingest-drain"
  project             = var.project_id
  location            = var.region
  labels              = var.labels
  deletion_protection = true
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.console[0].email
      timeout         = "900s"
      max_retries     = 2
      containers {
        name    = "worker"
        image   = var.console_image
        command = ["python", "-m", "worker.ingest_drain"]
        resources {
          limits = { cpu = "1", memory = "1Gi" }
        }
        env {
          name  = "FACE_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "FACE_REGION"
          value = var.region
        }
        env {
          name  = "FACE_INTERACTIVE_JOB"
          value = "face-interactive-gpu"
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
          value = google_sql_user.console_iam[0].name
        }
        env {
          name  = "FACE_DB_NAME"
          value = google_sql_database.app.name
        }
        env {
          name  = "FACE_INGEST_CONCURRENCY"
          value = "1"
        }
      }
      vpc_access {
        # Keep Cloud SQL private while fetching public media without Cloud NAT.
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.main.name
          subnetwork = google_compute_subnetwork.batch.name
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.console_roles,
    google_secret_manager_secret_iam_member.console_csek_accessor,
    google_storage_bucket_iam_member.console_temporary_user,
    google_storage_bucket_iam_member.console_source_reader,
    google_storage_bucket_iam_member.console_training_user,
    google_storage_bucket_iam_member.console_gallery_cleanup,
  ]
}

resource "google_cloud_run_v2_job" "interactive_gpu" {
  count               = local.phase1_enabled ? 1 : 0
  name                = "face-interactive-gpu"
  project             = var.project_id
  location            = var.region
  labels              = var.labels
  deletion_protection = true

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account               = google_service_account.batch_worker.email
      timeout                       = "3600s"
      max_retries                   = 1
      execution_environment         = "EXECUTION_ENVIRONMENT_GEN2"
      gpu_zonal_redundancy_disabled = true

      node_selector {
        accelerator = "nvidia-l4"
      }

      containers {
        name    = "worker"
        image   = coalesce(var.interactive_gpu_image, var.cloud_run_worker_image)
        command = ["python", "-m", "worker.interactive"]

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
          name  = "FACE_REGION"
          value = var.region
        }
        env {
          name  = "FACE_INGEST_JOB"
          value = "face-ingest-drain"
        }
        env {
          name  = "FACE_BUCKET"
          value = var.source_bucket_name
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
          name  = "FACE_GALLERY_REPAIR_MIN_SIMILARITY"
          value = tostring(var.gallery_repair_min_similarity)
        }
        env {
          name  = "FACE_REQUIRE_CUDA"
          value = "true"
        }
        env {
          name  = "FACE_TOP_K"
          value = "10"
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
    google_project_iam_member.batch_worker_project_roles,
    google_secret_manager_secret_iam_member.batch_worker_csek_accessor,
    google_storage_bucket_iam_member.runtime_source_reader,
    google_storage_bucket_iam_member.interactive_temporary_user,
    google_storage_bucket_iam_member.interactive_gallery_user,
    google_storage_bucket_iam_member.interactive_training_user,
  ]
}

resource "google_cloud_scheduler_job" "ingest_reconciliation" {
  count       = local.phase1_enabled ? 1 : 0
  project     = var.project_id
  region      = var.region
  name        = "face-ingest-reconciliation"
  description = "Recover durable Phase 1 work left queued after an invocation failure."
  schedule    = "*/10 * * * *"
  time_zone   = "Etc/UTC"
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.ingest_drain[0].name}:run"
    oauth_token {
      service_account_email = google_service_account.console[0].email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

resource "google_cloud_scheduler_job" "phase1_maintenance" {
  count       = local.phase1_enabled ? 1 : 0
  project     = var.project_id
  region      = var.region
  name        = "face-phase1-maintenance"
  description = "Expire Phase 1 runs and retry generation-exact temporary cleanup."
  schedule    = "17 3 * * *"
  time_zone   = "Etc/UTC"
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.ingest_drain[0].name}:run"
    body = base64encode(jsonencode({
      overrides = {
        containerOverrides = [{
          name = "worker"
          env  = [{ name = "FACE_INGEST_MODE", value = "maintenance" }]
        }]
        taskCount = 1
      }
    }))
    headers = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = google_service_account.console[0].email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}

# External OAuth audience configuration cannot be inferred here. The plan requires
# inspecting the actual organization and proving an intended external account before
# launch even though direct Cloud Run IAP and its group binding are managed above.

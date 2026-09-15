resource "google_sql_database_instance" "postgres" {
  provider = google-beta

  name             = "${var.name_prefix}-pg"
  project          = var.project_id
  region           = var.region
  database_version = "POSTGRES_17"

  encryption_key_name = google_kms_crypto_key.database.id
  deletion_protection = var.db_deletion_protection

  settings {
    tier = var.db_tier
    # PostgreSQL 16+ defaults to Enterprise Plus, which rejects shared-core
    # development tiers such as db-f1-micro unless Enterprise is explicit.
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = var.db_disk_size_gb
    disk_autoresize   = true
    user_labels       = var.labels

    ip_configuration {
      ipv4_enabled    = true
      private_network = google_compute_network.main.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "04:00"
    }
  }

  depends_on = [
    google_service_networking_connection.private_vpc,
    google_kms_crypto_key_iam_member.cloud_sql_database,
  ]
}

resource "google_sql_database" "app" {
  name     = "face_index"
  project  = var.project_id
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "developer_iam" {
  project  = var.project_id
  instance = google_sql_database_instance.postgres.name
  name     = var.developer_email
  type     = "CLOUD_IAM_USER"
}

resource "google_sql_user" "batch_worker_iam" {
  project  = var.project_id
  instance = google_sql_database_instance.postgres.name
  # Cloud SQL PostgreSQL omits this suffix because database usernames are limited.
  name = trimsuffix(
    google_service_account.batch_worker.email,
    ".gserviceaccount.com",
  )
  type = "CLOUD_IAM_SERVICE_ACCOUNT"
}

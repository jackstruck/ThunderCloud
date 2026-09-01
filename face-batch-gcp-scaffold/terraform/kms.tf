resource "google_kms_key_ring" "main" {
  name     = "${var.name_prefix}-kr"
  location = var.region
  project  = var.project_id

  depends_on = [google_project_service.apis["cloudkms.googleapis.com"]]
}

resource "google_kms_crypto_key" "database" {
  name            = "cloudsql-data"
  key_ring        = google_kms_key_ring.main.id
  rotation_period = "7776000s" # 90 days

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_project_service_identity" "cloud_sql" {
  provider = google-beta
  project  = var.project_id
  service  = "sqladmin.googleapis.com"

  depends_on = [google_project_service.apis["sqladmin.googleapis.com"]]
}

resource "google_kms_crypto_key_iam_member" "cloud_sql_database" {
  crypto_key_id = google_kms_crypto_key.database.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.cloud_sql.email}"
}

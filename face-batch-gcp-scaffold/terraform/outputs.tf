output "staging_bucket_name" {
  value = google_storage_bucket.archive.name
}

output "source_prefix" {
  value = var.source_prefix
}

output "staging_prefix" {
  value = var.staging_prefix
}

output "database_kms_key" {
  value = google_kms_crypto_key.database.id
}

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.worker.name
}

output "artifact_registry_image_prefix" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.worker.repository_id}"
}

output "vpc_name" {
  value = google_compute_network.main.name
}

output "batch_subnet_name" {
  value = google_compute_subnetwork.batch.name
}

output "batch_subnet_self_link" {
  value = google_compute_subnetwork.batch.self_link
}

output "cloud_sql_instance_name" {
  value = google_sql_database_instance.postgres.name
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.postgres.connection_name
}

output "cloud_sql_private_ip" {
  value = google_sql_database_instance.postgres.private_ip_address
}

output "cloud_sql_public_ip" {
  value = google_sql_database_instance.postgres.public_ip_address
}

output "cloud_sql_database" {
  value = google_sql_database.app.name
}

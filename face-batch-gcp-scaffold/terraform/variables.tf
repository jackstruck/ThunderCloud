variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Primary region for KMS, GCS, Artifact Registry, Batch, VPC subnet, and Cloud SQL."
  type        = string
  default     = "us-central1"
}

variable "developer_email" {
  description = "Developer Google account used for local ingestion, image pushes, and Cloud SQL IAM authentication."
  type        = string

  validation {
    condition     = can(regex("^[^@]+@[^@]+$", var.developer_email))
    error_message = "developer_email must be an email address."
  }
}

variable "name_prefix" {
  description = "Prefix used for resource names."
  type        = string
  default     = "face-batch"
}

variable "source_bucket_name" {
  description = "Existing CSEK-encrypted archive bucket."
  type        = string
  default     = "teak-banner-dome-bulk-videos"
}

variable "source_prefix" {
  description = "Immutable archive object prefix."
  type        = string
  default     = "videos/"
}

variable "staging_prefix" {
  description = "Ephemeral processing-object prefix."
  type        = string
  default     = "face-staging/"
}

variable "staging_ttl_days" {
  description = "Fallback lifecycle deletion age for staged objects. Application deletion should happen immediately after successful processing."
  type        = number
  default     = 1

  validation {
    condition     = var.staging_ttl_days >= 1 && var.staging_ttl_days <= 7
    error_message = "staging_ttl_days must be between 1 and 7 days."
  }
}

variable "subnet_cidr" {
  description = "CIDR for the private Batch subnet."
  type        = string
  default     = "10.42.0.0/24"
}

variable "private_service_range_prefix_length" {
  description = "Prefix length for private service networking allocation used by Cloud SQL."
  type        = number
  default     = 16
}

variable "db_tier" {
  description = "Cloud SQL tier. db-f1-micro is appropriate only for development/prototyping."
  type        = string
  default     = "db-f1-micro"
}

variable "db_disk_size_gb" {
  description = "Initial Cloud SQL disk size."
  type        = number
  default     = 10
}

variable "db_deletion_protection" {
  description = "Protect Cloud SQL instance from Terraform deletion. Enable for long-lived environments."
  type        = bool
  default     = false
}

variable "labels" {
  description = "Common labels."
  type        = map(string)
  default = {
    workload = "face-batch"
    data     = "biometric"
  }
}

variable "cloud_run_worker_image" {
  description = "Immutable Artifact Registry digest for the queue-aware Cloud Run worker."
  type        = string

  validation {
    condition = startswith(
      var.cloud_run_worker_image,
      "${var.region}-docker.pkg.dev/${var.project_id}/",
    ) && can(regex("@sha256:[0-9a-f]{64}$", var.cloud_run_worker_image))
    error_message = "cloud_run_worker_image must be an immutable digest in the project's regional Artifact Registry."
  }
}

variable "cloud_run_task_count" {
  description = "Default tasks per execution. Execution-time overrides are used for controlled runs."
  type        = number
  default     = 1

  validation {
    condition     = var.cloud_run_task_count >= 1 && var.cloud_run_task_count <= 10000
    error_message = "cloud_run_task_count must be between 1 and 10000."
  }
}

variable "match_threshold" {
  description = "Mandatory subject matching similarity threshold."
  type        = number
  default     = 0.55
}

variable "threshold_version" {
  description = "Version label for the mandatory remote matching threshold."
  type        = string
  default     = "controlled-eval-0p55-v1"
}

variable "monitoring_notification_channels" {
  description = "Existing Cloud Monitoring notification-channel resource names. Incidents remain visible without a channel."
  type        = list(string)
  default     = []
}

variable "monitoring_email_address" {
  description = "Operator email address for face-batch alert delivery."
  type        = string
  default     = "jack@jackstruck.info"

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.monitoring_email_address))
    error_message = "monitoring_email_address must be a valid email address."
  }
}

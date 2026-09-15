variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Primary region for KMS, GCS, Artifact Registry, Cloud Run, VPC subnet, and Cloud SQL."
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
  description = "CIDR for the private Cloud Run subnet."
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

variable "interactive_gpu_image" {
  description = "Optional immutable worker digest for the Phase 1 interactive GPU job; defaults to cloud_run_worker_image."
  type        = string
  default     = null
  nullable    = true
  validation {
    condition = var.interactive_gpu_image == null || (
      startswith(
        var.interactive_gpu_image,
        "${var.region}-docker.pkg.dev/${var.project_id}/",
      ) && can(regex("@sha256:[0-9a-f]{64}$", var.interactive_gpu_image))
    )
    error_message = "interactive_gpu_image must be null or an immutable digest in the project's regional Artifact Registry."
  }
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

variable "enable_phase1_console" {
  description = "Create the Phase 1 console and ingestion stubs after a deployable console image is supplied."
  type        = bool
  default     = false
}

variable "enable_retained_enrollment" {
  description = "Enable Phase 2 retained enrollment after its operational gates pass."
  type        = bool
  default     = false
}

variable "console_image" {
  description = "Immutable Artifact Registry digest containing face-console and face-ingest-drain entrypoints."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = var.console_image == null || (
      startswith(var.console_image, "${var.region}-docker.pkg.dev/${var.project_id}/") &&
      can(regex("@sha256:[0-9a-f]{64}$", var.console_image))
    )
    error_message = "console_image must be null or an immutable digest in the project's regional Artifact Registry."
  }
}

variable "console_origin" {
  description = "Exact HTTPS origin used for resumable-upload CORS and mutation Origin validation."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.console_origin == null || can(regex("^https://[^/]+$", var.console_origin))
    error_message = "console_origin must be null or an exact HTTPS origin without a path."
  }
}

variable "approved_iap_member" {
  description = "Single IAM principal granted access through IAP, normally group:address and temporarily user:address during acceptance."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.approved_iap_member == null || can(regex("^(group|user):[^@[:space:]]+@[^@[:space:]]+$", var.approved_iap_member))
    error_message = "approved_iap_member must be null or an explicit group: or user: IAM principal."
  }
}

variable "enable_arbitrary_host_fetch" {
  description = "Enable direct arbitrary HTTPS media after the connection-pinning SSRF matrix passes."
  type        = bool
  default     = false
}

variable "rollback_image_versions" {
  description = "Additional sha256 image versions protected from registry cleanup for rollback."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for version in var.rollback_image_versions : can(regex("^sha256:[0-9a-f]{64}$", version))])
    error_message = "Rollback versions must be complete sha256 digests."
  }
}

variable "subject_management_enabled" {
  type        = bool
  default     = false
  description = "Enable subject management after example backfill and writer readiness checks."
}

variable "gallery_repair_min_similarity" {
  description = "Minimum comparison similarity used only to repair gallery images from retained sources."
  type        = number
  default     = 0.55
  validation {
    condition     = var.gallery_repair_min_similarity >= 0 && var.gallery_repair_min_similarity <= 1
    error_message = "Gallery repair similarity must be between zero and one."
  }
}

variable "source_merges_apply_enabled" {
  description = "Enable explicitly reviewed source groups after the initial proposal review. No unattended application is implemented."
  type        = bool
  default     = false
}

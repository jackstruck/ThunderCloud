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

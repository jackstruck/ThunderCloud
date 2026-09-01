locals {
  developer_project_roles = toset([
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/serviceusage.serviceUsageConsumer",
  ])
}

resource "google_project_iam_member" "developer_project_roles" {
  for_each = local.developer_project_roles

  project = var.project_id
  role    = each.value
  member  = "user:${var.developer_email}"
}

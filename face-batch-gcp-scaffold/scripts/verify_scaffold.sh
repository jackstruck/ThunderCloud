#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${BUCKET:?Set BUCKET}"
: "${SQL_INSTANCE:?Set SQL_INSTANCE}"

echo "== Bucket security =="
gcloud storage buckets describe "gs://${BUCKET}" \
  --project "${PROJECT_ID}" \
  --format='yaml(name,location,iamConfiguration,defaultKmsKeyName,softDeletePolicy,lifecycle)'

echo
echo "== Cloud SQL public+private connector paths/CMEK =="
gcloud sql instances describe "${SQL_INSTANCE}" \
  --project "${PROJECT_ID}" \
  --format='yaml(name,region,databaseVersion,diskEncryptionConfiguration,ipAddresses,settings.ipConfiguration,settings.databaseFlags)'

echo
echo "== Developer project roles =="
: "${DEVELOPER_EMAIL:?set DEVELOPER_EMAIL}"
gcloud projects get-iam-policy "${PROJECT_ID}" \
  --flatten='bindings[].members' \
  --filter="bindings.members:user:${DEVELOPER_EMAIL}" \
  --format='table(bindings.role,bindings.members)'

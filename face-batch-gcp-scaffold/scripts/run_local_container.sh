#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "usage: scripts/run_local_container.sh submit-object ... | submit-manifest ..." >&2
  exit 2
fi

scaffold_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_dir="$(cd "${scaffold_dir}/.." && pwd)"
csek_path="${FACE_CSEK_FILE:-${workspace_dir}/.secrets/gcs-csek.base64}"
image="${FACE_WORKER_IMAGE:-thundercloud-face-batch:local}"
env_file="${FACE_ENV_FILE:-${scaffold_dir}/.env}"

if [[ ! -f "${csek_path}" ]] || [[ "$(stat -c '%a' "${csek_path}")" != "600" ]]; then
  echo "CSEK file must exist and have mode 0600" >&2
  exit 2
fi
if [[ ! -f "${env_file}" ]]; then
  echo "environment file is missing; copy .env.example to .env and configure it" >&2
  exit 2
fi

adc_path="${GOOGLE_APPLICATION_CREDENTIALS:-${CLOUDSDK_CONFIG:-${HOME}/.config/gcloud}/application_default_credentials.json}"
if [[ ! -f "${adc_path}" ]]; then
  echo "developer Application Default Credentials were not found" >&2
  exit 2
fi

exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  --env-file "${env_file}" \
  --env FACE_CSEK_FILE=/run/secrets/gcs-csek.base64 \
  --env GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/google-adc.json \
  --volume "${csek_path}:/run/secrets/gcs-csek.base64:ro" \
  --volume "${adc_path}:/run/secrets/google-adc.json:ro" \
  --volume "${workspace_dir}/bulk-download:${workspace_dir}/bulk-download:ro" \
  --volume "${scaffold_dir}:${scaffold_dir}:ro" \
  --volume "${scaffold_dir}/data:${scaffold_dir}/data:rw" \
  --entrypoint face-ingest \
  "${image}" "$@"

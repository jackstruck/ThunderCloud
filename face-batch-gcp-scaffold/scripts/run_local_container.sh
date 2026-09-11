#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "usage: scripts/run_local_container.sh prepare ... | submit --receipt private-receipts/FILE.json" >&2
  exit 2
fi

scaffold_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${FACE_WORKER_IMAGE:-thundercloud-face-batch:local}"
env_file="${FACE_ENV_FILE:-${scaffold_dir}/.env}"
receipt_dir="${FACE_RECEIPT_DIR:-${scaffold_dir}/private-receipts}"
adc_path="${GOOGLE_APPLICATION_CREDENTIALS:-${CLOUDSDK_CONFIG:-${HOME}/.config/gcloud}/application_default_credentials.json}"

if [[ ! -f "${env_file}" || ! -f "${adc_path}" ]]; then
  echo "Configure the non-secret environment file and developer Application Default Credentials first." >&2
  exit 2
fi

umask 077
mkdir -p "${receipt_dir}"
receipt_dir="$(cd "${receipt_dir}" && pwd)"
exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  --workdir "${scaffold_dir}" \
  --env-file "${env_file}" \
  --env GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/google-adc.json \
  --volume "${adc_path}:/run/secrets/google-adc.json:ro" \
  --volume "${receipt_dir}:${receipt_dir}:rw" \
  --entrypoint face-submit \
  "${image}" "$@"

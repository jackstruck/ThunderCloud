#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "usage: scripts/run_local.sh submit-object ... | submit-manifest ..." >&2
  exit 2
fi

: "${FACE_CSEK_FILE:=/workspaces/ThunderCloud/.secrets/gcs-csek.base64}"
export FACE_CSEK_FILE

if [[ ! -f "${FACE_CSEK_FILE}" ]]; then
  echo "CSEK file does not exist at configured path" >&2
  exit 2
fi
if [[ "$(stat -c '%a' "${FACE_CSEK_FILE}")" != "600" ]]; then
  echo "CSEK file must have mode 0600" >&2
  exit 2
fi

exec python -m worker.ingest "$@"

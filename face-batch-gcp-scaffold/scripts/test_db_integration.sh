#!/usr/bin/env bash
set -euo pipefail

# The maintenance image runs the complete current-schema unit/integration/browser
# acceptance procedure. This replaces the obsolete base-schema-only inline suite.
: "${FACE_MAINTENANCE_IMAGE:?Set FACE_MAINTENANCE_IMAGE to the built immutable image ID}"
: "${FACE_MAINTENANCE_ARTIFACTS:?Set FACE_MAINTENANCE_ARTIFACTS to a new protected artifact directory}"
: "${FACE_MAINTENANCE_EXECUTION:?Set FACE_MAINTENANCE_EXECUTION to a unique execution identifier}"
: "${FACE_MAINTENANCE_RETENTION:?Set FACE_MAINTENANCE_RETENTION to the evidence retention deadline}"
exec "${PYTHON_BIN:-python}" -m maintenance.local run \
  --image "$FACE_MAINTENANCE_IMAGE" \
  --artifacts "$FACE_MAINTENANCE_ARTIFACTS" \
  --execution-id "$FACE_MAINTENANCE_EXECUTION" \
  --retention-deadline "$FACE_MAINTENANCE_RETENTION"

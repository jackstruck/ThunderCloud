#!/usr/bin/env bash
set -euo pipefail
exec python -m worker.operator_submission "$@"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$(cd -- "${SCRIPT_DIR}/../helm/hc-data-platform" && pwd)"
OUTPUT_FILE="$(mktemp)"
trap 'rm -f -- "${OUTPUT_FILE}"' EXIT
HELM_BIN="${HELM_BIN:-helm}"

"${HELM_BIN}" lint "${CHART_DIR}" --values "${CHART_DIR}/values-ci.yaml" --strict
"${HELM_BIN}" template platform-ci "${CHART_DIR}" \
  --namespace hc-data-pilot --values "${CHART_DIR}/values-ci.yaml" >"${OUTPUT_FILE}"

if grep -E '(password|secret(-|_)?key|cursor(-|_)?secret):[[:space:]]+[^<{[:space:]]' "${CHART_DIR}/values.yaml"; then
  echo "values.yaml appears to contain an inline secret" >&2
  exit 1
fi

echo "Rendered chart: ${OUTPUT_FILE}"

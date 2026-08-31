#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: $0 RELEASE NAMESPACE [VALUES_FILE]" >&2
  exit 2
fi

RELEASE="$1"
NAMESPACE="$2"
VALUES_FILE="${3:-}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$(cd -- "${SCRIPT_DIR}/../helm/hc-data-platform" && pwd)"
HELM_BIN="${HELM_BIN:-helm}"
KUBECTL_BIN="${KUBECTL_BIN:-kubectl}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HELM_ARGS=(--namespace "${NAMESPACE}" --create-namespace --wait --timeout 10m)
if [[ -n "${VALUES_FILE}" ]]; then
  HELM_ARGS+=(-f "${VALUES_FILE}")
fi

if ! "${HELM_BIN}" status "${RELEASE}" --namespace "${NAMESPACE}" >/dev/null 2>&1; then
  echo "an existing healthy pilot release is required as the rollback baseline" >&2
  exit 1
fi

PREVIOUS_REVISION="$("${HELM_BIN}" history "${RELEASE}" --namespace "${NAMESPACE}" --output json | "${PYTHON_BIN}" -c 'import json,sys; rows=json.load(sys.stdin); print(rows[-1]["revision"])')"
"${HELM_BIN}" upgrade --install "${RELEASE}" "${CHART_DIR}" "${HELM_ARGS[@]}"
"${KUBECTL_BIN}" --namespace "${NAMESPACE}" rollout status deployment \
  --selector "app.kubernetes.io/instance=${RELEASE},app.kubernetes.io/component=frontend" --timeout=10m
"${KUBECTL_BIN}" --namespace "${NAMESPACE}" rollout status deployment \
  --selector "app.kubernetes.io/instance=${RELEASE},app.kubernetes.io/component=api" --timeout=10m
"${KUBECTL_BIN}" --namespace "${NAMESPACE}" rollout status deployment \
  --selector "app.kubernetes.io/instance=${RELEASE},app.kubernetes.io/component=worker" --timeout=10m

"${HELM_BIN}" rollback "${RELEASE}" "${PREVIOUS_REVISION}" --namespace "${NAMESPACE}" --wait --timeout 10m
"${KUBECTL_BIN}" --namespace "${NAMESPACE}" rollout status deployment \
  --selector "app.kubernetes.io/instance=${RELEASE},app.kubernetes.io/component=frontend" --timeout=10m
"${KUBECTL_BIN}" --namespace "${NAMESPACE}" rollout status deployment \
  --selector "app.kubernetes.io/instance=${RELEASE},app.kubernetes.io/component=api" --timeout=10m
"${KUBECTL_BIN}" --namespace "${NAMESPACE}" rollout status deployment \
  --selector "app.kubernetes.io/instance=${RELEASE},app.kubernetes.io/component=worker" --timeout=10m

"${HELM_BIN}" get manifest "${RELEASE}" --namespace "${NAMESPACE}" >/dev/null
echo "upgrade and rollback exercise completed for ${RELEASE} revision ${PREVIOUS_REVISION}"

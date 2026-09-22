#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
g0="${OPENARM_G0:-/home/hc_op/workspace/openarm-data-integration-plan/g0}"
compose=(bash "$g0/scripts/compose.sh" C)
extra=()
if [[ "${1:-}" == --cached ]]; then
  extra=(-f "$repo/artifacts/g0/compose.cached.json")
elif [[ $# != 0 ]]; then
  echo 'usage: test-processing.sh [--cached]' >&2
  exit 2
fi
mkdir -p "$repo/artifacts/g0/evidence"
"${compose[@]}" config --quiet
"${compose[@]}" "${extra[@]}" build migration
"${compose[@]}" "${extra[@]}" up -d postgres temporal minio minio-init migration
"${compose[@]}" "${extra[@]}" run --rm --no-deps --entrypoint python migration \
  tests/robot_ingest/prepare_processing_test_db.py
"${compose[@]}" run --rm --no-deps --user "$(id -u):$(id -g)" --entrypoint python \
  -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  -e ROBOT_INGEST_POSTGRES_DSN=postgresql://hc:hc@postgres:5432/hc_data \
  -e C_EVIDENCE_DIR=/evidence -v "$g0:/g0:ro" \
  -v "$repo/artifacts/g0/evidence:/evidence:rw" migration -m pytest \
  tests/robot_ingest/test_processing_integration.py \
  tests/robot_ingest/test_processing_contract.py \
  tests/robot_ingest/test_robot_ingest_service.py \
  tests/robot_ingest/test_robot_ingest_api.py \
  tests/tools/test_robot_ingest_upload.py \
  -q -p pytest_asyncio.plugin -p no:cacheprovider --tb=short \
  --junitxml=/evidence/c-tests.xml 2>&1 | tee "$repo/artifacts/g0/evidence/c-tests.log"

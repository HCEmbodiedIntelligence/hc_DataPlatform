#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${REPOSITORY}/backend/.venv/bin/python}"

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/compose_server_migration_exercise.py" "$@"

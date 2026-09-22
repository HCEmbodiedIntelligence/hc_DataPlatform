#!/usr/bin/env bash
# B-only, independent Python 3.12 environment; platform dependencies stay untouched.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
g0_dir=/home/hc_op/workspace/openarm-data-integration-plan/g0
mkdir -p "$repo_root/.reader-compose" "$repo_root/artifacts/g0"

if [[ "${1:-}" == --install ]]; then
  bash "$g0_dir/scripts/compose.sh" B run --rm --no-deps --user 1000:1000 \
    -v "$repo_root/.reader-compose:/reader-env:rw" \
    -v "$repo_root/artifacts/g0/reader-requirements.lock:/reader.lock:ro" \
    --entrypoint sh migration -c \
    'python -m venv /reader-env && /reader-env/bin/pip install -r /reader.lock --extra-index-url https://download.pytorch.org/whl/cpu'
fi

bash "$g0_dir/scripts/compose.sh" B run --rm --no-deps --user 1000:1000 \
  -v "$repo_root/.reader-compose:/reader-env:ro" \
  -v "$repo_root/scripts:/scripts:ro" \
  -v "$repo_root/backend/tests/fixtures/openarm-g0:/sources:ro" \
  -v "$repo_root/artifacts/g0:/delivery:rw" \
  -e HF_HOME=/tmp/hf -e HF_HUB_OFFLINE=1 -e HF_DATASETS_OFFLINE=1 \
  -e TORCHINDUCTOR_CACHE_DIR=/tmp/torch \
  --entrypoint sh migration -c '
    set -eu
    /reader-env/bin/pip check
    /reader-env/bin/python --version
    ffmpeg -version
    for name in valid-openarm valid-generic a-actual; do
      /reader-env/bin/python /scripts/verify_openarm_reader.py \
        "/sources/$name" "/sources/$name" "/delivery/reader-source-$name.json"
      /reader-env/bin/python /scripts/verify_openarm_reader.py \
        "/delivery/roundtrip/$name" "/sources/$name" "/delivery/reader-roundtrip-$name.json"
    done
  '

#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/robot-env.sh"
sdk_wheel="$CHAYI_ROBOT_WORKSPACE/evidence/wheels/pyrealsense2-2.58.4.10922-cp310-cp310-manylinux1_x86_64.whl"
sdk_digest="$(sha256sum -- "$sdk_wheel")"
if [[ "${sdk_digest%% *}" != 809d48b329890594ab158c924537d7ddd6b15acab0aa22d6ba29e974f09ddc9d ]]; then
  printf '%s\n' 'Official RealSense wheel SHA-256 mismatch' >&2
  exit 1
fi
unset PYTHONPATH
export PYTHONNOUSERSITE=1
# Only the G1 target directory is written. The existing interpreter is read-only.
/home/hc_op/workspace/openarm-integration/D/robot/.venv/bin/python -m pip install \
  --disable-pip-version-check --no-cache-dir --no-index --no-deps --upgrade \
  --target "$CHAYI_ROBOT_WORKSPACE/install/preview-python" "$sdk_wheel"
PYTHONPATH="$CHAYI_ROBOT_WORKSPACE/install/preview-python" \
  /home/hc_op/workspace/openarm-integration/D/robot/.venv/bin/python -c \
  'from importlib.metadata import version; import pyrealsense2; print(version("pyrealsense2"), pyrealsense2.__file__)'

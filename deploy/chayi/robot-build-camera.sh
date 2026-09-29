#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/robot-env.sh"
unset PYTHONPATH AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH
set +u
source /opt/ros/humble/setup.bash
set -u
# Build/install only. No launch, device access, capture or daily-workspace overlay.
exec colcon --log-base "$CHAYI_ROBOT_WORKSPACE/log/camera-build" build \
  --base-paths "$CHAYI_ROBOT_WORKSPACE/src/humanoid_camera" \
  --build-base "$CHAYI_ROBOT_WORKSPACE/build/camera" \
  --install-base "$CHAYI_ROBOT_WORKSPACE/install/ros" \
  --packages-select humanoid_camera \
  --cmake-args -DPython3_EXECUTABLE=/usr/bin/python3

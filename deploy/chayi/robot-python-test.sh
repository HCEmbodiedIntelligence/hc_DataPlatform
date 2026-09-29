#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/robot-env.sh"
# Message types come from the ROS distribution only, never the daily robot overlay.
unset PYTHONPATH AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH
set +u
source /opt/ros/humble/setup.bash
if [[ -f "$CHAYI_ROBOT_WORKSPACE/install/ros/local_setup.bash" ]]; then
  source "$CHAYI_ROBOT_WORKSPACE/install/ros/local_setup.bash"
fi
set -u
export PYTHONPATH="$CHAYI_ROBOT_WORKSPACE/src/humanoid_manager/python:$CHAYI_ROBOT_WORKSPACE/src/humanoid_camera/python:$CHAYI_ROBOT_WORKSPACE/install/preview-python:${PYTHONPATH:-}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export HUMANOID_UPLOAD_TEST_CLI="$CHAYI_UPLOADER"
cd "$CHAYI_ROBOT_WORKSPACE/src/humanoid_manager"
# Existing dependency environment is read-only input; source and output are G1.
exec /home/hc_op/workspace/openarm-integration/D/robot/.venv/bin/python -m pytest "$@"

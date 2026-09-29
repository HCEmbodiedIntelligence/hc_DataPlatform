#!/usr/bin/env bash
# Source only for G1 test commands. This file does not start ROS, capture or motion.
export CHAYI_ROBOT_WORKSPACE=/home/hc_op/workspace/teleop_ws-chayi-work
export ROS_DOMAIN_ID=221
export ROS_LOCALHOST_ONLY=1
export ROS_LOG_DIR="$CHAYI_ROBOT_WORKSPACE/log"
export CHAYI_MANAGER_STATE_ROOT="$CHAYI_ROBOT_WORKSPACE/state"
export CHAYI_RECORDING_ROOT="$CHAYI_ROBOT_WORKSPACE/recordings"
export CHAYI_UPLOAD_QUEUE_ROOT="$CHAYI_ROBOT_WORKSPACE/upload-queue"
export CHAYI_UPLOADER="$CHAYI_ROBOT_WORKSPACE/install/uploader/bin/humanoid-upload"
# Select the desired isolated source/install explicitly per test. Do not source
# the daily teleop_ws/install overlay or reuse its manager/upload configuration.

"""Explicit synthetic MCAP fixture using actual ROS2 CDR serialization."""

import hashlib
import io
import json

import numpy as np
from mcap.writer import CompressionType, Writer
from mcap_ros2._dynamic import serialize_dynamic

from hc_data_platform.ingest.models import RolloutManifestV1
from hc_data_platform.ingest.ports import crc64_ecma
from tests.episode_store.test_mcap_images import ROS_IMAGE, encoded, profile

START_NS = 1_700_000_000_000_000_000
RGB = "/head/color"
DEPTH = "/head/depth"
FLOAT_DEPTH = "/wrist/depth"
TOPICS = (RGB, DEPTH, FLOAT_DEPTH, "/joint_states", "/action")


def make_mcap(project, task, suffix, *, missing_units=False):
    output = io.BytesIO()
    writer = Writer(output, compression=CompressionType.NONE)
    writer.start(profile="ros2", library="hc-g1-synthetic-mcap/1")
    rgb_schema = writer.register_schema("sensor_msgs/Image", "jsonschema", b"{}")
    depth_schema = writer.register_schema("sensor_msgs/msg/Image", "ros2msg", ROS_IMAGE.encode())
    number_schema = writer.register_schema("hc/Numeric", "jsonschema", b"{}")
    channels = {}
    for topic in TOPICS:
        depth = topic in {DEPTH, FLOAT_DEPTH}
        channels[topic] = writer.register_channel(
            topic,
            "cdr" if depth else "json",
            depth_schema if depth else rgb_schema if topic == RGB else number_schema,
            metadata={
                "hc.depth_source": json.dumps(profile("32FC1" if topic == FLOAT_DEPTH else "16UC1"))
            }
            if depth and not missing_units
            else {},
        )
    serializer = serialize_dynamic("sensor_msgs/msg/Image", ROS_IMAGE)["sensor_msgs/msg/Image"]
    for index in range(30):
        timestamp = START_NS + index * 1_000_000_000 // 30
        rgb = np.empty((24, 32, 3), np.uint8)
        rgb[:] = (70 + index * 3, 60 + index * 4, 120)
        values = {
            RGB: json.dumps(encoded(rgb)).encode(),
            "/joint_states": json.dumps({"position": [index / 100, -index / 100]}).encode(),
            "/action": json.dumps({"value": [index / 100, index / 200]}).encode(),
        }
        for topic in (DEPTH, FLOAT_DEPTH):
            is_float = topic == FLOAT_DEPTH
            pixels = np.full((16, 16), 4000 + index, dtype="<f4" if is_float else "<u2")
            pixels[0, :4] = (0, float("nan") if is_float else 65535, 5, 48000)
            if is_float:
                pixels[0, 4] = float("inf")
            values[topic] = serializer(
                {
                    "header": {"stamp": {"sec": 5, "nanosec": index}, "frame_id": topic},
                    "height": 16,
                    "width": 16,
                    "encoding": "32FC1" if is_float else "16UC1",
                    "is_bigendian": 0,
                    "step": 16 * pixels.dtype.itemsize,
                    "data": pixels.tobytes(),
                }
            )
        for topic in TOPICS:
            # Zero sequence is legal. Source ordinals must still distinguish frames.
            writer.add_message(channels[topic], timestamp, values[topic], timestamp - 7, sequence=0)
    writer.finish()
    body = output.getvalue()
    sha, crc = hashlib.sha256(body).hexdigest(), crc64_ecma(body)
    manifest = RolloutManifestV1.model_validate(
        {
            "schema_version": 1,
            "project_id": project,
            "task_id": task,
            "collection_job_id": "job-" + suffix,
            "collection_session_id": "capture-" + suffix,
            "recording_request_id": "request-" + suffix,
            "data_package_id": "package-" + suffix,
            "rollout_id": "rollout-" + suffix,
            "sequence_no": 1,
            "robot_id": "synthetic-robot",
            "start_time": "2023-11-14T22:13:20Z",
            "end_time": "2023-11-14T22:13:21Z",
            "cameras": [
                {
                    "camera_id": topic.strip("/").replace("/", "_"),
                    "topic": topic,
                    "encoding": "jpeg"
                    if topic == RGB
                    else "32FC1"
                    if topic == FLOAT_DEPTH
                    else "16UC1",
                }
                for topic in (RGB, DEPTH, FLOAT_DEPTH)
            ],
            "topics": [
                {
                    "name": topic,
                    "required": True,
                    "message_encoding": "cdr" if topic in (DEPTH, FLOAT_DEPTH) else "json",
                    "schema_name": "sensor_msgs/msg/Image"
                    if topic in (DEPTH, FLOAT_DEPTH)
                    else "sensor_msgs/Image"
                    if topic == RGB
                    else "hc/Numeric",
                }
                for topic in TOPICS
            ],
            "expected_topics": TOPICS,
            "actual_topics": TOPICS,
            "files": [
                {
                    "path": "recording.mcap",
                    "size": len(body),
                    "sha256": sha,
                    "crc64": crc,
                    "role": "RAW_MCAP",
                    "media_type": "application/octet-stream",
                }
            ],
            "file_size": len(body),
            "sha256": sha,
            "crc64": crc,
            "compression": "none",
            "recorder_version": "hc-g1-synthetic-mcap/1",
        }
    )
    return body, manifest

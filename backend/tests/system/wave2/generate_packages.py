"""Generate deterministic, minimal BE22 MCAP/Manifest package fixtures.

This module is test data tooling.  It deliberately does not create an account,
project, bucket, or production seed.  ``build_files`` is side-effect free so the
contract test can prove that checked-in bytes are reproducible.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
from pathlib import Path
from typing import Any

from PIL import Image

from hc_data_platform.ingest.ports import crc64_ecma

ROOT = Path(__file__).resolve().parent / "data"
MCAP_MAGIC = b"\x89MCAP0\r\n"
START_NS = 1_700_000_000_000_000_000
PERIOD_NS = 1_000_000_000 // 30

BASE_TOPICS = (
    (1, "/camera/front/image", "sensor_msgs/Image", "uint8[] data"),
    (2, "/joint_states", "sensor_msgs/JointState", "float64[] position"),
    (3, "/action", "hc_msgs/Action", "float64[] value"),
)
REAR_CAMERA = (4, "/camera/rear/image", "sensor_msgs/Image", "uint8[] data")


def _is_camera_topic(topic: str) -> bool:
    return topic.startswith("/camera/")


def _schema_name(topic: str) -> str:
    return next(item[2] for item in BASE_TOPICS + (REAR_CAMERA,) if item[1] == topic)


def _camera_message(topic: str, sequence: int) -> bytes:
    output = io.BytesIO()
    base = 48 if "front" in topic else 144
    Image.new(
        "RGB",
        (32, 24),
        color=((base + sequence * 3) % 256, (base + sequence * 5) % 256, base),
    ).save(output, format="JPEG", quality=88)
    encoded = output.getvalue()
    return json.dumps(
        {
            "encoding": "jpeg",
            "width": 32,
            "height": 24,
            "jpeg_sha256": hashlib.sha256(encoded).hexdigest(),
            "data_base64": base64.b64encode(encoded).decode("ascii"),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("<I", len(encoded)) + encoded


def _byte_array(value: bytes) -> bytes:
    return struct.pack("<I", len(value)) + value


def _record(opcode: int, payload: bytes) -> bytes:
    return bytes((opcode,)) + struct.pack("<Q", len(payload)) + payload


def _message(channel_id: int, sequence: int, timestamp_ns: int, payload: bytes) -> bytes:
    return _record(
        0x05,
        struct.pack("<HIQQ", channel_id, sequence, timestamp_ns, timestamp_ns) + payload,
    )


def _ros2_cdr_sequence(field_values: tuple[float, ...]) -> bytes:
    """Encode one little-endian ROS 2 CDR ``float64[]`` field.

    The four-byte XCDR1 encapsulation is followed by the sequence length and
    naturally eight-byte-aligned float64 values. Keeping the fixture generator
    independent from a ROS installation makes these exact bytes reproducible,
    while the production verifier still decodes them with mcap-ros2-support.
    """

    return b"\x00\x01\x00\x00" + struct.pack(
        f"<I4x{len(field_values)}d", len(field_values), *field_values
    )


def _ros2_message(topic: str, sequence: int) -> bytes:
    if topic == "/joint_states":
        return _ros2_cdr_sequence((sequence / 100.0, -sequence / 100.0))
    if topic == "/action":
        return _ros2_cdr_sequence((sequence / 29.0, 1.0 - sequence / 29.0))
    raise ValueError(f"no deterministic ROS 2 fixture encoder for {topic}")


def _mcap(
    *,
    multi_camera: bool = False,
    omitted_topics: frozenset[str] = frozenset(),
    omitted_frames: frozenset[tuple[str, int]] = frozenset(),
) -> bytes:
    topics = BASE_TOPICS + ((REAR_CAMERA,) if multi_camera else ())
    selected = tuple(topic for topic in topics if topic[1] not in omitted_topics)
    records = [_record(0x01, _string("ros2") + _string("hc-be22-fixture/1"))]
    for schema_id, _topic, schema_name, schema_text in selected:
        schema_encoding = "jsonschema" if _is_camera_topic(_topic) else "ros2msg"
        records.append(
            _record(
                0x03,
                struct.pack("<H", schema_id)
                + _string(schema_name)
                + _string(schema_encoding)
                + _byte_array(schema_text.encode("utf-8")),
            )
        )
    for channel_id, topic, _schema_name, _schema_text in selected:
        records.append(
            _record(
                0x04,
                struct.pack("<HH", channel_id, channel_id)
                + _string(topic)
                + _string("json" if _is_camera_topic(topic) else "cdr")
                + struct.pack("<I", 0),
            )
        )
    for sequence in range(30):
        timestamp_ns = START_NS + sequence * PERIOD_NS
        for channel_id, topic, _schema_name, _schema_text in selected:
            if (topic, sequence) in omitted_frames:
                continue
            records.append(
                _message(
                    channel_id,
                    sequence,
                    timestamp_ns,
                    _camera_message(topic, sequence)
                    if _is_camera_topic(topic)
                    else _ros2_message(topic, sequence),
                )
            )
    records.extend(
        (
            _record(0x0F, struct.pack("<I", 0)),
            _record(0x02, struct.pack("<QQI", 0, 0, 0)),
        )
    )
    return MCAP_MAGIC + b"".join(records) + MCAP_MAGIC


def _manifest(
    payload: bytes,
    *,
    scenario: str,
    cameras: tuple[tuple[str, str], ...],
    actual_topics: tuple[str, ...],
    expected_topics: tuple[str, ...],
) -> dict[str, Any]:
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "schema_version": 1,
        "project_id": "be22-template-project",
        "task_id": "be22-template-task",
        "collection_job_id": f"be22-{scenario}-job",
        "rollout_id": f"be22-{scenario}-rollout",
        "collection_session_id": f"be22-{scenario}-session",
        "recording_request_id": f"be22-{scenario}-request",
        "data_package_id": f"be22-{scenario}-package",
        "sequence_no": 1,
        "robot_id": "be22-test-robot",
        "start_time": "2023-11-14T22:13:20Z",
        "end_time": "2023-11-14T22:13:21Z",
        "cameras": [
            {"camera_id": camera_id, "topic": topic, "encoding": "jpeg"}
            for camera_id, topic in cameras
        ],
        "topics": [
            {
                "name": topic,
                "required": topic in expected_topics,
                "message_encoding": "json" if _is_camera_topic(topic) else "cdr",
                "schema_name": _schema_name(topic),
            }
            for topic in actual_topics
        ],
        "expected_topics": list(expected_topics),
        "actual_topics": list(actual_topics),
        "files": [
            {
                "path": "recording.mcap",
                "size": len(payload),
                "sha256": digest,
                "crc64": str(crc64_ecma(payload)),
                "media_type": "application/octet-stream",
                "role": "RAW_MCAP",
            }
        ],
        "file_size": len(payload),
        "sha256": digest,
        "crc64": str(crc64_ecma(payload)),
        "compression": "none",
        "recorder_version": "hc-be22-fixture/1",
    }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_files() -> dict[str, bytes]:
    required = tuple(item[1] for item in BASE_TOPICS)
    legal = _mcap()
    multi = _mcap(multi_camera=True)
    missing_frame = _mcap(
        multi_camera=True,
        omitted_frames=frozenset({(REAR_CAMERA[1], 12)}),
    )
    missing_topic = _mcap(omitted_topics=frozenset({"/action"}))

    payloads = {
        "legal": legal,
        "multi_camera": multi,
        "missing_frame": missing_frame,
        "missing_topic": missing_topic,
    }
    manifests = {
        "legal": _manifest(
            legal,
            scenario="legal",
            cameras=(("front", "/camera/front/image"),),
            actual_topics=required,
            expected_topics=required,
        ),
        "multi_camera": _manifest(
            multi,
            scenario="multi-camera",
            cameras=(
                ("front", "/camera/front/image"),
                ("rear", "/camera/rear/image"),
            ),
            actual_topics=required + (REAR_CAMERA[1],),
            expected_topics=required + (REAR_CAMERA[1],),
        ),
        "missing_frame": _manifest(
            missing_frame,
            scenario="missing-frame",
            cameras=(
                ("front", "/camera/front/image"),
                ("rear", "/camera/rear/image"),
            ),
            actual_topics=required + (REAR_CAMERA[1],),
            expected_topics=required + (REAR_CAMERA[1],),
        ),
        "missing_topic": _manifest(
            missing_topic,
            scenario="missing-topic",
            cameras=(("front", "/camera/front/image"),),
            actual_topics=("/camera/front/image", "/joint_states"),
            expected_topics=required,
        ),
    }
    bad = json.loads(json.dumps(manifests["legal"]))
    bad["data_package_id"] = "be22-bad-manifest-package"
    bad["rollout_id"] = "be22-bad-manifest-rollout"
    bad["sha256"] = "0" * 64

    files: dict[str, bytes] = {
        f"packages/{name}.mcap": content for name, content in payloads.items()
    }
    files.update(
        {f"manifests/{name}.json": _json_bytes(value) for name, value in manifests.items()}
    )
    files["manifests/bad_manifest.json"] = _json_bytes(bad)

    scenarios: list[dict[str, Any]] = []
    definitions = (
        ("legal", "ACCEPT", "COMPLETE", None),
        ("multi_camera", "ACCEPT", "MULTI_CAMERA", None),
        ("missing_frame", "ACCEPT", "MISSING_FRAME", None),
        ("missing_topic", "ACCEPT_WITH_MISSING_TOPIC", "MISSING_TOPIC", None),
        ("bad_manifest", "REJECT", "INVALID_MANIFEST", None),
        ("duplicate_package", "IDEMPOTENT_REPLAY", "NO_DUPLICATE_SIDE_EFFECT", "legal"),
    )
    for name, preflight, condition, duplicate_of in definitions:
        source = duplicate_of or name
        payload_name = "legal" if source == "bad_manifest" else source
        if name == "bad_manifest":
            payload_name = "legal"
        payload_path = f"packages/{payload_name}.mcap"
        manifest_path = f"manifests/{source}.json"
        if name == "bad_manifest":
            manifest_path = "manifests/bad_manifest.json"
        payload = files[payload_path]
        manifest_bytes = files[manifest_path]
        scenarios.append(
            {
                "name": name,
                "payload_path": payload_path,
                "payload_size": len(payload),
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "manifest_path": manifest_path,
                "manifest_size": len(manifest_bytes),
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "expected_preflight": preflight,
                "expected_condition": condition,
                "duplicate_of": duplicate_of,
            }
        )
    files["catalog.json"] = _json_bytes(
        {
            "schema_version": 1,
            "generator": "generate_packages.py",
            "scenarios": scenarios,
        }
    )
    return files


def main() -> None:
    for relative_path, content in build_files().items():
        target = ROOT / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


if __name__ == "__main__":
    main()

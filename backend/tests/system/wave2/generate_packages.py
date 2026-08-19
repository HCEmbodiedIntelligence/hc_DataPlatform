"""Generate deterministic, minimal BE22 MCAP/Manifest package fixtures.

This module is test data tooling.  It deliberately does not create an account,
project, bucket, or production seed.  ``build_files`` is side-effect free so the
contract test can prove that checked-in bytes are reproducible.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

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
        records.append(
            _record(
                0x03,
                struct.pack("<H", schema_id)
                + _string(schema_name)
                + _string("ros2msg")
                + _byte_array(schema_text.encode("utf-8")),
            )
        )
    for channel_id, topic, _schema_name, _schema_text in selected:
        records.append(
            _record(
                0x04,
                struct.pack("<HH", channel_id, channel_id)
                + _string(topic)
                + _string("cdr")
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
                    f"{topic}:{sequence:02d}".encode(),
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
        "cameras": [{"camera_id": camera_id, "topic": topic} for camera_id, topic in cameras],
        "topics": [
            {"name": topic, "required": topic in expected_topics} for topic in actual_topics
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

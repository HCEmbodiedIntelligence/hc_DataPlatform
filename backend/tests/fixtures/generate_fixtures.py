"""Generate deterministic BE-12 MCAP and structured quality fixtures.

The generated MCAP files intentionally use only the uncompressed, summary-free
subset needed by the phase-one verifier.  Re-running this file must produce the
same bytes and the same fixture manifest on every host.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
MCAP_DIR = ROOT / "mcap"
STRUCTURED_DIR = ROOT / "structured"
MCAP_MAGIC = b"\x89MCAP0\r\n"
START_NS = 1_700_000_000_000_000_000
PERIOD_NS = 1_000_000_000 // 30

TOPICS = (
    (1, "/camera/front/image", "sensor_msgs/Image", "uint8[] data"),
    (2, "/joint_states", "sensor_msgs/JointState", "float64[] position"),
    (3, "/action", "hc_msgs/Action", "float64[] value"),
    (4, "/points", "sensor_msgs/PointCloud2", "uint8[] data"),
)


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


def _mcap(*, omitted_topics: frozenset[str] = frozenset()) -> bytes:
    selected = tuple(item for item in TOPICS if item[1] not in omitted_topics)
    records = [_record(0x01, _string("ros2") + _string("hc-be12-golden/1"))]
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
            payload = f"{topic}:{sequence:02d}".encode()
            records.append(_message(channel_id, sequence, timestamp_ns, payload))
    records.extend(
        (
            _record(0x0F, struct.pack("<I", 0)),
            _record(0x02, struct.pack("<QQI", 0, 0, 0)),
        )
    )
    return MCAP_MAGIC + b"".join(records) + MCAP_MAGIC


def _bad_crc_mcap() -> bytes:
    header = _record(0x01, _string("ros2") + _string("hc-be12-golden/1"))
    schema = _record(
        0x03,
        struct.pack("<H", 1)
        + _string("sensor_msgs/Image")
        + _string("ros2msg")
        + _byte_array(b"uint8[] data"),
    )
    channel = _record(
        0x04,
        struct.pack("<HH", 1, 1)
        + _string("/camera/front/image")
        + _string("cdr")
        + struct.pack("<I", 0),
    )
    nested = _message(1, 0, START_NS, b"camera-frame")
    wrong_crc = (zlib.crc32(nested) + 1) & 0xFFFFFFFF
    chunk_payload = (
        struct.pack("<QQQI", START_NS, START_NS, len(nested), wrong_crc)
        + _string("")
        + struct.pack("<Q", len(nested))
        + nested
    )
    records = (
        header,
        schema,
        channel,
        _record(0x06, chunk_payload),
        _record(0x0F, struct.pack("<I", 0)),
        _record(0x02, struct.pack("<QQI", 0, 0, 0)),
    )
    return MCAP_MAGIC + b"".join(records) + MCAP_MAGIC


def _timestamps(frequency_hz: int) -> list[int]:
    return [START_NS + index * (1_000_000_000 // frequency_hz) for index in range(frequency_hz)]


def _quality_profile() -> dict[str, Any]:
    return {
        "profile_id": "pilot-30hz-v1",
        "engine_version": "be06-qc/1",
        "required_topics": [item[1] for item in TOPICS],
        "target_frequency_hz": 30,
        "frequency_risk_ratio": 0.95,
        "frequency_reject_ratio": 0.75,
        "minimum_coverage_ratio": 0.90,
        "black_luma_threshold": 5.0,
        "black_frame_ratio_risk": 0.02,
        "point_count_range": [10, 2_000_000],
        "empty_point_cloud_ratio_reject": 0.20,
    }


def _quality_case(name: str) -> dict[str, Any]:
    timestamps = {topic: _timestamps(30) for _, topic, _, _ in TOPICS}
    expected_status = "PASS"
    expected_codes: list[str] = []
    images: dict[str, list[dict[str, Any]]] = {}
    point_clouds: dict[str, list[dict[str, Any]]] = {}
    if name == "28hz":
        timestamps["/camera/front/image"] = _timestamps(28)
        expected_status = "RISK"
        expected_codes = ["QC_FREQUENCY_LOW"]
    elif name == "timestamp_backward":
        timestamps["/joint_states"][10], timestamps["/joint_states"][11] = (
            timestamps["/joint_states"][11],
            timestamps["/joint_states"][10],
        )
        expected_status = "REJECT"
        expected_codes = ["QC_TIMESTAMP_BACKWARD"]
    elif name == "black_frames":
        images["/camera/front/image"] = [
            {
                "timestamp_ns": timestamp,
                "luma_mean": 0.0 if index < 3 else 96.0,
                "fingerprint": f"frame-{index:02d}",
                "corrupt": False,
            }
            for index, timestamp in enumerate(_timestamps(30))
        ]
        expected_status = "RISK"
        expected_codes = ["QC_IMAGE_BLACK"]
    elif name == "empty_point_cloud":
        point_clouds["/points"] = [
            {"timestamp_ns": timestamp, "point_count": 0 if index < 7 else 1024}
            for index, timestamp in enumerate(_timestamps(30))
        ]
        expected_status = "REJECT"
        expected_codes = ["QC_POINT_CLOUD_EMPTY"]
    elif name != "legal_30hz":
        raise ValueError(name)
    return {
        "case": name,
        "expected": {"status": expected_status, "codes": expected_codes},
        "profile": _quality_profile(),
        "input": {
            "rollout_id": f"fixture-{name}",
            "source_sha256": "a" * 64,
            "start_ns": START_NS,
            "end_ns": START_NS + 1_000_000_000,
            "topic_timestamps_ns": timestamps,
            "images": images,
            "actions": [{"timestamp_ns": START_NS, "values": [0.0, 0.0]}],
            "point_clouds": point_clouds,
            "complete_step_ratio": 1.0,
        },
    }


def _write(path: Path, content: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def main() -> None:
    legal = _mcap()
    mcap_entries = []
    for filename, payload, status, codes, required_topics in (
        ("legal.mcap", legal, "RAW_VERIFIED", [], [item[1] for item in TOPICS]),
        (
            "truncated.mcap",
            legal[:-5],
            "REJECTED",
            ["MCAP_TRAILING_MAGIC_MISSING"],
            [item[1] for item in TOPICS],
        ),
        (
            "bad_crc.mcap",
            _bad_crc_mcap(),
            "REJECTED",
            ["MCAP_CHUNK_CRC_MISMATCH"],
            ["/camera/front/image"],
        ),
        (
            "missing_topic.mcap",
            _mcap(omitted_topics=frozenset({"/points"})),
            "REJECTED",
            ["MCAP_REQUIRED_TOPIC_MISSING"],
            [item[1] for item in TOPICS],
        ),
    ):
        entry = _write(MCAP_DIR / filename, payload)
        entry.update(
            {
                "expected_status": status,
                "expected_codes": codes,
                "required_topics": required_topics,
            }
        )
        mcap_entries.append(entry)

    structured_entries = []
    for name in ("legal_30hz", "28hz", "timestamp_backward", "black_frames", "empty_point_cloud"):
        content = (json.dumps(_quality_case(name), indent=2, sort_keys=True) + "\n").encode()
        structured_entries.append(_write(STRUCTURED_DIR / f"{name}.json", content))

    manifest = {
        "schema_version": 1,
        "generator": "generate_fixtures.py",
        "mcap": mcap_entries,
        "structured": structured_entries,
    }
    (ROOT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

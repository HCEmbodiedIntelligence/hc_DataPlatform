from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.manifest import (
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_MANIFEST_BYTES,
    parse_manifest_bytes,
)
from hc_data_platform.ingest.models import RolloutManifestV1, raw_object_key


def valid_manifest() -> dict[str, Any]:
    start = datetime(2026, 8, 17, 8, tzinfo=timezone.utc)
    return {
        "schema_version": 1,
        "project_id": "project-a",
        "task_id": "task-a",
        "collection_job_id": "job-a",
        "rollout_id": "package-a",
        "collection_session_id": "session-a",
        "recording_request_id": "request-a",
        "data_package_id": "data-package-a",
        "sequence_no": 1,
        "robot_id": "robot-a",
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(seconds=1)).isoformat(),
        "cameras": [{"camera_id": "front", "topic": "/camera/front"}],
        "topics": [{"name": "/camera/front", "required": True}],
        "expected_topics": ["/camera/front"],
        "actual_topics": ["/camera/front"],
        "files": [
            {
                "path": "recording.mcap",
                "size": 1024,
                "sha256": "a" * 64,
                "crc64": "1",
                "role": "RAW_MCAP",
            }
        ],
        "file_size": 1024,
        "sha256": "a" * 64,
        "crc64": "1",
        "compression": "zstd",
        "recorder_version": "recorder/1.0",
    }


@pytest.mark.parametrize(
    "field",
    ["project_id", "task_id", "collection_job_id", "rollout_id", "robot_id"],
)
@pytest.mark.parametrize(
    "attack",
    ["../escape", "nested/path", "nul\x00suffix", "percent%2fescape", "空白"],
)
def test_manifest_identity_rejects_path_and_control_injection(field: str, attack: str) -> None:
    payload = valid_manifest()
    payload[field] = attack
    with pytest.raises(ValidationError):
        RolloutManifestV1.model_validate(payload)


def test_validated_manifest_can_only_build_a_scoped_raw_object_key() -> None:
    manifest = RolloutManifestV1.model_validate(valid_manifest())
    key = raw_object_key(manifest)
    assert key.startswith("raw/v1/project=project-a/")
    assert ".." not in key
    assert "//" not in key
    assert key.endswith("/recording.mcap")


def test_preflight_reports_read_only_discovery_and_missing_topics() -> None:
    payload = valid_manifest()
    payload["expected_topics"] = ["/camera/front", "/robot/action"]
    result = parse_manifest_bytes(json.dumps(payload).encode())

    assert result.identifiers.collection_session_id == "session-a"
    assert result.identifiers.recording_request_id == "request-a"
    assert result.identifiers.data_package_id == "data-package-a"
    assert result.discovery.read_only is True
    assert [camera.camera_id for camera in result.discovery.cameras] == ["front"]
    assert [topic.name for topic in result.discovery.topics] == ["/camera/front"]
    assert result.discovery.missing_expected_topics == ("/robot/action",)


def test_manifest_rejects_unknown_fields() -> None:
    payload = valid_manifest()
    payload["object_key"] = "raw/v1/project=victim/forged.mcap"
    with pytest.raises(ValidationError):
        RolloutManifestV1.model_validate(payload)


def test_manifest_rejects_duplicate_json_keys() -> None:
    encoded = json.dumps(valid_manifest()).replace(
        '"project_id": "project-a"',
        '"project_id": "project-a", "project_id": "project-b"',
        1,
    )
    with pytest.raises(ProblemException):
        parse_manifest_bytes(encoded.encode())


def test_manifest_rejects_topic_resource_exhaustion() -> None:
    payload = valid_manifest()
    payload["expected_topics"] = [f"/camera/{index}/" + "x" * 4096 for index in range(4096)]
    with pytest.raises(ValidationError):
        RolloutManifestV1.model_validate(payload)


def test_manifest_rejects_topic_control_characters() -> None:
    payload = valid_manifest()
    payload["actual_topics"] = ["/camera/front\x00forged"]
    with pytest.raises(ValidationError):
        RolloutManifestV1.model_validate(payload)


@pytest.mark.parametrize("attack", ["../recording.mcap", "/absolute.mcap", "a//b.mcap", "a\\b"])
def test_manifest_rejects_unsafe_file_paths(attack: str) -> None:
    payload = valid_manifest()
    payload["files"][0]["path"] = attack
    with pytest.raises(ValidationError):
        RolloutManifestV1.model_validate(payload)


def test_manifest_parser_rejects_oversized_json_before_schema_validation() -> None:
    with pytest.raises(ProblemException) as captured:
        parse_manifest_bytes(b"{" + b" " * MAX_MANIFEST_BYTES + b"}")
    assert captured.value.problem.code == "MANIFEST_TOO_LARGE"


def test_manifest_parser_rejects_excessive_nesting() -> None:
    nested: object = 0
    for _ in range(MAX_JSON_DEPTH + 1):
        nested = [nested]
    with pytest.raises(ProblemException) as captured:
        parse_manifest_bytes(json.dumps(nested).encode())
    assert captured.value.problem.code == "MANIFEST_INVALID"


def test_manifest_parser_rejects_excessive_json_nodes() -> None:
    body = json.dumps(list(range(MAX_JSON_NODES))).encode()
    assert len(body) < MAX_MANIFEST_BYTES
    with pytest.raises(ProblemException) as captured:
        parse_manifest_bytes(body)
    assert captured.value.problem.code == "MANIFEST_INVALID"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("compression", "../../unsupported"),
        ("recorder_version", "recorder\nforged-security-log-entry"),
    ],
)
def test_manifest_rejects_unbounded_parser_selector_fields(field: str, value: str) -> None:
    payload = valid_manifest()
    payload[field] = value
    with pytest.raises(ValidationError):
        RolloutManifestV1.model_validate(payload)

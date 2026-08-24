from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.ingest.manifest import preflight_manifest
from hc_data_platform.ingest.models import RolloutManifestV1
from hc_data_platform.lance_catalog import InMemoryLanceCatalog
from hc_data_platform.quality import QualityProfileV1
from hc_data_platform.verification import McapRos2DecoderProbe
from hc_data_platform.workflow.ingest_dispatch import IngestWorkflowPlanBlocked
from hc_data_platform.workflow.ingest_plan import (
    PostgresIngestWorkflowInputResolver,
    _image_observation,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "system/wave2/data"


class _Storage:
    def open_reader(self, object_key: str) -> Any:
        assert object_key == "raw/legal.mcap"
        return (FIXTURE_ROOT / "packages/legal.mcap").open("rb")


class _Cursor:
    def __init__(self, responses: list[object]) -> None:
        self._responses = iter(responses)
        self._response: object = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _query: str, _params: tuple[object, ...]) -> None:
        self._response = next(self._responses)

    def fetchone(self) -> object:
        return self._response

    def fetchall(self) -> object:
        return self._response


class _Connection:
    def __init__(self, responses: list[object]) -> None:
        self._cursor = _Cursor(responses)
        self.closed = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _manifest() -> RolloutManifestV1:
    original = RolloutManifestV1.model_validate(
        json.loads((FIXTURE_ROOT / "manifests/legal.json").read_text())
    )
    return original.model_copy(
        update={
            "project_id": "project-a",
            "rollout_id": "rollout-a",
            "data_package_id": "package-a",
        }
    )


def _profile(manifest: RolloutManifestV1) -> QualityProfileV1:
    return QualityProfileV1(
        profile_id="manifest-30hz",
        required_topics=frozenset(manifest.expected_topics),
        action={
            "minimum_observation_count_risk": 0,
            "minimum_observation_count_reject": 0,
        },
    )


def test_resolver_builds_exact_persisted_plan_and_registers_dataset_schema() -> None:
    manifest = _manifest()
    preflight = preflight_manifest(manifest)
    plan_connection = _Connection(
        [
            (
                "raw/legal.mcap",
                "raw/rollout_manifest.json",
                manifest.sha256,
                preflight.manifest_fingerprint,
                preflight.model_dump(mode="json"),
            ),
            [(_profile(manifest).model_dump(mode="json"),)],
            [("dataset-a", "snapshot-a")],
        ]
    )
    projection_connection = _Connection(
        [
            (
                "raw/legal.mcap",
                "raw/rollout_manifest.json",
                manifest.sha256,
                preflight.manifest_fingerprint,
                preflight.model_dump(mode="json"),
            )
        ]
    )
    connections = iter((plan_connection, projection_connection))
    catalog = InMemoryLanceCatalog()
    resolver = PostgresIngestWorkflowInputResolver(
        lambda: next(connections),
        _Storage(),
        catalog,
        decoder=McapRos2DecoderProbe(),
    )

    token = bind_request_context(
        RequestContext(
            organization_id="organization-a",
            project_id="project-a",
            region_code="region-a",
            service_identity=True,
        )
    )
    try:
        request = resolver.resolve(
            project_id="project-a",
            region_code="region-a",
            session_id="session-a",
            rollout_id="rollout-a",
            data_package_id="package-a",
        )
        assert request.alignment.source is not None
        alignment = resolver.project_alignment(request.alignment.source)
    finally:
        reset_request_context(token)

    assert plan_connection.closed
    assert projection_connection.closed
    assert request.dataset_id == "dataset-a"
    assert request.organization_id == "organization-a"
    assert request.alignment.schema_snapshot_id == "snapshot-a"
    assert request.quality.profile.profile_id == "manifest-30hz"
    assert request.quality.data is None
    assert request.alignment.data is None
    assert request.quality.source == request.alignment.source
    assert request.alignment.source.organization_id == "organization-a"
    serialized = request.model_dump_json()
    assert len(serialized.encode()) < 16_384
    assert "JFIF" not in serialized
    assert set(alignment.streams) == set(manifest.actual_topics)
    assert sum(len(stream.samples) for stream in alignment.streams.values()) == 90
    camera_samples = alignment.streams["/camera/front/image"].samples
    assert camera_samples
    assert all(isinstance(sample.value, bytes) and sample.value for sample in camera_samples)
    joint_samples = alignment.streams["/joint_states"].samples
    assert joint_samples
    assert all(
        isinstance(sample.value, list)
        and len(sample.value) == 2
        and all(isinstance(item, float) for item in sample.value)
        for sample in joint_samples
    )
    assert all(
        "mcap://" not in str(sample.value)
        for topic in ("/joint_states", "/action")
        for sample in alignment.streams[topic].samples
    )
    assert ("project-a", "dataset-a") in catalog._schemas
    assert catalog._schemas[("project-a", "dataset-a")].fields["/camera/front/image"] == ("binary")
    assert catalog._schemas[("project-a", "dataset-a")].fields["/joint_states"] == "json"
    assert catalog._schemas[("project-a", "dataset-a")].fields["/action"] == "json"


def test_resolver_fails_closed_when_quality_plan_is_ambiguous() -> None:
    manifest = _manifest()
    preflight = preflight_manifest(manifest)
    profile = _profile(manifest).model_dump(mode="json")
    connection = _Connection(
        [
            (
                "raw/legal.mcap",
                "raw/rollout_manifest.json",
                manifest.sha256,
                preflight.manifest_fingerprint,
                preflight.model_dump(mode="json"),
            ),
            [(profile,), (profile,)],
        ]
    )
    resolver = PostgresIngestWorkflowInputResolver(
        lambda: connection,
        _Storage(),
        InMemoryLanceCatalog(),
    )

    token = bind_request_context(
        RequestContext(
            organization_id="organization-a",
            project_id="project-a",
            region_code="region-a",
            service_identity=True,
        )
    )
    try:
        with pytest.raises(IngestWorkflowPlanBlocked, match="exactly one quality profile"):
            resolver.resolve(
                project_id="project-a",
                region_code="region-a",
                session_id="session-a",
                rollout_id="rollout-a",
                data_package_id="package-a",
            )
    finally:
        reset_request_context(token)


def test_json_jpeg_camera_envelope_projects_into_image_qc_observation() -> None:
    output = io.BytesIO()
    Image.new("RGB", (4, 3), color=(90, 90, 90)).save(output, format="JPEG")
    encoded = output.getvalue()
    message = json.dumps(
        {
            "encoding": "jpeg",
            "width": 4,
            "height": 3,
            "jpeg_sha256": hashlib.sha256(encoded).hexdigest(),
            "data_base64": base64.b64encode(encoded).decode("ascii"),
        }
    ).encode()

    observation = _image_observation(message, timestamp_ns=123)

    assert observation.timestamp_ns == 123
    assert observation.corrupt is False
    assert observation.fingerprint == hashlib.sha256(encoded).hexdigest()
    assert observation.luma_mean == pytest.approx(90, abs=1)


def test_invalid_json_jpeg_camera_envelope_is_reported_as_corrupt() -> None:
    observation = _image_observation(
        b'{"encoding":"jpeg","data_base64":"not base64"}', timestamp_ns=456
    )

    assert observation.timestamp_ns == 456
    assert observation.corrupt is True
    assert observation.fingerprint is None

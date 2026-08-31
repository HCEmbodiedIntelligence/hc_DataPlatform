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
from hc_data_platform.lance_catalog import DatasetSchemaSnapshot, InMemoryLanceCatalog
from hc_data_platform.quality import QualityProfileV1
from hc_data_platform.verification import McapRos2DecoderProbe
from hc_data_platform.workflow.ingest_dispatch import IngestWorkflowPlanBlocked
from hc_data_platform.workflow.ingest_plan import (
    PostgresIngestWorkflowInputResolver,
    _image_observation,
)
from hc_data_platform.workflow.projection_store import LocalProjectionArtifactStore

FIXTURE_ROOT = Path(__file__).parents[1] / "system/wave2/data"


class _Storage:
    def __init__(self) -> None:
        self.open_count = 0

    def open_reader(self, object_key: str) -> Any:
        assert object_key == "raw/legal.mcap"
        self.open_count += 1
        return (FIXTURE_ROOT / "packages/legal.mcap").open("rb")


class _Cursor:
    def __init__(self, responses: list[object]) -> None:
        self._responses = iter(responses)
        self._response: object = None
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _query: str, _params: tuple[object, ...]) -> None:
        self.executions.append((_query, _params))
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


def test_resolver_localizes_raw_once_without_projection_arrow_for_quality_alignment(
    tmp_path: Path,
) -> None:
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
            ("dataset_ab",),
            [("snapshot-a",)],
        ]
    )
    projection_row = (
        "raw/legal.mcap",
        "raw/rollout_manifest.json",
        manifest.sha256,
        preflight.manifest_fingerprint,
        preflight.model_dump(mode="json"),
    )
    projection_connections = (_Connection([projection_row]),)
    connections = iter((plan_connection, *projection_connections))
    catalog = InMemoryLanceCatalog()
    storage = _Storage()
    projection_store = LocalProjectionArtifactStore(tmp_path / "projection-store")
    resolver = PostgresIngestWorkflowInputResolver(
        lambda: next(connections),
        storage,
        catalog,
        decoder=McapRos2DecoderProbe(),
        projection_store=projection_store,
        projection_staging_root=tmp_path / "projection-staging",
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
        with resolver.open_local_session(request.alignment.source) as session:
            quality = session.quality_data
            quality_observations = tuple(session.quality_observations())
            alignment = session.alignment_data
            alignment_samples = tuple(session.alignment_samples())
            selection_ref = session.frame_selection()
    finally:
        reset_request_context(token)

    assert plan_connection.closed
    assert all(connection.closed for connection in projection_connections)
    assert storage.open_count == 1
    selection = projection_store.read_json(
        selection_ref.object_key,
        expected_sha256=selection_ref.content_sha256,
    )
    assert selection["schema_version"] == 1
    assert selection["source_sha256"] == manifest.sha256
    assert selection["source_frame_count"] == selection_ref.source_frame_count
    assert len(selection["groups"]) == selection_ref.selected_group_count
    assert 0 < selection_ref.selected_group_count <= selection_ref.source_frame_count
    assert not tuple((tmp_path / "projection-store").rglob("*.arrow"))
    assert not tuple((tmp_path / "projection-staging").glob("*.mcap.part"))
    assert request.dataset_id == "dataset_ab"
    assert request.organization_id == "organization-a"
    assert request.verification.organization_id == "organization-a"
    assert request.alignment.schema_snapshot_id == "snapshot-a"
    assert request.quality.profile.profile_id == "manifest-30hz"
    assert request.quality.data is None
    assert request.alignment.data is None
    assert request.quality.source == request.alignment.source
    assert request.alignment.source.organization_id == "organization-a"
    serialized = request.model_dump_json()
    assert len(serialized.encode()) < 16_384
    assert "JFIF" not in serialized
    assert quality.topic_timestamps_ns == {}
    assert {item.topic for item in quality_observations} == set(manifest.actual_topics)
    assert any(item.is_camera for item in quality_observations)
    assert set(alignment.streams) == set(manifest.actual_topics)
    assert sum(len(stream.samples) for stream in alignment.streams.values()) == 0
    assert len(alignment_samples) == 90
    camera_samples = tuple(
        sample for topic, sample in alignment_samples if topic == "/camera/front/image"
    )
    assert camera_samples
    assert all(isinstance(sample.value, bytes) and sample.value for sample in camera_samples)
    joint_samples = tuple(sample for topic, sample in alignment_samples if topic == "/joint_states")
    assert joint_samples
    assert all(
        isinstance(sample.value, list)
        and len(sample.value) == 2
        and all(isinstance(item, float) for item in sample.value)
        for sample in joint_samples
    )
    assert all(
        "mcap://" not in str(sample.value)
        for topic, sample in alignment_samples
        if topic in ("/joint_states", "/action")
    )
    assert ("project-a", "dataset_ab") in catalog._schemas
    assert catalog._schemas[("project-a", "dataset_ab")].fields["/camera/front/image"] == "json"
    assert catalog._schemas[("project-a", "dataset_ab")].fields["/joint_states"] == "json"
    assert catalog._schemas[("project-a", "dataset_ab")].fields["/action"] == "json"
    assert plan_connection._cursor.executions[2][1] == (
        "organization-a",
        "project-a",
        manifest.task_id,
    )
    assert plan_connection._cursor.executions[3][1] == (
        "project-a",
        "region-a",
        "dataset_ab",
    )


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


def test_resolver_routes_by_manifest_task_before_validating_dataset_schema() -> None:
    manifest = _manifest()
    preflight = preflight_manifest(manifest)
    connection = _Connection(
        [
            (
                "raw/legal.mcap",
                "raw/rollout_manifest.json",
                manifest.sha256,
                preflight.manifest_fingerprint,
                preflight.model_dump(mode="json"),
            ),
            [(_profile(manifest).model_dump(mode="json"),)],
            ("dataset_mcap",),
            [("snapshot-mcap",)],
        ]
    )
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(
        DatasetSchemaSnapshot.create(
            project_id="project-a",
            dataset_id="dataset_old",
            schema_snapshot_id="snapshot-old",
            frequency_hz=30,
            fields={"/legacy/topic": "json"},
        )
    )
    resolver = PostgresIngestWorkflowInputResolver(
        lambda: connection,
        _Storage(),
        catalog,
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
    finally:
        reset_request_context(token)

    assert request.dataset_id == "dataset_mcap"
    assert request.alignment.schema_snapshot_id == "snapshot-mcap"
    assert catalog.schema_for("project-a", "dataset_mcap") is not None
    assert connection._cursor.executions[2][1] == (
        "organization-a",
        "project-a",
        manifest.task_id,
    )
    assert connection._cursor.executions[3][1] == (
        "project-a",
        "region-a",
        "dataset_mcap",
    )


def test_resolver_fails_closed_when_manifest_task_has_no_project_dataset() -> None:
    manifest = _manifest()
    preflight = preflight_manifest(manifest)
    connection = _Connection(
        [
            (
                "raw/legal.mcap",
                "raw/rollout_manifest.json",
                manifest.sha256,
                preflight.manifest_fingerprint,
                preflight.model_dump(mode="json"),
            ),
            [(_profile(manifest).model_dump(mode="json"),)],
            None,
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
            region_code="storage-region-a",
            service_identity=True,
        )
    )
    try:
        with pytest.raises(IngestWorkflowPlanBlocked, match="Manifest task"):
            resolver.resolve(
                project_id="project-a",
                region_code="storage-region-a",
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

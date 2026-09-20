from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from hc_data_platform.aligned_media.models import (  # noqa: E402
    AlignedMediaArtifactStatus,
    AlignedMediaArtifactV1,
    AlignedMediaScopeV1,
)
from hc_data_platform.alignment.models import (  # noqa: E402
    AlignmentInputV1,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from hc_data_platform.core.context import (  # noqa: E402
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import (  # noqa: E402
    normalize_postgres_dsn,
    psycopg_connection_factory,
)
from hc_data_platform.dataset_registry.ingest_projection import (  # noqa: E402
    DatasetIngestProjectionConflict,
    PostgresDatasetIngestProjector,
    _stream_kind,
    _value_kind,
)
from hc_data_platform.ingest.manifest import preflight_manifest  # noqa: E402
from hc_data_platform.ingest.models import (  # noqa: E402
    ManifestCameraV1,
    ManifestFileV1,
    ManifestTopicV1,
    RolloutManifestV1,
)
from hc_data_platform.lance_catalog.models import (  # noqa: E402
    DatasetVersionRef,
    DerivedReadyV1,
)
from hc_data_platform.workflow.models import IngestProjectionSourceV1  # noqa: E402

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 21, 6, tzinfo=timezone.utc)
REGION = "cn-ingest-viewer"


def test_unitree_object_payloads_project_as_numeric_viewer_streams() -> None:
    assert _value_kind(({"names": ["a"], "positions": [1.0]},)) == "VECTOR"
    assert _value_kind(({"names": ["a"], "values": [2.0]},)) == "VECTOR"
    assert _value_kind(({"position_xyz": [1, 2, 3], "orientation_wxyz": [1, 0, 0, 0]},)) == "VECTOR"
    assert (
        _stream_kind("/humanoid/observation/state", ModalityKind.CONTINUOUS, "VECTOR")
        == "JOINT_STATE"
    )
    assert _stream_kind("/robot/end_effector/state", ModalityKind.CONTINUOUS, "VECTOR") == "POSE"
    assert _value_kind(({"source_format": "lerobot"},)) == "EVENT"


def _database_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


@contextmanager
def _scope(organization_id: str, project_id: str) -> Iterator[None]:
    token = bind_request_context(
        RequestContext(
            organization_id=organization_id,
            project_id=project_id,
            region_code=REGION,
            subject_id="dataset-ingest-projector-test",
            request_id=str(uuid4()),
            service_identity=True,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def _manifest(
    *,
    project_id: str,
    rollout_id: str,
    data_package_id: str,
    sequence_no: int,
    source_sha256: str,
) -> RolloutManifestV1:
    return RolloutManifestV1(
        project_id=project_id,
        task_id="pick-and-place",
        collection_job_id="job-ingest-viewer",
        rollout_id=rollout_id,
        collection_session_id="collection-session-ingest-viewer",
        recording_request_id=f"request-{rollout_id}",
        data_package_id=data_package_id,
        sequence_no=sequence_no,
        robot_id="robot-ingest-viewer",
        start_time=NOW + timedelta(minutes=sequence_no),
        end_time=NOW + timedelta(minutes=sequence_no, seconds=1),
        cameras=[
            ManifestCameraV1(
                camera_id="camera-front",
                topic="/camera/front/image",
                encoding="jpeg",
            )
        ],
        topics=[
            ManifestTopicV1(name="/camera/front/image", required=True),
            ManifestTopicV1(name="/joint_states", required=True),
        ],
        expected_topics=["/camera/front/image", "/joint_states"],
        actual_topics=["/camera/front/image", "/joint_states"],
        files=[
            ManifestFileV1(
                path="captures/recording.mcap",
                size=128,
                sha256=source_sha256,
                crc64=0,
                media_type="application/x-mcap",
                role="RAW_MCAP",
            ),
            ManifestFileV1(
                path="captures/metadata.json",
                size=64,
                sha256="9" * 64,
                crc64=0,
                media_type="application/json",
                role="AUXILIARY",
            ),
        ],
        file_size=128,
        sha256=source_sha256,
        crc64=0,
        compression="none",
        recorder_version="dataset-ingest-projector-test/1",
    )


def _alignment(rollout_id: str, source_sha256: str, attempt: int) -> AlignmentInputV1:
    return AlignmentInputV1(
        rollout_id=rollout_id,
        source_sha256=source_sha256,
        attempt_id=f"attempt-{attempt}",
        start_ns=1_000_000_000,
        end_ns=2_000_000_000,
        streams={
            "/camera/front/image": ModalityStreamV1(
                kind=ModalityKind.IMAGE,
                samples=(
                    TimedSampleV1(timestamp_ns=1_000_000_000, value=b"jpeg-a"),
                    TimedSampleV1(timestamp_ns=1_500_000_000, value=b"jpeg-b"),
                ),
            ),
            "/joint_states": ModalityStreamV1(
                kind=ModalityKind.CONTINUOUS,
                samples=(
                    TimedSampleV1(timestamp_ns=1_000_000_000, value=[0.1, 0.2, 0.3]),
                    TimedSampleV1(timestamp_ns=1_500_000_000, value=[0.4, 0.5, 0.6]),
                ),
            ),
        },
    )


def _source(
    *,
    organization_id: str,
    project_id: str,
    session_id: str,
    manifest: RolloutManifestV1,
    manifest_fingerprint: str,
) -> IngestProjectionSourceV1:
    return IngestProjectionSourceV1(
        organization_id=organization_id,
        project_id=project_id,
        region_code=REGION,
        session_id=session_id,
        rollout_id=manifest.rollout_id,
        data_package_id=manifest.data_package_id,
        object_key=f"private/raw/{project_id}/{manifest.rollout_id}.mcap",
        manifest_key=f"private/raw/{project_id}/{manifest.rollout_id}/manifest.json",
        manifest_fingerprint=manifest_fingerprint,
        source_sha256=manifest.sha256,
    )


def _version(
    *,
    project_id: str,
    dataset_id: str,
    number: int,
    content_hash: str,
    storage_commit_id: str,
    rollouts: tuple[str, ...],
) -> DatasetVersionRef:
    return DatasetVersionRef(
        project_id=project_id,
        dataset_id=dataset_id,
        version=number,
        schema_snapshot_id="schema_ingest_viewer_v1",
        schema_fingerprint="c" * 64,
        frequency_hz=30,
        content_hash=content_hash,
        dataset_uri=f"lance://private/{project_id}/{dataset_id}",
        lance_version=number,
        storage_commit_id=storage_commit_id,
        committed_rollouts=rollouts,
        created_at=NOW + timedelta(minutes=number),
    )


def _ready(
    *,
    project_id: str,
    dataset_id: str,
    number: int,
    rollout_id: str,
    source_sha256: str,
    content_hash: str,
) -> DerivedReadyV1:
    return DerivedReadyV1(
        project_id=project_id,
        dataset_id=dataset_id,
        rollout_id=rollout_id,
        source_sha256=source_sha256,
        converter_version="mcap-source-reference/1",
        dataset_version=number,
        lance_version=number,
        step_count=2,
        content_hash=content_hash,
    )


def _media_artifacts(
    *,
    organization_id: str,
    manifest: RolloutManifestV1,
    ready: DerivedReadyV1,
) -> tuple[AlignedMediaArtifactV1, ...]:
    return tuple(
        AlignedMediaArtifactV1(
            artifact_id=f"media-{ready.dataset_version}-{camera.camera_id}",
            artifact_key=(f"{ready.dataset_version:x}" * 64)[:64],
            scope=AlignedMediaScopeV1(
                organization_id=organization_id,
                project_id=manifest.project_id,
                region_code=REGION,
            ),
            dataset_id=ready.dataset_id,
            rollout_id=ready.rollout_id,
            dataset_version=ready.dataset_version,
            camera_id=camera.topic,
            profile_id="canonical-h264-crf20-v1",
            profile_version="1",
            alignment_version="causal-30hz-v1",
            source_sha256=manifest.sha256,
            status=AlignedMediaArtifactStatus.READY,
            frame_count=ready.step_count,
            duration_seconds=ready.step_count / 30,
            width=1280,
            height=720,
            content_sha256="8" * 64,
            created_at=NOW,
            ready_at=NOW,
        )
        for camera in manifest.cameras
    )


def _seed_manifest(
    dsn: str,
    *,
    organization_id: str,
    project_id: str,
    dataset_id: str,
    session_id: str,
    manifest: RolloutManifestV1,
) -> str:
    preflight = preflight_manifest(manifest)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            INSERT INTO registry.organization_projects (organization_id, project_id, display_name)
            VALUES (%s, %s, 'Ingest projection integration test') ON CONFLICT DO NOTHING
            """,
            (organization_id, project_id),
        )
        connection.execute(
            """
            SELECT set_config('app.organization_id', %s, false),
                   set_config('app.project_id', %s, false),
                   set_config('app.region_code', %s, false),
                   set_config('app.subject_id', 'dataset-ingest-projection-fixture', false),
                   set_config('app.request_id', %s, false)
            """,
            (organization_id, project_id, REGION, str(uuid4())),
        )
        connection.execute(
            """
            INSERT INTO collection_tasks.collection_tasks (
                collection_task_id, organization_id, project_id, dataset_id, task_code,
                name, task_type, scenario, description, target_json, quality_threshold,
                status, version, create_fingerprint, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s,
                lpad(nextval('collection_tasks.task_code_sequence')::text, 8, '0'),
                'Ingest projection task', 'DATA_CAPTURE', 'integration', '', NULL, NULL,
                'ACTIVE', 1, %s, %s, %s
            ) ON CONFLICT (organization_id, project_id, collection_task_id) DO NOTHING
            """,
            (
                manifest.task_id,
                organization_id,
                project_id,
                dataset_id,
                "7" * 64,
                NOW,
                NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO ingest.collection_jobs (
                project_id, region_code, task_id, collection_job_id, robot_id,
                status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, 'COLLECTING', %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                project_id,
                REGION,
                manifest.task_id,
                manifest.collection_job_id,
                manifest.robot_id,
                NOW,
                NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO ingest.rollouts (
                project_id, region_code, collection_job_id, rollout_id, sequence_no,
                robot_id, source_sha256, status, created_at, updated_at,
                collection_session_id, recording_request_id, data_package_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'RAW_COMMITTED', %s, %s, %s, %s, %s)
            """,
            (
                project_id,
                REGION,
                manifest.collection_job_id,
                manifest.rollout_id,
                manifest.sequence_no,
                manifest.robot_id,
                manifest.sha256,
                NOW,
                NOW,
                manifest.collection_session_id,
                manifest.recording_request_id,
                manifest.data_package_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO ingest.upload_sessions (
                session_id, project_id, region_code, rollout_id, object_key,
                multipart_upload_id, expected_sha256, expected_size, expected_crc64,
                manifest_fingerprint, status, created_at, updated_at, completed_at,
                data_package_id, source_type
            ) VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, %s,
                      'RAW_COMMITTED', %s, %s, %s, %s, 'BROWSER_MULTIPART')
            """,
            (
                session_id,
                project_id,
                REGION,
                manifest.rollout_id,
                f"raw/{project_id}/{manifest.rollout_id}",
                manifest.sha256,
                manifest.file_size,
                manifest.crc64,
                preflight.manifest_fingerprint,
                NOW,
                NOW,
                NOW,
                manifest.data_package_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO ingest.manifest_discoveries (
                session_id, project_id, region_code, data_package_id,
                manifest_fingerprint, preflight_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                session_id,
                project_id,
                REGION,
                manifest.data_package_id,
                preflight.manifest_fingerprint,
                preflight.model_dump_json(),
                NOW,
            ),
        )
    return preflight.manifest_fingerprint


def _cleanup(dsn: str, *, organization_id: str, project_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute("SET LOCAL session_replication_role = replica")
        for table in (
            "dataset_registry.dataset_version_review_findings",
            "dataset_registry.dataset_version_successor_drafts",
            "dataset_registry.dataset_version_review_decisions",
            "dataset_registry.dataset_version_async_jobs",
            "dataset_registry.dataset_version_operational_inventory",
            "dataset_registry.dataset_version_required_storage",
            "dataset_registry.dataset_version_manifest_entries",
            "dataset_registry.dataset_version_schema_details",
            "dataset_registry.dataset_version_episode_revisions",
            "dataset_registry.dataset_version_content_projections",
            "dataset_registry.dataset_version_episodes",
            "dataset_registry.dataset_version_capacity_facts",
            "dataset_registry.dataset_version_source_provenance",
            "dataset_registry.dataset_version_schema_summaries",
            "dataset_registry.dataset_detail_facts",
            "dataset_registry.dataset_versions",
            "dataset_registry.datasets",
        ):
            connection.execute(f"DELETE FROM {table} WHERE project_id = %s", (project_id,))
        connection.execute(
            "DELETE FROM collection_tasks.collection_tasks "
            "WHERE organization_id = %s AND project_id = %s",
            (organization_id, project_id),
        )
        for table in (
            "ingest.workflow_triggers",
            "ingest.manifest_discoveries",
            "ingest.upload_parts",
            "ingest.upload_objects",
            "ingest.rollout_objects",
            "ingest.upload_sessions",
            "ingest.rollouts",
            "ingest.collection_jobs",
        ):
            connection.execute(f"DELETE FROM {table} WHERE project_id = %s", (project_id,))
        connection.execute(
            "DELETE FROM core.audit_integrity_entries WHERE project_id = %s", (project_id,)
        )
        connection.execute("DELETE FROM core.audit_events WHERE project_id = %s", (project_id,))
        connection.execute(
            "DELETE FROM core.audit_integrity_heads WHERE project_id = %s", (project_id,)
        )
        connection.execute(
            "DELETE FROM registry.organization_projects "
            "WHERE organization_id = %s AND project_id = %s",
            (organization_id, project_id),
        )


def _count(cursor: Any, table: str, project_id: str, dataset_id: str) -> int:
    cursor.execute(
        f"SELECT count(*) FROM {table} WHERE project_id = %s AND dataset_id = %s",
        (project_id, dataset_id),
    )
    return int(cursor.fetchone()[0])


def test_ingest_commit_projects_idempotent_cumulative_viewer_snapshots() -> None:
    dsn = _database_dsn()
    suffix = uuid4().hex[:12]
    organization_id = f"organization-ingest-{suffix}"
    project_id = f"project-ingest-{suffix}"
    dataset_id = f"dataset_ingest_{suffix}"
    projector = PostgresDatasetIngestProjector(psycopg_connection_factory(dsn))
    first_manifest = _manifest(
        project_id=project_id,
        rollout_id=f"rollout-{suffix}-1",
        data_package_id=f"package-{suffix}-1",
        sequence_no=1,
        source_sha256="a" * 64,
    )
    first_session = str(uuid4())
    first_fingerprint = _seed_manifest(
        dsn,
        organization_id=organization_id,
        project_id=project_id,
        dataset_id=dataset_id,
        session_id=first_session,
        manifest=first_manifest,
    )
    first_source = _source(
        organization_id=organization_id,
        project_id=project_id,
        session_id=first_session,
        manifest=first_manifest,
        manifest_fingerprint=first_fingerprint,
    )
    first_version = _version(
        project_id=project_id,
        dataset_id=dataset_id,
        number=1,
        content_hash="d" * 64,
        storage_commit_id="e" * 64,
        rollouts=(first_manifest.rollout_id,),
    )
    first_ready = _ready(
        project_id=project_id,
        dataset_id=dataset_id,
        number=1,
        rollout_id=first_manifest.rollout_id,
        source_sha256=first_manifest.sha256,
        content_hash=first_version.content_hash,
    )

    def project_first() -> object:
        with _scope(organization_id, project_id):
            return projector.project(
                source=first_source,
                alignment=_alignment(first_manifest.rollout_id, first_manifest.sha256, 1),
                schema_snapshot_id=first_version.schema_snapshot_id,
                frequency_hz=first_version.frequency_hz,
                version=first_version,
                ready=first_ready,
                media_artifacts=_media_artifacts(
                    organization_id=organization_id,
                    manifest=first_manifest,
                    ready=first_ready,
                ),
            )

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            first_targets = tuple(executor.map(lambda _index: project_first(), range(4)))
        assert len(set(first_targets)) == 1
        first_target = first_targets[0]

        second_manifest = _manifest(
            project_id=project_id,
            # A rollout ID is an identity, not a commit-order key.  This second
            # commit intentionally sorts before the first rollout.
            rollout_id=f"rollout-{suffix}-0",
            data_package_id=f"package-{suffix}-2",
            sequence_no=2,
            source_sha256="b" * 64,
        )
        second_session = str(uuid4())
        second_fingerprint = _seed_manifest(
            dsn,
            organization_id=organization_id,
            project_id=project_id,
            dataset_id=dataset_id,
            session_id=second_session,
            manifest=second_manifest,
        )
        second_source = _source(
            organization_id=organization_id,
            project_id=project_id,
            session_id=second_session,
            manifest=second_manifest,
            manifest_fingerprint=second_fingerprint,
        )
        second_version = _version(
            project_id=project_id,
            dataset_id=dataset_id,
            number=2,
            content_hash="f" * 64,
            storage_commit_id="1" * 64,
            rollouts=(first_manifest.rollout_id, second_manifest.rollout_id),
        )
        second_ready = _ready(
            project_id=project_id,
            dataset_id=dataset_id,
            number=2,
            rollout_id=second_manifest.rollout_id,
            source_sha256=second_manifest.sha256,
            content_hash=second_version.content_hash,
        )
        with _scope(organization_id, project_id):
            second_target = projector.project(
                source=second_source,
                alignment=_alignment(second_manifest.rollout_id, second_manifest.sha256, 2),
                schema_snapshot_id=second_version.schema_snapshot_id,
                frequency_hz=second_version.frequency_hz,
                version=second_version,
                ready=second_ready,
                media_artifacts=_media_artifacts(
                    organization_id=organization_id,
                    manifest=second_manifest,
                    ready=second_ready,
                ),
            )

        assert first_target.dataset_id == dataset_id
        assert first_target.version_id == "version_lance_1"
        assert second_target.version_id == "version_lance_2"
        assert second_target.episode_id != first_target.episode_id

        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            assert _count(cursor, "dataset_registry.dataset_versions", project_id, dataset_id) == 2
            cursor.execute(
                """
                SELECT version_id, count(*)
                  FROM dataset_registry.dataset_version_episodes
                 WHERE project_id = %s AND dataset_id = %s
                 GROUP BY version_id ORDER BY version_id
                """,
                (project_id, dataset_id),
            )
            assert cursor.fetchall() == [("version_lance_1", 1), ("version_lance_2", 2)]
            cursor.execute(
                """
                SELECT version_id, count(*)
                  FROM dataset_registry.dataset_version_source_provenance
                 WHERE project_id = %s AND dataset_id = %s
                 GROUP BY version_id ORDER BY version_id
                """,
                (project_id, dataset_id),
            )
            assert cursor.fetchall() == [("version_lance_1", 1), ("version_lance_2", 2)]
            cursor.execute(
                """
                SELECT capacity_document ->> 'source_bytes'
                  FROM dataset_registry.dataset_version_capacity_facts
                 WHERE project_id = %s AND dataset_id = %s AND version_id = 'version_lance_2'
                """,
                (project_id, dataset_id),
            )
            assert cursor.fetchone() == ("384",)
            cursor.execute(
                """
                SELECT revision_document
                  FROM dataset_registry.dataset_version_episode_revisions
                 WHERE project_id = %s AND dataset_id = %s AND version_id = 'version_lance_2'
                 ORDER BY ordinal
                """,
                (project_id, dataset_id),
            )
            revisions = [row[0] for row in cursor.fetchall()]
            assert len(revisions) == 2
            for revision in revisions:
                streams = {item["channel_path"]: item for item in revision["streams"]}
                assert streams["/camera/front/image"]["aligned_media_binding"]["fps"] == 30
                assert streams["/joint_states"]["data_binding"]["value_kind"] == "VECTOR"
                assert "object_key" not in str(revision)
                assert "mcap://" not in str(revision)
            cursor.execute(
                """
                SELECT count(*) FROM core.audit_events
                 WHERE project_id = %s AND action = 'dataset.ingest_version.projected'
                """,
                (project_id,),
            )
            assert cursor.fetchone() == (2,)

        conflicting_version = first_version.model_copy(update={"storage_commit_id": "2" * 64})
        with (
            _scope(organization_id, project_id),
            pytest.raises(
                DatasetIngestProjectionConflict,
                match="disagrees with the Lance commit",
            ),
        ):
            projector.project(
                source=first_source,
                alignment=_alignment(first_manifest.rollout_id, first_manifest.sha256, 1),
                schema_snapshot_id=conflicting_version.schema_snapshot_id,
                frequency_hz=conflicting_version.frequency_hz,
                version=conflicting_version,
                ready=first_ready,
                media_artifacts=_media_artifacts(
                    organization_id=organization_id,
                    manifest=first_manifest,
                    ready=first_ready,
                ),
            )
    finally:
        _cleanup(dsn, organization_id=organization_id, project_id=project_id)

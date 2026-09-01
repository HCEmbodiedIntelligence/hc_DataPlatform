from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from hc_data_platform.calibrations.models import (
    CalibrationDatasetAssociationRequest,
    CalibrationPublishPreflightRequest,
    CalibrationPublishRequest,
    CreateCalibrationSetRequest,
    RecalibrateCalibrationSetRequest,
)
from hc_data_platform.calibrations.repository import PostgresCalibrationRepository
from hc_data_platform.calibrations.service import CalibrationService
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.security.auth import AuthContext

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "p16-calibration-integration-project"
FOREIGN_PROJECT_ID = "p16-calibration-integration-foreign-project"
REGION_CODE = "p16-calibration-integration-region"
ROBOT_ID = "p16-calibration-integration-robot"
FOREIGN_ROBOT_ID = "p16-calibration-integration-foreign-robot"
COMPONENT_ID = "p16-calibration-integration-component"
SET_ID = "p16-calibration-integration-set"
ORGANIZATION_ID = "p16-calibration-integration-organization"
FOREIGN_ORGANIZATION_ID = "p16-calibration-integration-foreign-organization"
DATASET_ID = "dataset_p16calibration"
DATASET_VERSION_ID = "version_p16calibration"
APP_ROLE = "p16_calibration_reader"
APP_PASSWORD = "p16-test-only-reader-password"


def _superuser_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _app_dsn(superuser_dsn: str) -> str:
    _credentials, separator, address = superuser_dsn.rpartition("@")
    assert separator
    return f"postgresql://{APP_ROLE}:{APP_PASSWORD}@{address}"


def _drop_app_role(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,))
        if cursor.fetchone() is None:
            return
        cursor.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(APP_ROLE)))
        cursor.execute(
            psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(psycopg.sql.Identifier(APP_ROLE))
        )


def _prepare_app_role(dsn: str) -> None:
    _drop_app_role(dsn)
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER").format(
                psycopg.sql.Identifier(APP_ROLE), psycopg.sql.Literal(APP_PASSWORD)
            )
        )
        cursor.execute(
            "GRANT USAGE ON SCHEMA calibrations, robotics, dataset_registry, core TO " + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON calibrations.calibration_sets, "
            "calibrations.calibration_publish_preflights, "
            "calibrations.calibration_version_documents, "
            "calibrations.calibration_validation_reports, "
            "calibrations.calibration_command_receipts, "
            "calibrations.calibration_dataset_version_associations TO " + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT ON robotics.robot_assets, robotics.project_robot_assignments, "
            "robotics.robot_asset_components TO "
            + APP_ROLE
        )
        cursor.execute("GRANT SELECT ON dataset_registry.dataset_versions TO " + APP_ROLE)
        cursor.execute("GRANT INSERT ON core.audit_events TO " + APP_ROLE)


def _cleanup(dsn: str) -> None:
    projects = [PROJECT_ID, FOREIGN_PROJECT_ID]
    organizations = [ORGANIZATION_ID, FOREIGN_ORGANIZATION_ID]
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM core.audit_integrity_entries WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute("DELETE FROM core.audit_events WHERE project_id = ANY(%s)", (projects,))
        cursor.execute(
            "DELETE FROM core.audit_integrity_heads WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM calibrations.calibration_command_receipts WHERE project_id = ANY(%s)",
            (projects,),
        )
        cursor.execute(
            "DELETE FROM calibrations.calibration_validation_reports WHERE project_id = ANY(%s)",
            (projects,),
        )
        cursor.execute(
            "DELETE FROM calibrations.calibration_dataset_version_associations "
            "WHERE project_id = ANY(%s)",
            (projects,),
        )
        cursor.execute(
            "DELETE FROM calibrations.calibration_version_documents WHERE project_id = ANY(%s)",
            (projects,),
        )
        cursor.execute(
            "DELETE FROM calibrations.calibration_publish_preflights WHERE project_id = ANY(%s)",
            (projects,),
        )
        cursor.execute(
            "DELETE FROM calibrations.calibration_sets WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM robotics.robot_asset_components WHERE organization_id = ANY(%s)",
            (organizations,),
        )
        cursor.execute(
            "DELETE FROM robotics.project_robot_assignments WHERE project_id = ANY(%s)",
            (projects,),
        )
        cursor.execute(
            "DELETE FROM robotics.robot_assets WHERE organization_id = ANY(%s)",
            (organizations,),
        )
        cursor.execute(
            "DELETE FROM dataset_registry.dataset_versions WHERE project_id = ANY(%s)",
            (projects,),
        )
        cursor.execute(
            "DELETE FROM dataset_registry.datasets WHERE project_id = ANY(%s)",
            (projects,),
        )
        cursor.execute(
            "DELETE FROM registry.organization_projects WHERE organization_id IN (%s, %s)",
            (ORGANIZATION_ID, FOREIGN_ORGANIZATION_ID),
        )


def _seed(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO registry.organization_projects "
            "(organization_id, project_id, display_name) "
            "VALUES (%s, %s, %s), (%s, %s, %s)",
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                "P16 集成项目",
                FOREIGN_ORGANIZATION_ID,
                FOREIGN_PROJECT_ID,
                "P16 外部项目",
            ),
        )
        cursor.execute(
            """
            INSERT INTO dataset_registry.datasets (
                organization_id, project_id, region_code, dataset_id, name, description,
                labels, availability, owner_id, owner_display_name, asset_state, storage_class,
                channels, episode_count, pending_review_version_count, returned_version_count,
                actionable_draft_count, version, dataset_document, created_at, updated_at,
                activity_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, '[]'::jsonb, 'ACTIVE', %s, %s, 'READY', 'STANDARD',
                '[]'::jsonb, 0, 0, 0, 0, 1, %s::jsonb, %s, %s, %s
            )
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                DATASET_ID,
                "P16 标定关联数据集",
                "验证真实标定版本关联的固定数据集版本。",
                "p16-owner",
                "P16 Owner",
                (
                    '{"dataset_id":"'
                    + DATASET_ID
                    + '","scope":{"organization_id":"'
                    + ORGANIZATION_ID
                    + '","project_id":"'
                    + PROJECT_ID
                    + '","region_code":"'
                    + REGION_CODE
                    + '"}}'
                ),
                datetime(2026, 8, 19, 11, tzinfo=timezone.utc),
                datetime(2026, 8, 19, 11, tzinfo=timezone.utc),
                datetime(2026, 8, 19, 11, tzinfo=timezone.utc),
            ),
        )
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_versions (
                organization_id, project_id, region_code, dataset_id, version_id, display_version,
                version_kind, version_status, created_at, published_at, version_document
            ) VALUES (%s, %s, %s, %s, %s, 'P16 V1', 'RAW', 'READY', %s, %s, %s::jsonb)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                DATASET_ID,
                DATASET_VERSION_ID,
                datetime(2026, 8, 19, 11, tzinfo=timezone.utc),
                datetime(2026, 8, 19, 11, tzinfo=timezone.utc),
                (
                    '{"dataset_id":"'
                    + DATASET_ID
                    + '","version_id":"'
                    + DATASET_VERSION_ID
                    + '","kind":"RAW","status":"READY","scope":{"organization_id":"'
                    + ORGANIZATION_ID
                    + '","project_id":"'
                    + PROJECT_ID
                    + '","region_code":"'
                    + REGION_CODE
                    + '"}}'
                ),
            ),
        )
        cursor.execute(
            """
            INSERT INTO robotics.robot_assets (
                organization_id, robot_id, display_name, serial_no,
                lifecycle_status, connectivity_state, etag, topology_revision, allowed_actions
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb),
                     (%s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb)
            """,
            (
                ORGANIZATION_ID,
                ROBOT_ID,
                "P16 集成机器人",
                "P16-SN",
                "ACTIVE",
                "ONLINE",
                '"p16-robot:1"',
                "p16-topology:1",
                FOREIGN_ORGANIZATION_ID,
                FOREIGN_ROBOT_ID,
                "P16 外部机器人",
                "P16-SN-FOREIGN",
                "ACTIVE",
                "ONLINE",
                '"p16-foreign-robot:1"',
                "p16-foreign-topology:1",
            ),
        )
        cursor.execute(
            """
            INSERT INTO robotics.project_robot_assignments (
                organization_id, project_id, region_code, robot_id, assigned_by
            ) VALUES (%s, %s, %s, %s, %s), (%s, %s, %s, %s, %s)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                ROBOT_ID,
                "p16-test",
                FOREIGN_ORGANIZATION_ID,
                FOREIGN_PROJECT_ID,
                REGION_CODE,
                FOREIGN_ROBOT_ID,
                "p16-test",
            ),
        )
        cursor.execute(
            """
            INSERT INTO robotics.robot_asset_components (
                organization_id, component_id, robot_id, component_model_id,
                component_type, display_name, serial_no, lifecycle_status, sort_order
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ORGANIZATION_ID,
                COMPONENT_ID,
                ROBOT_ID,
                "p16-camera",
                "CAMERA",
                "P16 相机",
                "P16-CAM",
                "ACTIVE",
                0,
            ),
        )
        cursor.execute(
            """
            INSERT INTO calibrations.calibration_sets (
                organization_id, project_id, region_code, set_id, robot_instance_id,
                component_id, version,
                snapshot_status, availability, content_hash, validation_context_hash,
                validation_status, validation_content_hash, validation_report_id, etag,
                allowed_actions, blocked_reasons
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                '[]'::jsonb, '[]'::jsonb
            )
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                SET_ID,
                ROBOT_ID,
                COMPONENT_ID,
                1,
                "DRAFT",
                None,
                "a" * 64,
                "b" * 64,
                "PASSED",
                "a" * 64,
                "p16-report",
                '"p16-set:1"',
            ),
        )


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    dsn = _superuser_dsn()
    # Execute the exact production migration manifest twice. This verifies full
    # composition and repeatability before a restricted role receives any facts.
    asyncio.run(apply_migrations(dsn))
    asyncio.run(apply_migrations(dsn))
    _cleanup(dsn)
    _prepare_app_role(dsn)
    _seed(dsn)
    try:
        yield dsn
    finally:
        _cleanup(dsn)
        _drop_app_role(dsn)


def _auth(
    project_id: str = PROJECT_ID,
    *,
    can_publish: bool = False,
    can_dataset_read: bool = False,
) -> AuthContext:
    organization_id = (
        FOREIGN_ORGANIZATION_ID if project_id == FOREIGN_PROJECT_ID else ORGANIZATION_ID
    )
    return AuthContext(
        subject_id="p16-reader",
        project_ids=frozenset({project_id}),
        organization_ids=frozenset({organization_id}),
        region_codes=frozenset({REGION_CODE}),
        scope_pairs=frozenset({(project_id, REGION_CODE)}),
        organization_scope_triples=frozenset({(organization_id, project_id, REGION_CODE)}),
        scoped_capabilities=(
            frozenset({(project_id, "calibration.read")})
            | (frozenset({(project_id, "calibration.publish")}) if can_publish else frozenset())
            | (frozenset({(project_id, "dataset.read")}) if can_dataset_read else frozenset())
        ),
        organization_scoped_capabilities=(
            frozenset({(organization_id, project_id, "calibration.read")})
            | (
                frozenset({(organization_id, project_id, "calibration.publish")})
                if can_publish
                else frozenset()
            )
            | (
                frozenset({(organization_id, project_id, "dataset.read")})
                if can_dataset_read
                else frozenset()
            )
        ),
    )


def _service(dsn: str) -> CalibrationService:
    return CalibrationService(
        PostgresCalibrationRepository(psycopg_connection_factory(_app_dsn(dsn))),
        clock=lambda: datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
    )


@contextmanager
def _within_request(project_id: str = PROJECT_ID, region_code: str = REGION_CODE) -> Iterator[None]:
    organization_id = (
        FOREIGN_ORGANIZATION_ID if project_id == FOREIGN_PROJECT_ID else ORGANIZATION_ID
    )
    token = bind_request_context(
        RequestContext(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            subject_id="p16-reader",
            request_id="p16-postgres-integration",
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def test_postgres_calibration_reads_are_rls_scoped_and_audited_with_non_superuser(
    postgres_dsn: str,
) -> None:
    service = _service(postgres_dsn)
    with _within_request():
        assert [
            item.id
            for item in service.list_sets(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                query="P16",
                robot_id=ROBOT_ID,
                component_id=COMPONENT_ID,
                request_id="p16-list",
            ).items
        ] == [SET_ID]
        assert (
            service.get_set(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                set_id=SET_ID,
                request_id="p16-detail",
            ).data.validation
            is not None
        )
        preflight = service.preflight_publish(
            auth=_auth(can_publish=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=SET_ID,
            version="1",
            expected_etag='"p16-set:1"',
            idempotency_key="p16-publish",
            request_id="p16-preflight",
            command=CalibrationPublishPreflightRequest(
                expected_hash="a" * 64,
                expected_etag='"p16-set:1"',
                validation_report_id="p16-report",
                change_summary="P16 RLS 发布证明",
                validation_context_hash="b" * 64,
            ),
        )
        assert preflight.data.allowed is True
        assert preflight.data.preflight_token is not None
        published = service.publish(
            auth=_auth(can_publish=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=SET_ID,
            version="1",
            expected_etag='"p16-set:1"',
            idempotency_key="p16-publish",
            request_id="p16-publish",
            command=CalibrationPublishRequest(
                preflight_token=preflight.data.preflight_token,
            ),
        )
        assert published.data.snapshot_status == "READY"
        assert published.data.availability == "ACTIVE"

        connection = psycopg_connection_factory(_app_dsn(postgres_dsn))()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_id FROM calibrations.calibration_sets ORDER BY set_id")
                assert cursor.fetchall() == [(SET_ID,)]
        finally:
            connection.close()

    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM core.audit_events WHERE project_id = %s", (PROJECT_ID,)
        )
        assert cursor.fetchone()[0] == 5
        cursor.execute(
            "SELECT status, consumed_at IS NOT NULL "
            "FROM calibrations.calibration_publish_preflights "
            "WHERE project_id = %s AND set_id = %s",
            (PROJECT_ID, SET_ID),
        )
        assert cursor.fetchone() == ("CONSUMED", True)

    with _within_request(), pytest.raises(ProblemException) as foreign:
        service.list_sets(
            auth=_auth(),
            project_id=FOREIGN_PROJECT_ID,
            region_code=REGION_CODE,
            query=None,
            robot_id=None,
            component_id=None,
            request_id="p16-foreign",
        )
    assert foreign.value.problem.status == 403
    assert foreign.value.problem.code == "PROJECT_SCOPE_DENIED"

    with _within_request(region_code="p16-calibration-integration-foreign-region"):
        connection = psycopg_connection_factory(_app_dsn(postgres_dsn))()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_id FROM calibrations.calibration_sets")
                assert cursor.fetchall() == []
        finally:
            connection.close()


def test_postgres_calibration_document_create_validate_and_report_are_rls_scoped(
    postgres_dsn: str,
) -> None:
    service = _service(postgres_dsn)
    command = CreateCalibrationSetRequest(
        set_id="p16-calibration-document-v1",
        robot_instance_id=ROBOT_ID,
        component_id=COMPONENT_ID,
        source="IMPORT",
        document={
            "frame_transforms": [
                {
                    "parent_frame": "base_link",
                    "child_frame": "camera_front",
                    "translation_m": [0.11, 0.02, 0.43],
                    "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
                }
            ],
            "camera_intrinsics": [
                {
                    "frame_id": "camera_front",
                    "width_px": 1280,
                    "height_px": 720,
                    "fx_px": 704.0,
                    "fy_px": 703.0,
                    "cx_px": 640.0,
                    "cy_px": 360.0,
                }
            ],
        },
    )
    with _within_request():
        created = service.create_set(
            auth=_auth(can_publish=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            command=command,
            idempotency_key="p16-document-create",
            request_id="p16-document-create",
        )
        assert created.data.snapshot_status == "DRAFT"
        assert created.data.content_hash is not None
        document = service.get_version_document(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            version="1",
            request_id="p16-document-read",
        )
        assert document.data.document.frame_transforms[0].translation_m == (0.11, 0.02, 0.43)
        validated = service.validate_version(
            auth=_auth(can_publish=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            version="1",
            expected_etag=created.data.etag,
            idempotency_key="p16-document-validate",
            request_id="p16-document-validate",
        )
        assert validated.data.status == "PASSED"
        assert validated.data.findings == ()
        replay = service.validate_version(
            auth=_auth(can_publish=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            version="1",
            expected_etag=created.data.etag,
            idempotency_key="p16-document-validate",
            request_id="p16-document-validate-replay",
        )
        assert replay.data == validated.data
        report = service.get_validation_report(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            report_id=validated.data.id,
            request_id="p16-document-report-read",
        )
        assert report.data.content_hash == created.data.content_hash
        validated_set = service.get_set(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            request_id="p16-document-after-validate",
        )
        assert created.data.content_hash is not None
        preflight = service.preflight_publish(
            auth=_auth(can_publish=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            version="1",
            expected_etag=validated_set.data.etag,
            idempotency_key="p16-document-publish",
            request_id="p16-document-preflight",
            command=CalibrationPublishPreflightRequest(
                expected_hash=created.data.content_hash,
                expected_etag=validated_set.data.etag,
                validation_report_id=validated.data.id,
                change_summary="将已验证的标定版本固定关联到真实数据集版本。",
                validation_context_hash=validated.data.validation_context_hash,
            ),
        )
        assert preflight.data.allowed is True
        assert preflight.data.preflight_token is not None
        published = service.publish(
            auth=_auth(can_publish=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            version="1",
            expected_etag=validated_set.data.etag,
            idempotency_key="p16-document-publish",
            request_id="p16-document-publish",
            command=CalibrationPublishRequest(preflight_token=preflight.data.preflight_token),
        )
        association = service.associate_dataset(
            auth=_auth(can_publish=True, can_dataset_read=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            version="1",
            expected_etag=published.data.etag,
            idempotency_key="p16-document-dataset-association",
            request_id="p16-document-dataset-association",
            command=CalibrationDatasetAssociationRequest(
                dataset_id=DATASET_ID,
                dataset_version_id=DATASET_VERSION_ID,
            ),
        )
        assert association.data.dataset_id == DATASET_ID
        assert association.data.calibration_version == "1"
        assert service.list_dataset_associations(
            auth=_auth(can_dataset_read=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            version="1",
            request_id="p16-document-dataset-association-list",
        ).items == (association.data,)
        with pytest.raises(ProblemException) as missing_dataset_permission:
            service.list_dataset_associations(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                set_id=command.set_id,
                version="1",
                request_id="p16-document-dataset-association-denied",
            )
        assert missing_dataset_permission.value.problem.code == "CAPABILITY_REQUIRED"
        successor = service.recalibrate_set(
            auth=_auth(can_publish=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            expected_etag=published.data.etag,
            idempotency_key="p16-document-recalibrate",
            request_id="p16-document-recalibrate",
            command=RecalibrateCalibrationSetRequest(
                change_summary="以新一轮实测外参创建不可变后继版本。",
                document={
                    "frame_transforms": [
                        {
                            "parent_frame": "base_link",
                            "child_frame": "camera_front",
                            "translation_m": [0.14, 0.02, 0.45],
                            "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
                        }
                    ],
                    "camera_intrinsics": [
                        {
                            "frame_id": "camera_front",
                            "width_px": 1280,
                            "height_px": 720,
                            "fx_px": 704.0,
                            "fy_px": 703.0,
                            "cx_px": 640.0,
                            "cy_px": 360.0,
                        }
                    ],
                },
            ),
        )
        assert successor.data.version == "2"
        assert successor.data.validation is None
        history = service.list_versions(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            request_id="p16-document-history",
        )
        assert [(item.version, item.source) for item in history.items] == [
            ("2", "RECALIBRATION"),
            ("1", "IMPORT"),
        ]
        original = service.get_version_document(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            set_id=command.set_id,
            version="1",
            request_id="p16-document-original",
        )
        assert original.data.document.frame_transforms[0].translation_m == (0.11, 0.02, 0.43)

    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT source, document -> 'frame_transforms' -> 0 ->> 'child_frame', content_hash "
            "FROM calibrations.calibration_version_documents "
            "WHERE project_id = %s AND set_id = %s AND version = 1",
            (PROJECT_ID, command.set_id),
        )
        assert cursor.fetchone()[0:2] == ("IMPORT", "camera_front")
        cursor.execute(
            "SELECT status, jsonb_array_length(findings) "
            "FROM calibrations.calibration_validation_reports "
            "WHERE project_id = %s AND set_id = %s",
            (PROJECT_ID, command.set_id),
        )
        assert cursor.fetchone() == ("PASSED", 0)
        cursor.execute(
            "SELECT dataset_id, dataset_version_id, calibration_version, associated_by "
            "FROM calibrations.calibration_dataset_version_associations "
            "WHERE project_id = %s AND set_id = %s",
            (PROJECT_ID, command.set_id),
        )
        assert cursor.fetchone() == (DATASET_ID, DATASET_VERSION_ID, 1, "p16-reader")

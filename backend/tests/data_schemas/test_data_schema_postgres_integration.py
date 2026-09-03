from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.data_schemas.models import (
    CreateStreamSchemaRequest,
    DataSchemaDatasetReferenceRequest,
    DataSchemaPublishPreflightRequest,
    DataSchemaPublishRequest,
    StreamSchemaDefinition,
)
from hc_data_platform.data_schemas.repository import PostgresDataSchemaRepository
from hc_data_platform.data_schemas.service import DataSchemaService
from hc_data_platform.security.auth import AuthContext

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "p17-schema-integration-project"
FOREIGN_PROJECT_ID = "p17-schema-integration-foreign-project"
REGION_CODE = "p17-schema-integration-region"
ORGANIZATION_ID = "p17-schema-integration-organization"
FOREIGN_ORGANIZATION_ID = "p17-schema-integration-foreign-organization"
ROBOT_ID = "p17-schema-integration-robot"
FOREIGN_ROBOT_ID = "p17-schema-integration-foreign-robot"
COMPONENT_ID = "p17-schema-integration-component"
FOREIGN_COMPONENT_ID = "p17-schema-integration-foreign-component"
SCHEMA_ID = "p17-schema-integration-image"
FOREIGN_SCHEMA_ID = "p17-schema-integration-foreign-image"
CHANNEL_ID = "p17-schema-integration-channel"
FOREIGN_CHANNEL_ID = "p17-schema-integration-foreign-channel"
DATASET_ID = "dataset_p17schemareference"
DATASET_VERSION_ID = "version_p17schemareference"
APP_ROLE = "p17_schema_reader"
APP_PASSWORD = "p17-test-only-reader-password"


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
            "GRANT USAGE ON SCHEMA registry, robotics, data_schemas, dataset_registry, core TO "
            + APP_ROLE
        )
        cursor.execute("GRANT SELECT ON registry.organization_projects TO " + APP_ROLE)
        cursor.execute(
            "GRANT SELECT ON robotics.robot_components, robotics.component_channels TO " + APP_ROLE
        )
        cursor.execute("GRANT SELECT ON data_schemas.stream_schema_versions TO " + APP_ROLE)
        cursor.execute("GRANT INSERT, UPDATE ON data_schemas.stream_schema_versions TO " + APP_ROLE)
        cursor.execute(
            "GRANT SELECT, INSERT ON data_schemas.stream_schema_dataset_version_references TO "
            + APP_ROLE
        )
        cursor.execute("GRANT SELECT, UPDATE ON dataset_registry.dataset_versions TO " + APP_ROLE)
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON data_schemas.schema_validation_reports, "
            "data_schemas.schema_publish_preflights, data_schemas.schema_command_receipts TO "
            + APP_ROLE
        )
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
            "DELETE FROM data_schemas.stream_schema_dataset_version_references "
            "WHERE organization_id = ANY(%s)",
            (organizations,),
        )
        cursor.execute(
            "DELETE FROM data_schemas.schema_command_receipts WHERE organization_id = ANY(%s)",
            (organizations,),
        )
        cursor.execute(
            "DELETE FROM data_schemas.schema_publish_preflights WHERE organization_id = ANY(%s)",
            (organizations,),
        )
        cursor.execute(
            "DELETE FROM data_schemas.schema_validation_reports WHERE organization_id = ANY(%s)",
            (organizations,),
        )
        cursor.execute(
            "DELETE FROM data_schemas.stream_schema_versions WHERE organization_id = ANY(%s)",
            (organizations,),
        )
        cursor.execute(
            "DELETE FROM dataset_registry.dataset_versions WHERE project_id = ANY(%s)",
            (projects,),
        )
        cursor.execute(
            "DELETE FROM dataset_registry.datasets WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM robotics.component_channels WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM robotics.robot_components WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM robotics.robot_instances WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM registry.organization_projects WHERE organization_id = ANY(%s)",
            (organizations,),
        )


def _seed(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO registry.organization_projects (organization_id, project_id)
            VALUES (%s, %s), (%s, %s), (%s, %s)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                ORGANIZATION_ID,
                FOREIGN_PROJECT_ID,
                FOREIGN_ORGANIZATION_ID,
                FOREIGN_PROJECT_ID,
            ),
        )
        cursor.execute(
            """
            INSERT INTO robotics.robot_instances (
                organization_id, project_id, region_code, robot_id, display_name, serial_no,
                lifecycle_status, connectivity_state, etag, topology_revision, allowed_actions
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb),
                     (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                ROBOT_ID,
                "P17 集成机器人",
                "P17-SN-01",
                "ACTIVE",
                "ONLINE",
                '"p17-robot:1"',
                "p17-topology:1",
                FOREIGN_ORGANIZATION_ID,
                FOREIGN_PROJECT_ID,
                REGION_CODE,
                FOREIGN_ROBOT_ID,
                "P17 外部机器人",
                "P17-SN-FOREIGN",
                "ACTIVE",
                "ONLINE",
                '"p17-foreign-robot:1"',
                "p17-foreign-topology:1",
            ),
        )
        now = datetime(2026, 8, 19, 11, tzinfo=timezone.utc)
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
                "P17 Schema 引用数据集",
                "验证固定已发布 Schema 版本的真实数据集引用。",
                "p17-owner",
                "P17 Owner",
                json.dumps(
                    {
                        "dataset_id": DATASET_ID,
                        "scope": {
                            "organization_id": ORGANIZATION_ID,
                            "project_id": PROJECT_ID,
                            "region_code": REGION_CODE,
                        },
                    }
                ),
                now,
                now,
                now,
            ),
        )
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_versions (
                organization_id, project_id, region_code, dataset_id, version_id, display_version,
                version_kind, version_status, created_at, published_at, version_document
            ) VALUES (%s, %s, %s, %s, %s, 'P17 V1', 'RAW', 'READY', %s, %s, %s::jsonb)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                DATASET_ID,
                DATASET_VERSION_ID,
                now,
                now,
                json.dumps(
                    {
                        "dataset_id": DATASET_ID,
                        "version_id": DATASET_VERSION_ID,
                        "kind": "RAW",
                        "status": "READY",
                        "scope": {
                            "organization_id": ORGANIZATION_ID,
                            "project_id": PROJECT_ID,
                            "region_code": REGION_CODE,
                        },
                    }
                ),
            ),
        )
        cursor.execute(
            """
            INSERT INTO robotics.robot_components (
                organization_id, project_id, region_code, component_id, robot_id,
                component_model_id,
                component_type, display_name, serial_no, lifecycle_status, sort_order
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                     (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                COMPONENT_ID,
                ROBOT_ID,
                "p17-camera-model",
                "CAMERA",
                "P17 相机",
                "P17-CAM-01",
                "ACTIVE",
                0,
                FOREIGN_ORGANIZATION_ID,
                FOREIGN_PROJECT_ID,
                REGION_CODE,
                FOREIGN_COMPONENT_ID,
                FOREIGN_ROBOT_ID,
                "p17-foreign-camera-model",
                "CAMERA",
                "P17 外部相机",
                "P17-CAM-FOREIGN",
                "ACTIVE",
                0,
            ),
        )
        cursor.execute(
            """
            INSERT INTO data_schemas.stream_schema_versions (
                organization_id, schema_id, schema_version, family_id, display_name, logical_type,
                status, compatibility_mode, compatibility_result, hash_algorithm,
                canonicalization_version, hash_value, schema_definition, etag,
                allowed_actions, blocked_reasons
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                '["VIEW"]'::jsonb, '[]'::jsonb
            ), (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                '["VIEW"]'::jsonb, '[]'::jsonb
            )
            """,
            (
                ORGANIZATION_ID,
                SCHEMA_ID,
                1,
                "p17-image-family",
                "P17 前视图像 Schema",
                "IMAGE",
                "PUBLISHED",
                "BACKWARD",
                "BACKWARD",
                "SHA-256",
                "schema-c14n-v1",
                "a" * 64,
                json.dumps(
                    {
                        "fields": [
                            {
                                "name": "image",
                                "type": "bytes",
                                "description": "P17 integration image payload",
                            }
                        ]
                    }
                ),
                '"p17-schema:1"',
                FOREIGN_ORGANIZATION_ID,
                FOREIGN_SCHEMA_ID,
                1,
                "p17-foreign-image-family",
                "P17 外部图像 Schema",
                "IMAGE",
                "PUBLISHED",
                "BACKWARD",
                "BACKWARD",
                "SHA-256",
                "schema-c14n-v1",
                "b" * 64,
                json.dumps(
                    {
                        "fields": [
                            {
                                "name": "foreign_image",
                                "type": "bytes",
                                "description": "foreign payload",
                            }
                        ]
                    }
                ),
                '"p17-foreign-schema:1"',
            ),
        )
        cursor.execute(
            """
            INSERT INTO robotics.component_channels (
                organization_id, project_id, region_code, channel_id, component_id, canonical_path,
                display_name, modality, schema_id, schema_version, role, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                     (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                CHANNEL_ID,
                COMPONENT_ID,
                "/camera/front/image",
                "P17 图像",
                "IMAGE",
                SCHEMA_ID,
                1,
                "PRIMARY",
                "ACTIVE",
                FOREIGN_ORGANIZATION_ID,
                FOREIGN_PROJECT_ID,
                REGION_CODE,
                FOREIGN_CHANNEL_ID,
                FOREIGN_COMPONENT_ID,
                "/camera/foreign/image",
                "P17 外部图像",
                "IMAGE",
                FOREIGN_SCHEMA_ID,
                1,
                "PRIMARY",
                "ACTIVE",
            ),
        )


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    dsn = _superuser_dsn()
    # Run exactly the production manifest twice: P17 relies on both registry's
    # organization/project RLS and P15's component/channel RLS.
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
    project_id: str = PROJECT_ID, *, capabilities: frozenset[str] | None = None
) -> AuthContext:
    resolved_capabilities = (
        capabilities
        if capabilities is not None
        else frozenset(
            {
                "data_schema.read",
                "data_schema.create",
                "data_schema.import",
                "data_schema.validate",
                "data_schema.publish",
            }
        )
    )
    return AuthContext(
        subject_id="p17-schema-integration-reader",
        organization_ids=frozenset({ORGANIZATION_ID}),
        project_ids=frozenset({project_id}),
        region_codes=frozenset({REGION_CODE}),
        scope_pairs=frozenset({(project_id, None), (project_id, REGION_CODE)}),
        scoped_capabilities=frozenset(
            (project_id, capability) for capability in resolved_capabilities
        ),
        organization_scope_triples=frozenset(
            {
                (ORGANIZATION_ID, project_id, None),
                (ORGANIZATION_ID, project_id, REGION_CODE),
            }
        ),
        organization_scoped_capabilities=frozenset(
            (ORGANIZATION_ID, project_id, capability) for capability in resolved_capabilities
        ),
    )


def _service(dsn: str) -> DataSchemaService:
    return DataSchemaService(
        PostgresDataSchemaRepository(psycopg_connection_factory(_app_dsn(dsn))),
        clock=lambda: datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
    )


@contextmanager
def _within_request(
    project_id: str = PROJECT_ID,
    region_code: str = REGION_CODE,
    organization_id: str = ORGANIZATION_ID,
) -> Iterator[None]:
    token = bind_request_context(
        RequestContext(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            subject_id="p17-schema-integration-reader",
            request_id="p17-schema-postgres-integration",
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def test_postgres_data_schema_reads_and_route_resolution_are_rls_scoped_and_audited(
    postgres_dsn: str,
) -> None:
    service = _service(postgres_dsn)
    with _within_request():
        assert [
            item.schema_id
            for item in service.list_versions(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                query="P17",
                status=None,
                logical_type=None,
                after=None,
                before=None,
                limit=20,
                request_id="p17-list",
            ).items
        ] == [SCHEMA_ID]
        assert (
            service.get_version(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                schema_id=SCHEMA_ID,
                schema_version="1",
                request_id="p17-detail",
            ).data.schema_hash.value
            == "a" * 64
        )
        assert (
            service.resolve_route(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                schema_id=SCHEMA_ID,
                schema_version="1",
                component_id=COMPONENT_ID,
                detail_tab="references",
                request_id="p17-route",
            ).data.scope.organization_id
            == ORGANIZATION_ID
        )
        # This non-superuser can query all tables used by P17, but its connection
        # factory scope limits the organization/project relationship and P15 channel.
        connection = psycopg_connection_factory(_app_dsn(postgres_dsn))()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT organization_id, project_id
                      FROM registry.organization_projects
                     WHERE organization_id = %s
                     ORDER BY project_id
                    """,
                    (ORGANIZATION_ID,),
                )
                assert cursor.fetchall() == [(ORGANIZATION_ID, PROJECT_ID)]
                cursor.execute(
                    "SELECT channel_id FROM robotics.component_channels ORDER BY channel_id"
                )
                assert cursor.fetchall() == [(CHANNEL_ID,)]
        finally:
            connection.close()

    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM core.audit_events WHERE project_id = %s", (PROJECT_ID,)
        )
        assert cursor.fetchone()[0] == 3

    with _within_request(), pytest.raises(ProblemException) as foreign_project:
        service.list_versions(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=FOREIGN_PROJECT_ID,
            query=None,
            status=None,
            logical_type=None,
            after=None,
            before=None,
            limit=20,
            request_id="p17-foreign-project",
        )
    assert foreign_project.value.problem.code == "PROJECT_SCOPE_DENIED"

    with _within_request(), pytest.raises(ProblemException) as foreign_organization:
        service.list_versions(
            auth=_auth(),
            organization_id=FOREIGN_ORGANIZATION_ID,
            project_id=PROJECT_ID,
            query=None,
            status=None,
            logical_type=None,
            after=None,
            before=None,
            limit=20,
            request_id="p17-foreign-organization",
        )
    assert foreign_organization.value.problem.code == "ORGANIZATION_SCOPE_DENIED"

    with _within_request(region_code="p17-schema-integration-foreign-region"):
        connection = psycopg_connection_factory(_app_dsn(postgres_dsn))()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT channel_id FROM robotics.component_channels")
                assert cursor.fetchall() == []
        finally:
            connection.close()


def test_postgres_data_schema_list_uses_filtered_keyset_windows(
    postgres_dsn: str,
) -> None:
    page_records = (
        ("p17-pagination-alpha", "P17 Pagination Alpha", "PUBLISHED", "c" * 64),
        ("p17-pagination-bravo", "P17 Pagination Bravo", "DRAFT", "d" * 64),
        ("p17-pagination-charlie", "P17 Pagination Charlie", "PUBLISHED", "e" * 64),
        ("p17-pagination-delta", "P17 Pagination Delta", "DRAFT", "f" * 64),
    )
    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO data_schemas.stream_schema_versions (
                organization_id, schema_id, schema_version, family_id, display_name, logical_type,
                status, compatibility_mode, compatibility_result, hash_algorithm,
                canonicalization_version, hash_value, schema_definition, etag,
                allowed_actions, blocked_reasons
            ) VALUES (
                %s, %s, 1, %s, %s, 'IMAGE', %s, 'BACKWARD', NULL, 'SHA-256',
                'schema-c14n-v1', %s, %s::jsonb, %s, '[]'::jsonb, '[]'::jsonb
            )
            """,
            [
                (
                    ORGANIZATION_ID,
                    schema_id,
                    "p17-pagination-family",
                    display_name,
                    schema_status,
                    hash_value,
                    json.dumps(
                        {
                            "fields": [
                                {
                                    "name": "payload",
                                    "type": "bytes",
                                    "description": "pagination integration payload",
                                }
                            ]
                        }
                    ),
                    f'"{schema_id}:1"',
                )
                for schema_id, display_name, schema_status, hash_value in page_records
            ],
        )

    service = _service(postgres_dsn)
    with _within_request():
        first = service.list_versions(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            query="P17 Pagination",
            status=None,
            logical_type="IMAGE",
            after=None,
            before=None,
            limit=2,
            request_id="p17-pagination-first",
        )
        assert [item.schema_id for item in first.items] == [
            "p17-pagination-alpha",
            "p17-pagination-bravo",
        ]
        assert first.page_info.has_next_page is True
        assert first.page_info.end_cursor

        second = service.list_versions(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            query="P17 Pagination",
            status=None,
            logical_type="IMAGE",
            after=first.page_info.end_cursor,
            before=None,
            limit=2,
            request_id="p17-pagination-second",
        )
        assert [item.schema_id for item in second.items] == [
            "p17-pagination-charlie",
            "p17-pagination-delta",
        ]
        assert second.page_info.has_previous_page is True
        assert second.page_info.start_cursor

        previous = service.list_versions(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            query="P17 Pagination",
            status=None,
            logical_type="IMAGE",
            after=None,
            before=second.page_info.start_cursor,
            limit=2,
            request_id="p17-pagination-previous",
        )
        assert [item.schema_id for item in previous.items] == [
            "p17-pagination-alpha",
            "p17-pagination-bravo",
        ]

        published = service.list_versions(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            query="P17 Pagination",
            status="PUBLISHED",
            logical_type="IMAGE",
            after=None,
            before=None,
            limit=20,
            request_id="p17-pagination-published",
        )
        assert [item.schema_id for item in published.items] == [
            "p17-pagination-alpha",
            "p17-pagination-charlie",
        ]


def test_postgres_data_schema_dataset_reference_is_durable_and_requires_ready_dataset(
    postgres_dsn: str,
) -> None:
    service = _service(postgres_dsn)
    command = {
        "dataset_id": DATASET_ID,
        "dataset_version_id": DATASET_VERSION_ID,
    }
    with _within_request():
        created = service.associate_dataset_reference(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            schema_id=SCHEMA_ID,
            schema_version="1",
            expected_etag='"p17-schema:1"',
            command=DataSchemaDatasetReferenceRequest.model_validate(command),
            idempotency_key="p17-reference-create",
            request_id="p17-reference-create",
        )
        assert created.data.dataset_id == DATASET_ID
        assert created.data.associated_by == "p17-schema-integration-reader"

        replay = service.associate_dataset_reference(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            schema_id=SCHEMA_ID,
            schema_version="1",
            expected_etag='"p17-schema:1"',
            command=DataSchemaDatasetReferenceRequest.model_validate(command),
            idempotency_key="p17-reference-create",
            request_id="p17-reference-replay",
        )
        assert replay.data == created.data

        listed = service.list_dataset_references(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            schema_id=SCHEMA_ID,
            schema_version="1",
            request_id="p17-reference-list",
        )
        assert [reference.dataset_id for reference in listed.items] == [DATASET_ID]

        with pytest.raises(ProblemException) as missing_dataset:
            service.associate_dataset_reference(
                auth=_auth(),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                schema_id=SCHEMA_ID,
                schema_version="1",
                expected_etag='"p17-schema:1"',
                command=DataSchemaDatasetReferenceRequest(
                    dataset_id="dataset_missingreference",
                    dataset_version_id="version_missingreference",
                ),
                idempotency_key="p17-reference-missing",
                request_id="p17-reference-missing",
            )
    assert missing_dataset.value.problem.code == "DATASET_VERSION_REFERENCE_INVALID"

    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT dataset_id, dataset_version_id, associated_by
              FROM data_schemas.stream_schema_dataset_version_references
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND schema_id = %s AND schema_version = 1
            """,
            (ORGANIZATION_ID, PROJECT_ID, REGION_CODE, SCHEMA_ID),
        )
        assert cursor.fetchall() == [
            (DATASET_ID, DATASET_VERSION_ID, "p17-schema-integration-reader")
        ]
        cursor.execute(
            """
            SELECT count(*)
              FROM core.audit_events
             WHERE project_id = %s AND region_code = %s
               AND action = 'data_schema.dataset_reference.created'
            """,
            (PROJECT_ID, REGION_CODE),
        )
        assert cursor.fetchone()[0] == 1


def test_postgres_data_schema_authoring_validation_preflight_and_publish_are_durable(
    postgres_dsn: str,
) -> None:
    service = _service(postgres_dsn)
    command = CreateStreamSchemaRequest(
        schema_id="p17-schema-integration-authoring",
        family_id="p17-schema-integration-authoring-family",
        display_name="P17 可写图像 Schema",
        logical_type="IMAGE",
        compatibility_mode="BACKWARD",
        schema_definition=StreamSchemaDefinition(
            fields=({"name": "image", "type": "bytes", "description": "payload"},)
        ),
        change_summary="create durable P17 schema",
    )
    with _within_request():
        created = service.create_version(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            command=command,
            source="MANUAL",
            idempotency_key="p17-create-authoring",
            request_id="p17-create-authoring",
        )
        assert created.data.status == "DRAFT"
        report = service.validate_version(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            schema_id=command.schema_id,
            schema_version="1",
            expected_etag=created.data.etag,
            idempotency_key="p17-validate-authoring",
            request_id="p17-validate-authoring",
        )
        assert report.data.status == "PASSED"
        assert report.data.compatibility_result == "PASSED"
        preflight = service.preflight_publish(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            schema_id=command.schema_id,
            schema_version="1",
            expected_etag=created.data.etag,
            command=DataSchemaPublishPreflightRequest(
                expected_hash=created.data.schema_hash.value,
                expected_etag=created.data.etag,
                validation_report_id=report.data.id,
                compatibility_check_id=report.data.compatibility_check_id,
                change_summary="publish approved P17 schema",
            ),
            idempotency_key="p17-preflight-authoring",
            request_id="p17-preflight-authoring",
        )
        assert preflight.data.allowed is True
        assert preflight.data.preflight_token is not None
        published = service.publish_version(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            schema_id=command.schema_id,
            schema_version="1",
            expected_etag=created.data.etag,
            command=DataSchemaPublishRequest(preflight_token=preflight.data.preflight_token),
            idempotency_key="p17-publish-authoring",
            request_id="p17-publish-authoring",
        )
        assert published.data.status == "PUBLISHED"
        replay = service.publish_version(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            schema_id=command.schema_id,
            schema_version="1",
            expected_etag=created.data.etag,
            command=DataSchemaPublishRequest(preflight_token=preflight.data.preflight_token),
            idempotency_key="p17-publish-authoring",
            request_id="p17-publish-authoring-replay",
        )
        assert replay.data == published.data

    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, revision, published_by
              FROM data_schemas.stream_schema_versions
             WHERE organization_id = %s AND schema_id = %s AND schema_version = 1
            """,
            (ORGANIZATION_ID, command.schema_id),
        )
        assert cursor.fetchone() == ("PUBLISHED", 2, "p17-schema-integration-reader")
        cursor.execute(
            "SELECT count(*) FROM data_schemas.schema_validation_reports "
            "WHERE organization_id = %s AND schema_id = %s",
            (ORGANIZATION_ID, command.schema_id),
        )
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT count(*) FROM data_schemas.schema_publish_preflights "
            "WHERE organization_id = %s AND schema_id = %s AND status = 'CONSUMED'",
            (ORGANIZATION_ID, command.schema_id),
        )
        assert cursor.fetchone()[0] == 1

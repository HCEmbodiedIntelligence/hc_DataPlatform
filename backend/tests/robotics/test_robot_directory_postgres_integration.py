from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Barrier, Event

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.robotics.models import (
    ComponentLifecycleTransitionRequest,
    CreateMaintenanceRecordRequest,
    CreateRobotComponentRequest,
    CreateRobotRequest,
    RobotLifecycleTransitionRequest,
    UpdateRobotComponentRequest,
    UpdateRobotRequest,
)
from hc_data_platform.robotics.repository import PostgresRoboticsRepository
from hc_data_platform.robotics.service import RoboticsService
from hc_data_platform.security.auth import AuthContext

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "p15-robotics-integration-project"
FOREIGN_PROJECT_ID = "p15-robotics-integration-foreign-project"
ORGANIZATION_ID = "p15-robotics-integration-organization"
FOREIGN_ORGANIZATION_ID = "p15-robotics-integration-foreign-organization"
REGION_CODE = "p15-robotics-integration-region"
ROBOT_ID = "p15-robotics-integration-robot"
FOREIGN_ROBOT_ID = "p15-robotics-integration-foreign-robot"
SAME_PROJECT_FOREIGN_ROBOT_ID = "p15-robotics-integration-same-project-foreign-robot"
SEARCH_ROBOT_FIRST_ID = "p15-robotics-search-a"
SEARCH_ROBOT_SECOND_ID = "p15-robotics-search-b"
COMPONENT_ID = "p15-robotics-integration-component"
APP_ROLE = "p15_robotics_reader"
APP_PASSWORD = "p15-test-only-reader-password"
COMPONENT_TRIGGER_ADVISORY_KEY = 815_201_507


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
        cursor.execute("GRANT USAGE ON SCHEMA robotics, core TO " + APP_ROLE)
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON robotics.robot_instances, robotics.robot_components, "
            "robotics.component_frames, robotics.component_channels TO " + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON robotics.robot_maintenance_records, "
            "robotics.robot_command_receipts TO " + APP_ROLE
        )
        cursor.execute("GRANT INSERT ON core.audit_events TO " + APP_ROLE)


def _cleanup(dsn: str) -> None:
    projects = [PROJECT_ID, FOREIGN_PROJECT_ID]
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM core.audit_integrity_entries WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute("DELETE FROM core.audit_events WHERE project_id = ANY(%s)", (projects,))
        cursor.execute(
            "DELETE FROM core.audit_integrity_heads WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM robotics.robot_command_receipts WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM robotics.robot_maintenance_records WHERE project_id = ANY(%s)",
            (projects,),
        )
        cursor.execute(
            "DELETE FROM robotics.component_channels WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM robotics.component_frames WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM robotics.robot_components WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM robotics.robot_instances WHERE project_id = ANY(%s)", (projects,)
        )
        cursor.execute(
            "DELETE FROM registry.organization_projects "
            "WHERE (organization_id, project_id) IN ((%s, %s), (%s, %s), (%s, %s))",
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                FOREIGN_ORGANIZATION_ID,
                PROJECT_ID,
                FOREIGN_ORGANIZATION_ID,
                FOREIGN_PROJECT_ID,
            ),
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
                FOREIGN_ORGANIZATION_ID,
                PROJECT_ID,
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
                     (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb),
                     (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                ROBOT_ID,
                "P15 集成机器人",
                "P15-SN-01",
                "ACTIVE",
                "ONLINE",
                '"p15-robot:1"',
                "p15-topology:1",
                FOREIGN_ORGANIZATION_ID,
                FOREIGN_PROJECT_ID,
                REGION_CODE,
                FOREIGN_ROBOT_ID,
                "P15 外部机器人",
                "P15-SN-FOREIGN",
                "ACTIVE",
                "ONLINE",
                '"p15-foreign-robot:1"',
                "p15-foreign-topology:1",
                FOREIGN_ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                SAME_PROJECT_FOREIGN_ROBOT_ID,
                "P15 同项目外部机器人",
                "P15-SN-SAME-PROJECT-FOREIGN",
                "ACTIVE",
                "ONLINE",
                '"p15-same-project-foreign-robot:1"',
                "p15-same-project-foreign-topology:1",
            ),
        )
        cursor.execute(
            """
            INSERT INTO robotics.robot_components (
                organization_id, project_id, region_code, component_id, robot_id,
                component_model_id,
                component_type, display_name, serial_no, lifecycle_status, sort_order
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                COMPONENT_ID,
                ROBOT_ID,
                "p15-camera-model",
                "CAMERA",
                "P15 相机",
                "P15-CAM-01",
                "ACTIVE",
                0,
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
                SEARCH_ROBOT_FIRST_ID,
                "搜索演示 A",
                "SEARCH-SN-A",
                "ACTIVE",
                "ONLINE",
                '"p15-search-a:1"',
                "p15-search-a-topology:1",
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                SEARCH_ROBOT_SECOND_ID,
                "搜索演示 B",
                "SEARCH-SN-B",
                "ACTIVE",
                "ONLINE",
                '"p15-search-b:1"',
                "p15-search-b-topology:1",
            ),
        )
        cursor.execute(
            """
            INSERT INTO robotics.component_frames (
                organization_id, project_id, region_code, frame_id, component_id, name, source,
                calibration_set_id, status, valid_from
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                "p15-frame",
                COMPONENT_ID,
                "camera_link",
                "CALIBRATION",
                "p15-calibration",
                "ACTIVE",
                datetime(2026, 8, 1, tzinfo=timezone.utc),
            ),
        )
        cursor.execute(
            """
            INSERT INTO robotics.component_channels (
                organization_id, project_id, region_code, channel_id, component_id, canonical_path,
                display_name, modality, schema_id, schema_version, role, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                "p15-channel",
                COMPONENT_ID,
                "/camera/front/image",
                "P15 图像",
                "IMAGE",
                "p15-image-schema",
                1,
                "PRIMARY",
                "ACTIVE",
            ),
        )


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    dsn = _superuser_dsn()
    # Production composes robotics with every current module migration. Execute the
    # full manifest twice to preserve both composition and repeatability coverage.
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


def _auth(project_id: str = PROJECT_ID, *, can_manage: bool = False) -> AuthContext:
    organization_id = (
        FOREIGN_ORGANIZATION_ID if project_id == FOREIGN_PROJECT_ID else ORGANIZATION_ID
    )
    return AuthContext(
        subject_id="p15-robotics-integration-reader",
        project_ids=frozenset({project_id}),
        organization_ids=frozenset({organization_id}),
        region_codes=frozenset({REGION_CODE}),
        roles=frozenset(),
        scope_pairs=frozenset({(project_id, REGION_CODE)}),
        organization_scope_triples=frozenset({(organization_id, project_id, REGION_CODE)}),
        scoped_capabilities=(
            frozenset({(project_id, "robot.read")})
            | (frozenset({(project_id, "robot.manage")}) if can_manage else frozenset())
        ),
        organization_scoped_capabilities=(
            frozenset({(organization_id, project_id, "robot.read")})
            | (
                frozenset({(organization_id, project_id, "robot.manage")})
                if can_manage
                else frozenset()
            )
        ),
    )


def _service(dsn: str) -> RoboticsService:
    return RoboticsService(
        PostgresRoboticsRepository(psycopg_connection_factory(_app_dsn(dsn))),
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
            subject_id="p15-robotics-integration-reader",
            request_id="p15-robotics-integration-request",
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def test_postgres_robot_directory_is_rls_scoped_and_audited_with_non_superuser(
    postgres_dsn: str,
) -> None:
    service = _service(postgres_dsn)
    with _within_request():
        assert [
            item.id
            for item in service.list_robots(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                query="P15",
                lifecycle_status=None,
                connectivity_state=None,
                request_id="p15-list",
            ).items
        ] == [ROBOT_ID]
        assert [
            item.id
            for item in service.list_robots(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                query=None,
                lifecycle_status="MAINTENANCE",
                connectivity_state="ONLINE",
                request_id="p15-filtered-list",
            ).items
        ] == []
        assert (
            service.robot_bootstrap(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                robot_id=ROBOT_ID,
                request_id="p15-bootstrap",
            ).data.etag
            == '"p15-robot:1"'
        )
        assert [
            item.id
            for item in service.components(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                robot_id=ROBOT_ID,
                request_id="p15-components",
            ).items
        ] == [COMPONENT_ID]
        assert (
            service.frames(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                component_id=COMPONENT_ID,
                request_id="p15-frames",
            )
            .items[0]
            .calibration_set_id
            == "p15-calibration"
        )
        assert (
            service.channels(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                component_id=COMPONENT_ID,
                request_id="p15-channels",
            )
            .items[0]
            .schema_id
            == "p15-image-schema"
        )

        # The production connection factory selects this request scope before the
        # non-superuser queries the RLS-protected relation.
        connection = psycopg_connection_factory(_app_dsn(postgres_dsn))()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT robot_id FROM robotics.robot_instances ORDER BY robot_id")
                assert cursor.fetchall() == [
                    (ROBOT_ID,),
                    (SEARCH_ROBOT_FIRST_ID,),
                    (SEARCH_ROBOT_SECOND_ID,),
                ]
        finally:
            connection.close()

    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM core.audit_events WHERE project_id = %s", (PROJECT_ID,)
        )
        assert cursor.fetchone()[0] == 6

    with _within_request(), pytest.raises(ProblemException) as foreign:
        service.list_robots(
            auth=_auth(),
            project_id=FOREIGN_PROJECT_ID,
            region_code=REGION_CODE,
            query=None,
            lifecycle_status=None,
            connectivity_state=None,
            request_id="p15-foreign",
        )
    assert foreign.value.problem.status == 403
    assert foreign.value.problem.code == "PROJECT_SCOPE_DENIED"

    # Region is also an RLS boundary even when the selected project matches.
    with _within_request(region_code="p15-robotics-integration-foreign-region"):
        connection = psycopg_connection_factory(_app_dsn(postgres_dsn))()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT robot_id FROM robotics.robot_instances")
                assert cursor.fetchall() == []
        finally:
            connection.close()


def test_postgres_global_robot_search_is_keyset_paged_rls_scoped_and_redacted(
    postgres_dsn: str,
) -> None:
    """The shell search uses the RLS repository, not an in-memory fallback."""

    service = _service(postgres_dsn)
    with _within_request():
        first = service.search_robots(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            query="搜索演示",
            cursor=None,
            limit=1,
            request_id="p15-search-first",
        )
        assert [item.robot.id for item in first.items] == [SEARCH_ROBOT_FIRST_ID]
        assert first.page_info.has_next_page is True
        assert first.page_info.end_cursor is not None

        second = service.search_robots(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            query="搜索演示",
            cursor=first.page_info.end_cursor,
            limit=1,
            request_id="p15-search-second",
        )
        assert [item.robot.id for item in second.items] == [SEARCH_ROBOT_SECOND_ID]
        assert second.page_info.has_previous_page is True
        assert second.page_info.has_next_page is False

        # The foreign row deliberately has a matching ``P15`` name. RLS and the
        # caller's exact project/region scope must keep it out of this result.
        local = service.search_robots(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            query="P15",
            cursor=None,
            limit=10,
            request_id="p15-search-rls",
        )
        assert [item.robot.id for item in local.items] == [ROBOT_ID]

    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT action, details ->> 'entity_type', details ->> 'has_next_page',
                   details ->> 'query_length', details ->> 'result_count', details::text
              FROM core.audit_events
             WHERE project_id = %s AND request_id = %s
            """,
            (PROJECT_ID, "p15-search-first"),
        )
        action, entity_type, has_next_page, query_length, result_count, raw_details = (
            cursor.fetchone()
        )
        assert (action, entity_type, has_next_page, query_length, result_count) == (
            "robot.search.executed",
            "ROBOT",
            "true",
            "4",
            "1",
        )
        assert "搜索演示" not in raw_details

    # The application role can append through the trigger, but still cannot
    # inspect or invoke the integrity chain directly.
    with (
        psycopg.connect(_app_dsn(postgres_dsn), autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute("SELECT action FROM core.audit_events")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute(
                "SELECT core.append_audit_integrity_entry(%s::uuid)",
                ("00000000-0000-0000-0000-000000000000",),
            )


def test_postgres_robot_management_is_durable_idempotent_and_compare_and_swap_protected(
    postgres_dsn: str,
) -> None:
    service = _service(postgres_dsn)
    with _within_request():
        created = service.create_robot(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            command=CreateRobotRequest(
                display_name="P15 新建机器人",
                serial_no="P15-SN-NEW",
                lifecycle_status="DRAFT",
                connectivity_state="OFFLINE",
            ),
            idempotency_key="p15-create-new",
            request_id="p15-create-new",
        )
        robot_id = created.data.robot.id
        assert created.data.robot.lifecycle_status == "DRAFT"
        assert (
            service.create_robot(
                auth=_auth(can_manage=True),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                command=CreateRobotRequest(
                    display_name="P15 新建机器人",
                    serial_no="P15-SN-NEW",
                    lifecycle_status="DRAFT",
                    connectivity_state="OFFLINE",
                ),
                idempotency_key="p15-create-new",
                request_id="p15-create-new-replay",
            ).data.robot.id
            == robot_id
        )
        updated = service.update_robot(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            robot_id=robot_id,
            expected_etag=created.data.etag,
            command=UpdateRobotRequest(
                display_name="P15 已更新机器人", connectivity_state="ONLINE"
            ),
            idempotency_key="p15-update-new",
            request_id="p15-update-new",
        )
        assert updated.data.etag != created.data.etag
        with pytest.raises(ProblemException) as stale:
            service.update_robot(
                auth=_auth(can_manage=True),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                robot_id=robot_id,
                expected_etag=created.data.etag,
                command=UpdateRobotRequest(display_name="P15 不应写入"),
                idempotency_key="p15-update-stale",
                request_id="p15-update-stale",
            )
        assert stale.value.problem.code == "ROBOT_ETAG_MISMATCH"
        activated = service.transition_robot(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            robot_id=robot_id,
            expected_etag=updated.data.etag,
            command=RobotLifecycleTransitionRequest(
                lifecycle_status="ACTIVE", reason="P15 集成验收"
            ),
            idempotency_key="p15-transition-active",
            request_id="p15-transition-active",
        )
        assert activated.data.robot.lifecycle_status == "ACTIVE"
        manual = service.create_maintenance_record(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            robot_id=robot_id,
            command=CreateMaintenanceRecordRequest(
                summary="P15 维护记录", details="真实 PostgreSQL"
            ),
            idempotency_key="p15-maintenance-new",
            request_id="p15-maintenance-new",
        )
        assert manual.data.robot_id == robot_id
        history = service.maintenance_records(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            robot_id=robot_id,
            request_id="p15-maintenance-list",
        )
        assert {item.event_type for item in history.items} == {
            "LIFECYCLE_TRANSITION",
            "MAINTENANCE",
        }

    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT lifecycle_status, connectivity_state FROM robotics.robot_instances "
            "WHERE project_id = %s AND region_code = %s AND robot_id = %s",
            (PROJECT_ID, REGION_CODE, robot_id),
        )
        assert cursor.fetchone() == ("ACTIVE", "ONLINE")
        cursor.execute(
            "SELECT count(*) FROM robotics.robot_command_receipts "
            "WHERE project_id = %s AND resource_id = %s",
            (PROJECT_ID, robot_id),
        )
        assert cursor.fetchone()[0] == 4
        cursor.execute(
            "SELECT action FROM core.audit_events WHERE project_id = %s AND resource_id = %s",
            (PROJECT_ID, robot_id),
        )
        assert {row[0] for row in cursor.fetchall()} >= {
            "robot.created",
            "robot.updated",
            "robot.lifecycle.transitioned",
            "robot.maintenance.recorded",
        }


def test_postgres_component_management_is_rls_scoped_and_preserves_reference_history(
    postgres_dsn: str,
) -> None:
    service = _service(postgres_dsn)
    with _within_request():
        created = service.create_component(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            robot_id=ROBOT_ID,
            expected_robot_etag='"p15-robot:1"',
            command=CreateRobotComponentRequest(
                component_model_id="p15-arm-model",
                component_type="ARM",
                display_name="P15 主机械臂",
                serial_no="P15-ARM-01",
                lifecycle_status="DRAFT",
                sort_order=1,
            ),
            idempotency_key="p15-component-create",
            request_id="p15-component-create",
        )
        component_id = created.data.component.id
        assert component_id.startswith("component-")
        assert (
            service.create_component(
                auth=_auth(can_manage=True),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                robot_id=ROBOT_ID,
                expected_robot_etag='"p15-robot:1"',
                command=CreateRobotComponentRequest(
                    component_model_id="p15-arm-model",
                    component_type="ARM",
                    display_name="P15 主机械臂",
                    serial_no="P15-ARM-01",
                    lifecycle_status="DRAFT",
                    sort_order=1,
                ),
                idempotency_key="p15-component-create",
                request_id="p15-component-create-replay",
            ).data.component.id
            == component_id
        )
        updated = service.update_component(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            component_id=component_id,
            expected_robot_etag=created.data.robot_etag,
            command=UpdateRobotComponentRequest(display_name="P15 主机械臂 A"),
            idempotency_key="p15-component-update",
            request_id="p15-component-update",
        )
        assert updated.data.component.display_name == "P15 主机械臂 A"
        with pytest.raises(ProblemException) as stale:
            service.update_component(
                auth=_auth(can_manage=True),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                component_id=component_id,
                expected_robot_etag=created.data.robot_etag,
                command=UpdateRobotComponentRequest(display_name="P15 不能写入"),
                idempotency_key="p15-component-stale",
                request_id="p15-component-stale",
            )
        assert stale.value.problem.code == "ROBOT_ETAG_MISMATCH"
        active = service.transition_component(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            component_id=component_id,
            expected_robot_etag=updated.data.robot_etag,
            command=ComponentLifecycleTransitionRequest(
                lifecycle_status="ACTIVE", reason="P15 安装验收"
            ),
            idempotency_key="p15-component-active",
            request_id="p15-component-active",
        )
        child = service.create_component(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            robot_id=ROBOT_ID,
            expected_robot_etag=active.data.robot_etag,
            command=CreateRobotComponentRequest(
                parent_component_id=component_id,
                component_model_id="p15-gripper-model",
                component_type="GRIPPER",
                display_name="P15 末端夹具",
                serial_no="P15-GRIP-01",
            ),
            idempotency_key="p15-component-child",
            request_id="p15-component-child",
        )
        with pytest.raises(ProblemException) as parent_blocked:
            service.transition_component(
                auth=_auth(can_manage=True),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                component_id=component_id,
                expected_robot_etag=child.data.robot_etag,
                command=ComponentLifecycleTransitionRequest(
                    lifecycle_status="RETIRED", reason="子组件仍在拓扑中"
                ),
                idempotency_key="p15-component-parent-retire-blocked",
                request_id="p15-component-parent-retire-blocked",
            )
        assert parent_blocked.value.problem.code == "ROBOT_COMPONENT_TOPOLOGY_CONFLICT"
        child_retired = service.transition_component(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            component_id=child.data.component.id,
            expected_robot_etag=child.data.robot_etag,
            command=ComponentLifecycleTransitionRequest(
                lifecycle_status="RETIRED", reason="P15 夹具拆除"
            ),
            idempotency_key="p15-component-child-retire",
            request_id="p15-component-child-retire",
        )
        retired = service.transition_component(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            component_id=component_id,
            expected_robot_etag=child_retired.data.robot_etag,
            command=ComponentLifecycleTransitionRequest(
                lifecycle_status="RETIRED", reason="P15 主机械臂退役"
            ),
            idempotency_key="p15-component-parent-retire",
            request_id="p15-component-parent-retire",
        )
        assert retired.data.component.lifecycle_status == "RETIRED"

        # The existing camera remains referentially intact after a soft lifecycle
        # change: Frame/Channel facts are never cascaded away by a component removal.
        existing = service.transition_component(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            component_id=COMPONENT_ID,
            expected_robot_etag=retired.data.robot_etag,
            command=ComponentLifecycleTransitionRequest(
                lifecycle_status="RETIRED", reason="P15 保留历史标定事实"
            ),
            idempotency_key="p15-camera-retire",
            request_id="p15-camera-retire",
        )
        assert existing.data.component.lifecycle_status == "RETIRED"
        assert (
            service.frames(
                auth=_auth(),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                component_id=COMPONENT_ID,
                request_id="p15-retired-camera-frames",
            )
            .items[0]
            .id
            == "p15-frame"
        )

    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM robotics.robot_components "
            "WHERE project_id = %s AND robot_id = %s AND lifecycle_status = 'RETIRED'",
            (PROJECT_ID, ROBOT_ID),
        )
        assert cursor.fetchone()[0] >= 3
        cursor.execute(
            "SELECT count(*) FROM robotics.robot_maintenance_records "
            "WHERE project_id = %s AND component_id = %s "
            "AND event_type = 'COMPONENT_LIFECYCLE_TRANSITION'",
            (PROJECT_ID, component_id),
        )
        assert cursor.fetchone()[0] == 2
        cursor.execute(
            "SELECT action FROM core.audit_events WHERE project_id = %s AND resource_id = %s",
            (PROJECT_ID, component_id),
        )
        assert {row[0] for row in cursor.fetchall()} >= {
            "robot.component.created",
            "robot.component.updated",
            "robot.component.lifecycle.transitioned",
        }


def test_postgres_component_compare_and_swap_overlaps_at_the_real_row_lock(
    postgres_dsn: str,
) -> None:
    """Concurrent topology writes have one durable winner and one stale result."""

    service = _service(postgres_dsn)
    with _within_request():
        bootstrap = service.robot_bootstrap(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            robot_id=ROBOT_ID,
            request_id="p15-component-concurrent-bootstrap",
        )
        created = service.create_component(
            auth=_auth(can_manage=True),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            robot_id=ROBOT_ID,
            expected_robot_etag=bootstrap.data.etag,
            command=CreateRobotComponentRequest(
                component_model_id="p15-concurrency-model",
                component_type="SENSOR",
                display_name="P15 并发传感器",
                serial_no="P15-CONCURRENT-01",
            ),
            idempotency_key="p15-component-concurrent-create",
            request_id="p15-component-concurrent-create",
        )

    component_id = created.data.component.id
    expected_etag = created.data.robot_etag
    writers_ready = Event()
    write_gate = Barrier(2, action=writers_ready.set)

    def attempt(display_name: str) -> tuple[str, str | int]:
        token = bind_request_context(
            RequestContext(
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                subject_id=f"p15-component-concurrent-{display_name}",
                request_id=f"p15-component-concurrent-{display_name}",
            )
        )
        try:
            write_gate.wait(timeout=3)
            updated = _service(postgres_dsn).update_component(
                auth=_auth(can_manage=True),
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
                component_id=component_id,
                expected_robot_etag=expected_etag,
                command=UpdateRobotComponentRequest(display_name=display_name),
                idempotency_key=f"p15-component-concurrent-{display_name}",
                request_id=f"p15-component-concurrent-{display_name}",
            )
            return ("success", updated.data.robot_etag)
        except ProblemException as exc:
            return (exc.problem.code, exc.problem.status)
        finally:
            reset_request_context(token)

    outcomes: list[tuple[str, str | int]] = []
    writers_waiting = False
    waiting_activity: list[tuple[object, ...]] = []
    try:
        with psycopg.connect(postgres_dsn) as lock_connection, lock_connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE OR REPLACE FUNCTION robotics.p15_test_component_lock_trigger()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    PERFORM pg_advisory_xact_lock(815201507);
                    RETURN NEW;
                END;
                $$
                """
            )
            cursor.execute(
                """
                CREATE TRIGGER p15_test_component_lock_trigger
                BEFORE UPDATE ON robotics.robot_components
                FOR EACH ROW EXECUTE FUNCTION robotics.p15_test_component_lock_trigger()
                """
            )
            lock_connection.commit()
            cursor.execute("SELECT pg_advisory_lock(%s)", (COMPONENT_TRIGGER_ADVISORY_KEY,))
            lock_connection.commit()

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(attempt, display_name)
                    for display_name in ("P15 并发写入 A", "P15 并发写入 B")
                ]
                assert writers_ready.wait(timeout=3), (
                    "component writers never reached the write path"
                )
                try:
                    deadline = time.monotonic() + 3
                    with (
                        psycopg.connect(postgres_dsn) as monitor_connection,
                        monitor_connection.cursor() as monitor,
                    ):
                        while True:
                            monitor.execute(
                                """
                                SELECT count(*)
                                  FROM pg_stat_activity
                                 WHERE usename = %s AND wait_event_type = 'Lock'
                                """,
                                (APP_ROLE,),
                            )
                            if monitor.fetchone()[0] >= 2:
                                writers_waiting = True
                                break
                            if time.monotonic() >= deadline:
                                monitor.execute(
                                    """
                                    SELECT usename, state, wait_event_type, wait_event, query
                                      FROM pg_stat_activity
                                     WHERE datname = current_database()
                                     ORDER BY pid
                                    """
                                )
                                waiting_activity = list(monitor.fetchall())
                                break
                            time.sleep(0.02)
                finally:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        (COMPONENT_TRIGGER_ADVISORY_KEY,),
                    )
                    lock_connection.commit()
                outcomes = [future.result(timeout=3) for future in futures]
    finally:
        with (
            psycopg.connect(postgres_dsn) as cleanup_connection,
            cleanup_connection.cursor() as cursor,
        ):
            cursor.execute(
                "DROP TRIGGER IF EXISTS p15_test_component_lock_trigger "
                "ON robotics.robot_components"
            )
            cursor.execute("DROP FUNCTION IF EXISTS robotics.p15_test_component_lock_trigger()")

    assert writers_waiting, (
        f"component writers did not overlap at PostgreSQL locks: {outcomes}; "
        f"activity={waiting_activity}"
    )
    assert sum(outcome[0] == "success" for outcome in outcomes) == 1
    assert outcomes.count(("ROBOT_ETAG_MISMATCH", 412)) == 1
    with _within_request():
        components = _service(postgres_dsn).components(
            auth=_auth(),
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            robot_id=ROBOT_ID,
            request_id="p15-component-concurrent-readback",
        )
    component = next(item for item in components.items if item.id == component_id)
    assert component.display_name in {"P15 并发写入 A", "P15 并发写入 B"}

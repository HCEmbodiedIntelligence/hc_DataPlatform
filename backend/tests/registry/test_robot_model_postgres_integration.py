from __future__ import annotations

import asyncio
import hashlib
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
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.registry.models import (
    BindRobotModelVersionRequest,
    CompleteRobotModelAssetFileRequest,
    CreateRobotModelAssetUploadRequest,
    PublishRobotModelVersionRequest,
    ReplaceRobotModelJointMappingsRequest,
    RobotAssetCompletedPart,
    RobotAssetRole,
    RobotJointDirection,
    RobotModelAssetUploadFileRequest,
    RobotModelJointMapping,
    RobotModelVersion,
)
from hc_data_platform.registry.repository import ConnectionFactory, PostgresRegistryRepository
from hc_data_platform.registry.service import RegistryService
from hc_data_platform.security.auth import AuthContext

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

PROJECT_ID = "p14-registry-integration-project"
FOREIGN_PROJECT_ID = "p14-registry-integration-foreign-project"
ORGANIZATION_ID = "p14-registry-integration-organization"
MODEL_ID = "p14-registry-integration-model"
VERSION_ID = "p14-registry-integration-version"
DRAFT_VERSION_ID = "p14-registry-integration-draft-version"
CONCURRENT_DRAFT_VERSION_ID = "p14-registry-integration-concurrent-draft-version"
ROBOT_ID = "p14-registry-integration-robot"
REGION_CODE = "p14-region"
APP_ROLE = "p14_registry_reader"
APP_PASSWORD = "p14-registry-reader-test-password"
MAPPING_TRIGGER_ADVISORY_KEY = 814_201_947


def _superuser_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _app_dsn(superuser_dsn: str) -> str:
    _credentials, separator, address = superuser_dsn.rpartition("@")
    assert separator
    return f"postgresql://{APP_ROLE}:{APP_PASSWORD}@{address}"


def _drop_role(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,))
        if cursor.fetchone() is None:
            return
        cursor.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(APP_ROLE)))
        cursor.execute(
            psycopg.sql.SQL("DROP ROLE IF EXISTS {}").format(psycopg.sql.Identifier(APP_ROLE))
        )


def _prepare_app_role(dsn: str) -> None:
    _drop_role(dsn)
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER").format(
                psycopg.sql.Identifier(APP_ROLE), psycopg.sql.Literal(APP_PASSWORD)
            )
        )
        cursor.execute("GRANT USAGE ON SCHEMA registry TO " + APP_ROLE)
        cursor.execute("GRANT USAGE ON SCHEMA robotics TO " + APP_ROLE)
        cursor.execute("GRANT SELECT, UPDATE ON registry.robot_model_versions TO " + APP_ROLE)
        cursor.execute(
            "GRANT SELECT, UPDATE ON registry.organization_projects, registry.robot_models TO "
            + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE ON registry.robot_model_asset_uploads, "
            "registry.robot_model_asset_upload_files, registry.robot_model_assets TO " + APP_ROLE
        )
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON registry.robot_model_joint_mappings, "
            "registry.robot_model_publish_preflights, registry.robot_model_command_receipts, "
            "registry.robot_model_bindings TO " + APP_ROLE
        )
        cursor.execute("GRANT SELECT, UPDATE ON robotics.robot_instances TO " + APP_ROLE)
        cursor.execute("GRANT INSERT ON registry.audit_events TO " + APP_ROLE)
        cursor.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA registry TO " + APP_ROLE)


def _cleanup(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM registry.audit_events WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )
        cursor.execute(
            "DELETE FROM registry.robot_model_assets WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )
        cursor.execute(
            "DELETE FROM registry.robot_model_publish_preflights WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )
        cursor.execute(
            "DELETE FROM registry.robot_model_bindings WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )
        cursor.execute(
            "DELETE FROM registry.robot_model_command_receipts WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )
        cursor.execute(
            "DELETE FROM registry.robot_model_joint_mappings WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )
        cursor.execute(
            "DELETE FROM registry.robot_model_asset_uploads WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )
        cursor.execute(
            "DELETE FROM registry.robot_model_versions WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )
        cursor.execute(
            "DELETE FROM robotics.robot_instances WHERE project_id IN (%s, %s)",
            (PROJECT_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            "DELETE FROM registry.robot_models WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )
        cursor.execute(
            "DELETE FROM registry.organization_projects WHERE organization_id = %s",
            (ORGANIZATION_ID,),
        )


def _seed(dsn: str) -> None:
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO registry.organization_projects (organization_id, project_id)
            VALUES (%s, %s), (%s, %s)
            """,
            (ORGANIZATION_ID, PROJECT_ID, ORGANIZATION_ID, FOREIGN_PROJECT_ID),
        )
        cursor.execute(
            """
            INSERT INTO registry.robot_models (
                organization_id, model_id, manufacturer, model_code, display_name
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (ORGANIZATION_ID, MODEL_ID, "HC Robotics", "P14-01", "P14 集成模型"),
        )
        cursor.execute(
            """
            INSERT INTO registry.robot_model_versions (
                organization_id, version_id, robot_model_id, version_label, lifecycle,
                asset_availability, publish_readiness, asset_manifest_hash,
                validation_input_hash, etag, allowed_actions, blocked_reasons
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb, '[]'::jsonb)
            """,
            (
                ORGANIZATION_ID,
                VERSION_ID,
                MODEL_ID,
                "1.0.0",
                "PUBLISHED",
                "AVAILABLE",
                "READY",
                "a" * 64,
                "b" * 64,
                '"p14-registry-integration-version:1"',
            ),
        )
        cursor.execute(
            """
            INSERT INTO robotics.robot_instances (
                organization_id, project_id, region_code, robot_id, display_name, serial_no,
                lifecycle_status,
                connectivity_state, etag, topology_revision, allowed_actions
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '[]'::jsonb)
            """,
            (
                ORGANIZATION_ID,
                PROJECT_ID,
                REGION_CODE,
                ROBOT_ID,
                "P14 integration robot",
                "P14-ROBOT-01",
                "ACTIVE",
                "ONLINE",
                '"p14-robot:1"',
                "p14-topology:1",
            ),
        )
        cursor.execute(
            """
            UPDATE registry.robot_models
               SET current_published_version_id = %s
             WHERE organization_id = %s AND model_id = %s
            """,
            (VERSION_ID, ORGANIZATION_ID, MODEL_ID),
        )
        cursor.execute(
            """
            INSERT INTO registry.robot_model_versions (
                organization_id, version_id, robot_model_id, version_label, lifecycle,
                asset_availability, publish_readiness, asset_manifest_hash,
                validation_input_hash, etag, allowed_actions, blocked_reasons
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s, '[]'::jsonb, '[]'::jsonb)
            """,
            (
                ORGANIZATION_ID,
                DRAFT_VERSION_ID,
                MODEL_ID,
                "1.1.0-rc.1",
                "DRAFT",
                "MISSING",
                "CONFIGURATION_REQUIRED",
                '"p14-registry-integration-draft:1"',
            ),
        )
        cursor.execute(
            """
            INSERT INTO registry.robot_model_versions (
                organization_id, version_id, robot_model_id, version_label, lifecycle,
                asset_availability, publish_readiness, asset_manifest_hash,
                validation_input_hash, etag, allowed_actions, blocked_reasons
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s, '[]'::jsonb, '[]'::jsonb)
            """,
            (
                ORGANIZATION_ID,
                CONCURRENT_DRAFT_VERSION_ID,
                MODEL_ID,
                "1.1.0-rc.concurrent",
                "DRAFT",
                "MISSING",
                "CONFIGURATION_REQUIRED",
                '"p14-registry-integration-concurrent-draft:1"',
            ),
        )


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    dsn = _superuser_dsn()
    # Production composes registry with every current module migration; run the
    # manifest twice so this test also preserves migration repeatability.
    asyncio.run(apply_migrations(dsn))
    asyncio.run(apply_migrations(dsn))
    _cleanup(dsn)
    _prepare_app_role(dsn)
    _seed(dsn)
    try:
        yield dsn
    finally:
        _cleanup(dsn)
        _drop_role(dsn)


def _auth(
    project_id: str = PROJECT_ID,
    *,
    can_manage: bool = False,
    region_code: str | None = None,
    can_robot_manage: bool = False,
) -> AuthContext:
    return AuthContext(
        subject_id="p14-registry-integration-reader",
        project_ids=frozenset({project_id}),
        region_codes=frozenset({region_code} if region_code else ()),
        roles=frozenset(),
        scope_pairs=frozenset(
            {(project_id, None)} | ({(project_id, region_code)} if region_code else set())
        ),
        scoped_capabilities=frozenset(
            {(project_id, "robot_model.read")}
            | ({(project_id, "robot_model.manage")} if can_manage else set())
            | ({(project_id, "robot.manage")} if can_robot_manage else set())
        ),
    )


def _service(dsn: str) -> RegistryService:
    return RegistryService(
        PostgresRegistryRepository(psycopg_connection_factory(_app_dsn(dsn))),
        clock=lambda: datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
    )


class _BarrierPostgresRegistryRepository(PostgresRegistryRepository):
    """Synchronize contenders immediately before the real write transaction."""

    def __init__(self, connection_factory: ConnectionFactory, *, replace_gate: Barrier) -> None:
        super().__init__(connection_factory)
        self._replace_gate = replace_gate

    def replace_robot_model_joint_mappings(
        self,
        *,
        organization_id: str,
        project_id: str,
        version_id: str,
        expected_etag: str,
        idempotency_key: str,
        request_fingerprint: str,
        mappings: tuple[RobotModelJointMapping, ...],
        mapping_hash: str,
        updated_at: datetime,
    ) -> RobotModelVersion:
        self._replace_gate.wait(timeout=3)
        return super().replace_robot_model_joint_mappings(
            organization_id=organization_id,
            project_id=project_id,
            version_id=version_id,
            expected_etag=expected_etag,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            mappings=mappings,
            mapping_hash=mapping_hash,
            updated_at=updated_at,
        )


@contextmanager
def _within_project_request() -> Iterator[None]:
    token = bind_request_context(
        RequestContext(
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            subject_id="p14-registry-integration-reader",
            request_id="p14-registry-integration-request",
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def test_postgres_registry_read_scope_audit_and_rls_with_non_superuser(
    postgres_dsn: str,
) -> None:
    service = _service(postgres_dsn)
    with _within_project_request():
        page = service.list_robot_models(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            query="P14",
            request_id="p14-registry-list",
        )
        assert [item.id for item in page.items] == [MODEL_ID]
        assert page.scope.project_id == PROJECT_ID

        detail = service.get_robot_model_version(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=VERSION_ID,
            request_id="p14-registry-detail",
        )
        assert detail.data.id == VERSION_ID
        assert detail.data.etag == '"p14-registry-integration-version:1"'

        # A non-superuser sees only the connection factory's selected project
        # membership even though this organization has another project row.
        connection = psycopg_connection_factory(_app_dsn(postgres_dsn))()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT project_id
                      FROM registry.organization_projects
                     WHERE organization_id = %s
                     ORDER BY project_id
                    """,
                    (ORGANIZATION_ID,),
                )
                assert cursor.fetchall() == [(PROJECT_ID,)]
        finally:
            connection.close()

    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM registry.audit_events WHERE project_id = %s",
            (PROJECT_ID,),
        )
        assert cursor.fetchone()[0] == 2

    with _within_project_request(), pytest.raises(ProblemException) as foreign:
        service.list_robot_models(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=FOREIGN_PROJECT_ID,
            query=None,
            request_id="p14-registry-foreign",
        )
    assert foreign.value.problem.status == 403
    assert foreign.value.problem.code == "PROJECT_SCOPE_DENIED"


def test_postgres_registry_asset_ledger_is_project_scoped_and_manifested(
    postgres_dsn: str,
) -> None:
    storage = InMemoryObjectStorage()
    service = RegistryService(
        PostgresRegistryRepository(psycopg_connection_factory(_app_dsn(postgres_dsn))),
        storage=storage,
        clock=lambda: datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
    )
    body = b'<robot name="p14"><joint name="joint_1" type="revolute"/></robot>'
    command = CreateRobotModelAssetUploadRequest(
        files=(
            RobotModelAssetUploadFileRequest(
                relative_path="models/p14.urdf",
                role=RobotAssetRole.URDF,
                media_type="application/xml",
                size_bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            ),
        )
    )
    with _within_project_request():
        created = service.create_asset_upload(
            auth=_auth(can_manage=True),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=DRAFT_VERSION_ID,
            request_id="p14-registry-asset-create",
            idempotency_key="p14-registry-asset-create",
            command=command,
        )
        upload_id = created.data.upload_id
        record = service._repository.get_asset_upload(  # noqa: SLF001 - assert ledger state
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            upload_id=upload_id,
        )
        assert record is not None
        part = storage.upload_part(
            record.files[0].multipart_upload_id,
            1,
            body,
            key=record.files[0].object_key,
        )
        completed = service.complete_asset_upload_file(
            auth=_auth(can_manage=True),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            upload_id=upload_id,
            request_id="p14-registry-asset-complete",
            command=CompleteRobotModelAssetFileRequest(
                relative_path="models/p14.urdf",
                parts=(RobotAssetCompletedPart(part_number=1, etag=part.etag),),
            ),
        )
        assert completed.data.status.value == "COMPLETED"
        assets = service.list_robot_model_assets(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=DRAFT_VERSION_ID,
            request_id="p14-registry-assets-list",
        )
        assert len(assets.items) == 1
        assert assets.items[0].sha256 == hashlib.sha256(body).hexdigest()
        authorization = service.authorize_asset_download(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=DRAFT_VERSION_ID,
            asset_id=assets.items[0].asset_id,
            request_id="p14-registry-assets-download",
        )
        assert authorization.download_url.startswith("memory://object/")

        draft = service.get_robot_model_version(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=DRAFT_VERSION_ID,
            request_id="p14-registry-draft-after-upload",
        )
        mapped = service.replace_robot_model_joint_mappings(
            auth=_auth(can_manage=True),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=DRAFT_VERSION_ID,
            expected_etag=draft.data.etag,
            idempotency_key="p14-registry-mapping-replace",
            request_id="p14-registry-mapping-replace",
            command=ReplaceRobotModelJointMappingsRequest(
                mappings=(
                    RobotModelJointMapping(
                        source_joint_name="actuator_1",
                        target_joint_name="joint_1",
                        direction=RobotJointDirection.SAME,
                    ),
                )
            ),
        )
        preflight = service.preflight_robot_model_publish(
            auth=_auth(can_manage=True),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=DRAFT_VERSION_ID,
            expected_etag=mapped.data.etag,
            idempotency_key="p14-registry-publish",
            request_id="p14-registry-preflight",
        )
        assert preflight.data.allowed is True
        assert preflight.data.preflight_token is not None
        published = service.publish_robot_model_version(
            auth=_auth(can_manage=True),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=DRAFT_VERSION_ID,
            expected_etag=mapped.data.etag,
            idempotency_key="p14-registry-publish",
            request_id="p14-registry-publish",
            command=PublishRobotModelVersionRequest(preflight_token=preflight.data.preflight_token),
        )
        assert published.data.lifecycle == "PUBLISHED"
        replay = service.publish_robot_model_version(
            auth=_auth(can_manage=True),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=DRAFT_VERSION_ID,
            expected_etag=mapped.data.etag,
            idempotency_key="p14-registry-publish",
            request_id="p14-registry-publish-replay",
            command=PublishRobotModelVersionRequest(preflight_token=preflight.data.preflight_token),
        )
        assert replay.data.etag == published.data.etag
        binding = service.bind_robot_model_version(
            auth=_auth(
                can_manage=True,
                region_code=REGION_CODE,
                can_robot_manage=True,
            ),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=DRAFT_VERSION_ID,
            idempotency_key="p14-registry-bind-robot",
            request_id="p14-registry-bind-robot",
            command=BindRobotModelVersionRequest(
                region_code=REGION_CODE,
                robot_id=ROBOT_ID,
                robot_etag='"p14-robot:1"',
            ),
        )
        assert binding.robot_id == ROBOT_ID
        assert binding.status.value == "ACTIVE"
        bindings = service.list_robot_model_bindings(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=DRAFT_VERSION_ID,
            request_id="p14-registry-bindings-list",
        )
        assert [item.binding_id for item in bindings.items] == [binding.binding_id]
        with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT robot_model_version_id, binding_id, binding_scope_type, binding_scope_id
                  FROM robotics.robot_instances
                 WHERE project_id = %s AND region_code = %s AND robot_id = %s
                """,
                (PROJECT_ID, REGION_CODE, ROBOT_ID),
            )
            assert cursor.fetchone() == (
                DRAFT_VERSION_ID,
                binding.binding_id,
                "ROBOT_INSTANCE",
                ROBOT_ID,
            )

        with pytest.raises(ProblemException) as denied:
            service.list_robot_model_assets(
                auth=_auth(FOREIGN_PROJECT_ID),
                organization_id=ORGANIZATION_ID,
                project_id=FOREIGN_PROJECT_ID,
                version_id=DRAFT_VERSION_ID,
                request_id="p14-registry-assets-foreign",
            )
        assert denied.value.problem.code == "ORGANIZATION_SCOPE_DENIED"


def test_postgres_joint_mapping_compare_and_swap_is_real_under_row_lock(
    postgres_dsn: str,
) -> None:
    """Two stale writers must overlap at PostgreSQL and leave one durable winner."""

    expected_etag = '"p14-registry-integration-concurrent-draft:1"'
    contenders_ready = Event()
    replace_gate = Barrier(2, action=contenders_ready.set)

    def attempt(target_joint_name: str) -> tuple[str, str | int]:
        token = bind_request_context(
            RequestContext(
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                subject_id=f"p14-registry-concurrent-{target_joint_name}",
                request_id=f"p14-registry-concurrent-{target_joint_name}",
            )
        )
        try:
            service = RegistryService(
                _BarrierPostgresRegistryRepository(
                    psycopg_connection_factory(_app_dsn(postgres_dsn)),
                    replace_gate=replace_gate,
                ),
                clock=lambda: datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
            )
            updated = service.replace_robot_model_joint_mappings(
                auth=_auth(can_manage=True),
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                version_id=CONCURRENT_DRAFT_VERSION_ID,
                expected_etag=expected_etag,
                idempotency_key=f"p14-registry-concurrent-{target_joint_name}",
                request_id=f"p14-registry-concurrent-{target_joint_name}",
                command=ReplaceRobotModelJointMappingsRequest(
                    mappings=(
                        RobotModelJointMapping(
                            source_joint_name="joint_1",
                            target_joint_name=target_joint_name,
                            direction=RobotJointDirection.SAME,
                        ),
                    )
                ),
            )
            return ("success", updated.data.etag)
        except ProblemException as exc:
            return (exc.problem.code, exc.problem.status)
        finally:
            reset_request_context(token)

    try:
        with psycopg.connect(postgres_dsn) as lock_connection, lock_connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE OR REPLACE FUNCTION registry.p14_test_mapping_lock_trigger()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    PERFORM pg_advisory_xact_lock(814201947);
                    RETURN NEW;
                END;
                $$
                """
            )
            cursor.execute(
                """
                CREATE TRIGGER p14_test_mapping_lock_trigger
                BEFORE UPDATE ON registry.robot_model_versions
                FOR EACH ROW EXECUTE FUNCTION registry.p14_test_mapping_lock_trigger()
                """
            )
            lock_connection.commit()
            cursor.execute("SELECT pg_advisory_lock(%s)", (MAPPING_TRIGGER_ADVISORY_KEY,))
            lock_connection.commit()

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(attempt, target_joint_name)
                    for target_joint_name in ("actuator_left", "actuator_right")
                ]
                assert contenders_ready.wait(timeout=3), (
                    "mapping writers never reached the write path"
                )
                writers_waiting = False
                waiting_activity: list[tuple[object, ...]] = []
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
                    # The first writer holds the version row while the trigger
                    # waits; releasing this advisory lock lets it commit before
                    # the stale competing writer obtains that row lock.
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (MAPPING_TRIGGER_ADVISORY_KEY,))
                    lock_connection.commit()
                outcomes = [future.result(timeout=3) for future in futures]
    finally:
        with (
            psycopg.connect(postgres_dsn) as cleanup_connection,
            cleanup_connection.cursor() as cleanup,
        ):
            cleanup.execute(
                "DROP TRIGGER IF EXISTS p14_test_mapping_lock_trigger "
                "ON registry.robot_model_versions"
            )
            cleanup.execute("DROP FUNCTION IF EXISTS registry.p14_test_mapping_lock_trigger()")

    assert writers_waiting, (
        f"mapping writers did not reach the row lock: {outcomes}; activity={waiting_activity}"
    )
    assert sum(outcome[0] == "success" for outcome in outcomes) == 1
    assert outcomes.count(("ROBOT_MODEL_VERSION_ETAG_MISMATCH", 412)) == 1
    with _within_project_request():
        mappings = _service(postgres_dsn).list_robot_model_joint_mappings(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            version_id=CONCURRENT_DRAFT_VERSION_ID,
            request_id="p14-registry-concurrent-readback",
        )
    assert len(mappings.items) == 1
    assert mappings.items[0].target_joint_name in {"actuator_left", "actuator_right"}

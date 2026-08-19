from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from hc_data_platform.core.context import (  # noqa: E402
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory  # noqa: E402
from hc_data_platform.core.errors import ProblemException  # noqa: E402
from hc_data_platform.security.auth import AuthContext, Role  # noqa: E402
from hc_data_platform.storage.models import (  # noqa: E402
    BusinessCapacityCategory,
    CapacityInventoryFact,
    CreateLifecyclePolicy,
    InventoryDisposition,
    LifecyclePolicyAction,
    ObjectRole,
)
from hc_data_platform.storage.postgres import (  # noqa: E402
    PostgresStorageIdempotencyStore,
    PostgresStorageRepository,
    StorageTransactionConnectionFactory,
)
from hc_data_platform.storage.service import StorageGovernanceService  # noqa: E402

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 17, 4, tzinfo=timezone.utc)


def _dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _apply_migrations(dsn: str) -> None:
    scripts = (
        ROOT / "migrations" / "security" / "001_core.sql",
        ROOT / "migrations" / "storage" / "0001_storage_governance.sql",
        ROOT / "migrations" / "storage" / "0002_seal_inventory_snapshots.sql",
    )
    with psycopg.connect(dsn) as connection:
        for script in scripts:
            connection.execute(script.read_text(encoding="utf-8"))
        # Repeatability is part of the module contract.
        for script in scripts:
            connection.execute(script.read_text(encoding="utf-8"))


def _operator(project_id: str) -> AuthContext:
    return AuthContext.service(
        subject_id="storage-postgres-integration",
        roles={Role.ADMIN},
        project_ids={project_id},
    )


def _fact(
    physical_id: str,
    logical_id: str | None,
    size: int,
    *,
    category: BusinessCapacityCategory | None,
    disposition: InventoryDisposition = InventoryDisposition.PRIMARY,
    role: ObjectRole = ObjectRole.OTHER,
) -> CapacityInventoryFact:
    return CapacityInventoryFact(
        snapshot_id="snapshot-postgres",
        project_id="project-postgres",
        physical_instance_id=physical_id,
        logical_object_id=logical_id,
        physical_bytes=str(size),
        disposition=disposition,
        business_category=category,
        object_role=role,
        observed_at=NOW,
    )


def _facts() -> tuple[CapacityInventoryFact, ...]:
    raw = _fact(
        "physical-01",
        "logical-raw",
        100,
        category=BusinessCapacityCategory.RAW,
        role=ObjectRole.RAW,
    )
    return (
        raw,
        raw,
        _fact(
            "physical-02",
            "logical-raw",
            100,
            category=BusinessCapacityCategory.RAW,
            disposition=InventoryDisposition.REPLICA,
            role=ObjectRole.RAW,
        ),
        _fact(
            "physical-03",
            "logical-complete",
            50,
            category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
        ),
        _fact(
            "physical-04",
            "logical-pending",
            30,
            category=BusinessCapacityCategory.PENDING_ANNOTATION,
        ),
        _fact("physical-05", "logical-issue", 20, category=BusinessCapacityCategory.ISSUE_DATA),
        _fact(
            "physical-06",
            None,
            10,
            category=None,
            disposition=InventoryDisposition.TEMPORARY,
        ),
    )


def _service(dsn: str) -> StorageGovernanceService:
    base_factory = psycopg_connection_factory(dsn)
    transaction_factory = StorageTransactionConnectionFactory(base_factory)
    return StorageGovernanceService(
        PostgresStorageRepository(transaction_factory),
        cursor_secret="postgres-storage-integration",
        idempotency=PostgresStorageIdempotencyStore(base_factory, transaction_factory),
        clock=lambda: NOW,
        id_factory=lambda: "policy-postgres",
    )


def test_postgres_storage_reconciliation_replay_rls_and_protection_constraints() -> None:
    dsn = _dsn()
    _apply_migrations(dsn)
    token = bind_request_context(
        RequestContext(
            project_id="project-postgres",
            subject_id="storage-postgres-integration",
            region_code="cn-test",
            request_id="request-postgres",
            service_identity=True,
        )
    )
    try:
        target = _service(dsn)
        actor = _operator("project-postgres")

        snapshot = target.record_inventory_snapshot(
            project_id="project-postgres",
            snapshot_id="snapshot-postgres",
            facts=_facts(),
        )
        replay = target.record_inventory_snapshot(
            project_id="project-postgres",
            snapshot_id="snapshot-postgres",
            facts=_facts(),
        )
        assert snapshot == replay
        assert snapshot.physical_total_bytes == "310"
        assert snapshot.candidate_business_total_bytes == "200"
        assert snapshot.reconciliation.duplicate_inventory_rows_ignored == 1
        persisted = target.capacity_snapshot(project_id="project-postgres", actor=actor)
        assert persisted.reconciliation.duplicate_inventory_rows_ignored == 1

        first = target.inventory_page(project_id="project-postgres", actor=actor, limit=2)
        second = target.inventory_page(
            project_id="project-postgres",
            actor=actor,
            cursor=first.page_info.end_cursor,
            limit=2,
        )
        previous = target.inventory_page(
            project_id="project-postgres",
            actor=actor,
            cursor=second.page_info.start_cursor,
            limit=2,
        )
        assert previous.items == first.items

        created = target.create_policy(
            project_id="project-postgres",
            command=CreateLifecyclePolicy(
                name="可重建缓存清理",
                business_category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
                object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                action=LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE,
                minimum_age_days=30,
                priority=100,
            ),
            actor=actor,
            idempotency_key="create-policy-postgres",
            request_id="request-create",
        )
        repeated = target.create_policy(
            project_id="project-postgres",
            command=CreateLifecyclePolicy(
                name="可重建缓存清理",
                business_category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
                object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                action=LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE,
                minimum_age_days=30,
                priority=100,
            ),
            actor=actor,
            idempotency_key="create-policy-postgres",
            request_id="request-create-replay",
        )
        assert repeated.replayed is True
        assert repeated.policy == created.policy
        assert len(target.list_audit(project_id="project-postgres", actor=actor).items) == 1

        with pytest.raises(ProblemException) as immutable:
            target.record_inventory_snapshot(
                project_id="project-postgres",
                snapshot_id="snapshot-postgres",
                facts=(
                    *_facts()[:-1],
                    _fact(
                        "physical-06",
                        None,
                        11,
                        category=None,
                        disposition=InventoryDisposition.TEMPORARY,
                    ),
                ),
            )
        assert immutable.value.problem.code == "CAPACITY_SNAPSHOT_IMMUTABLE"

        connection_factory = psycopg_connection_factory(dsn)
        connection = connection_factory()
        try:
            cursor = connection.cursor()
            with pytest.raises(psycopg.Error):
                cursor.execute(
                    """
                    UPDATE storage.lifecycle_audit_events
                    SET action = 'tampered'
                    WHERE project_id = %s
                    """,
                    ("project-postgres",),
                )
            connection.rollback()
            cursor.close()
            connection.close()
            connection = connection_factory()

            cursor = connection.cursor()
            with pytest.raises(psycopg.Error):
                cursor.execute(
                    """
                    UPDATE storage.inventory_facts
                    SET physical_bytes = physical_bytes + 1
                    WHERE project_id = %s AND snapshot_id = %s
                    """,
                    ("project-postgres", "snapshot-postgres"),
                )
            connection.rollback()
            cursor.close()
            connection.close()
            connection = connection_factory()

            cursor = connection.cursor()
            with pytest.raises(psycopg.Error):
                cursor.execute(
                    """
                    INSERT INTO storage.inventory_facts (
                        project_id, snapshot_id, physical_instance_id, logical_object_id,
                        physical_bytes, disposition, business_category, object_role, observed_at
                    ) VALUES (%s, %s, 'late-fact', 'late-logical', 1, 'PRIMARY', 'RAW', 'RAW', %s)
                    """,
                    ("project-postgres", "snapshot-postgres", NOW),
                )
            connection.rollback()
            cursor.close()
            connection.close()
            connection = connection_factory()

            cursor = connection.cursor()
            with pytest.raises(psycopg.Error):
                cursor.execute(
                    """
                    INSERT INTO storage.lifecycle_executions (
                        project_id, execution_id, policy_id, policy_version, action,
                        production, production_execution_approved, request_fingerprint,
                        status, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, 1, 'CLEAN_REBUILDABLE_CACHE',
                        true, false, %s, 'PENDING', %s, %s
                    )
                    """,
                    (
                        "project-postgres",
                        "execution-production",
                        "policy-postgres",
                        "a" * 64,
                        NOW,
                        NOW,
                    ),
                )
            connection.rollback()
            cursor.close()
            connection.close()
            connection = connection_factory()

            cursor = connection.cursor()
            cursor.execute(
                "SELECT count(*) FROM storage.inventory_snapshots WHERE project_id = %s",
                ("another-project",),
            )
            assert cursor.fetchone()[0] == 0
            cursor.close()
        finally:
            connection.close()
    finally:
        reset_request_context(token)
    _apply_migrations(dsn)

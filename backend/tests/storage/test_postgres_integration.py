from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone
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
from hc_data_platform.core.events import DomainEventEnvelope  # noqa: E402
from hc_data_platform.core.migrations import apply_migrations  # noqa: E402
from hc_data_platform.security.auth import AuthContext  # noqa: E402
from hc_data_platform.security.outbox import (  # noqa: E402
    PostgresOutboxDeliveryRepository,
)
from hc_data_platform.storage.dispatch import (  # noqa: E402
    RepositoryStorageExecutionInputResolver,
    StorageLifecycleScheduleOutboxHandler,
)
from hc_data_platform.storage.executor import PostgresLifecycleBatchExecutor  # noqa: E402
from hc_data_platform.storage.models import (  # noqa: E402
    ApproveLifecycleExecutionRequest,
    BusinessCapacityCategory,
    CapacityInventoryFact,
    CreateLifecyclePolicy,
    CreateLifecycleScheduleRequest,
    InventoryDisposition,
    LifecycleBatchCommand,
    LifecycleDryRunRequest,
    LifecyclePolicyAction,
    ManagedStorageObjectRecord,
    ObjectRole,
    RetryLifecycleExecutionRequest,
    StartLifecycleExecutionRequest,
    StorageObjectStatus,
    StorageTier,
)
from hc_data_platform.storage.object_store import InMemoryStorageObjectOperator  # noqa: E402
from hc_data_platform.storage.postgres import (  # noqa: E402
    PostgresStorageIdempotencyStore,
    PostgresStorageRepository,
    StorageTransactionConnectionFactory,
)
from hc_data_platform.storage.service import StorageGovernanceService  # noqa: E402

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 17, 4, tzinfo=timezone.utc)
ORGANIZATION_ID = "organization-storage-postgres"


def _dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _apply_migrations(dsn: str) -> None:
    # The production runtime composes storage with every current domain
    # migration.  Applying the full manifest catches later constraints and
    # RLS reconciliation changes before this fixture can claim real-Postgres
    # coverage for P12/P13.
    asyncio.run(apply_migrations(dsn))
    asyncio.run(apply_migrations(dsn))
    scripts = (
        ROOT / "migrations" / "storage" / "0001_storage_governance.sql",
        ROOT / "migrations" / "storage" / "0002_seal_inventory_snapshots.sql",
    )
    with psycopg.connect(dsn) as connection:
        for script in scripts:
            connection.execute(script.read_text(encoding="utf-8"))
        # Repeatability is part of the module contract.
        for script in scripts:
            connection.execute(script.read_text(encoding="utf-8"))


def _cleanup(dsn: str) -> None:
    project_id = "project-postgres"
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        for table in (
            "storage.lifecycle_execution_logs",
            "storage.lifecycle_execution_approvals",
            "storage.lifecycle_execution_items",
            "storage.lifecycle_executions",
            "storage.lifecycle_schedules",
            "storage.object_operations",
            "storage.managed_multipart_uploads",
            "storage.managed_objects",
            "storage.lifecycle_audit_events",
            "storage.lifecycle_policies",
            "storage.inventory_facts",
            "storage.inventory_snapshots",
            "core.audit_integrity_entries",
            "core.audit_integrity_heads",
            "core.audit_events",
            "core.outbox_events",
            "core.idempotency_records",
        ):
            cursor.execute(f"DELETE FROM {table} WHERE project_id = %s", (project_id,))
        cursor.execute(
            "DELETE FROM registry.organization_projects "
            "WHERE organization_id = %s AND project_id = %s",
            (ORGANIZATION_ID, project_id),
        )


def _prepare_scope(dsn: str) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO registry.organization_projects (organization_id, project_id) "
            "VALUES (%s, %s)",
            (ORGANIZATION_ID, "project-postgres"),
        )


def _operator(
    project_id: str,
    *,
    subject_id: str = "storage-postgres-integration",
) -> AuthContext:
    return AuthContext(
        subject_id=subject_id,
        project_ids=frozenset({project_id}),
        region_codes=frozenset({"cn-test"}),
        capabilities=frozenset(
            {
                "storage.overview.read",
                "storage.object.read",
                "storage.object.manage",
                "storage.lifecycle.read",
                "storage.lifecycle.manage",
                "storage.lifecycle.execute",
                "storage.lifecycle.approve",
            }
        ),
        service_identity=True,
        scope_pairs=frozenset({(project_id, "cn-test")}),
        organization_ids=frozenset({ORGANIZATION_ID}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, project_id, "cn-test")}),
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


def _snapshot_facts(
    *,
    snapshot_id: str,
    observed_at: datetime,
    annotated_bytes: int,
) -> tuple[CapacityInventoryFact, ...]:
    return tuple(
        item.model_copy(
            update={
                "snapshot_id": snapshot_id,
                "observed_at": observed_at,
                **(
                    {"physical_bytes": str(annotated_bytes)}
                    if item.physical_instance_id == "physical-03"
                    else {}
                ),
            }
        )
        for item in _facts()
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
    _cleanup(dsn)
    _prepare_scope(dsn)
    token = bind_request_context(
        RequestContext(
            organization_id=ORGANIZATION_ID,
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

        target.record_inventory_snapshot(
            project_id="project-postgres",
            snapshot_id="snapshot-postgres-previous",
            facts=_snapshot_facts(
                snapshot_id="snapshot-postgres-previous",
                observed_at=NOW - timedelta(days=1),
                annotated_bytes=20,
            ),
        )
        # The older same-day fact must not displace the current snapshot in the
        # history projection merely because it was written first.
        target.record_inventory_snapshot(
            project_id="project-postgres",
            snapshot_id="snapshot-postgres-same-day-early",
            facts=_snapshot_facts(
                snapshot_id="snapshot-postgres-same-day-early",
                observed_at=NOW - timedelta(hours=1),
                annotated_bytes=40,
            ),
        )

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

        history = target.capacity_history(
            project_id="project-postgres",
            actor=actor,
            window_start=NOW - timedelta(days=2),
            window_end=NOW + timedelta(hours=1),
        )
        assert [item.snapshot_id for item in history.items] == [
            "snapshot-postgres-previous",
            "snapshot-postgres",
        ]
        assert history.growth is not None
        assert history.growth.candidate_change_bytes == "30"
        assert history.growth.candidate_bytes_per_day == "30"

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
    _cleanup(dsn)
    _apply_migrations(dsn)


def test_postgres_approved_lifecycle_execution_is_checkpointed_and_recoverable() -> None:
    dsn = _dsn()
    _apply_migrations(dsn)
    _cleanup(dsn)
    _prepare_scope(dsn)
    token = bind_request_context(
        RequestContext(
            organization_id=ORGANIZATION_ID,
            project_id="project-postgres",
            subject_id="storage-postgres-requester",
            region_code="cn-test",
            request_id="request-postgres-execution",
            service_identity=True,
        )
    )
    try:
        base_factory = psycopg_connection_factory(dsn)
        transaction_factory = StorageTransactionConnectionFactory(base_factory)
        repository = PostgresStorageRepository(transaction_factory)
        object_operator = InMemoryStorageObjectOperator()
        ids = iter(f"postgres-execution-{index:03d}" for index in range(100))
        service = StorageGovernanceService(
            repository,
            cursor_secret="postgres-storage-execution",
            idempotency=PostgresStorageIdempotencyStore(base_factory, transaction_factory),
            object_operator=object_operator,
            clock=lambda: NOW,
            id_factory=lambda: next(ids),
        )
        requester = _operator(
            "project-postgres",
            subject_id="storage-postgres-requester",
        )
        approver = _operator(
            "project-postgres",
            subject_id="storage-postgres-approver",
        )
        body = b"postgres-lifecycle-cache"
        managed = ManagedStorageObjectRecord(
            object_id="postgres-cache-object",
            project_id="project-postgres",
            display_key="cache/postgres-cache.bin",
            object_key="storage-tests/postgres-cache.bin",
            original_object_key="storage-tests/postgres-cache.bin",
            physical_bytes=str(len(body)),
            checksum_sha256=hashlib.sha256(body).hexdigest(),
            business_category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
            object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
            storage_tier=StorageTier.HOT,
            status=StorageObjectStatus.ACTIVE,
            active_reference_count=0,
            rebuild_source_id="postgres-raw-source",
            version=1,
            etag='"v1"',
            created_at=NOW - timedelta(days=90),
            updated_at=NOW - timedelta(days=60),
        )
        object_operator.put(managed.object_key, body)
        repository.save_managed_object(
            managed,
            expected_version=None,
            audit_action="storage.object.registered",
            actor_id="fixture",
            request_id="fixture",
            before=None,
        )
        created = service.create_policy(
            project_id="project-postgres",
            command=CreateLifecyclePolicy(
                name="PostgreSQL 可重建缓存清理",
                business_category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
                object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                action=LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE,
                minimum_age_days=30,
                priority=101,
            ),
            actor=requester,
            idempotency_key="execution-policy-create",
            request_id="execution-policy-create",
        ).policy
        assert created is not None
        enabled = service.enable_policy(
            project_id="project-postgres",
            policy_id=created.policy_id,
            actor=requester,
            if_match=created.etag,
            idempotency_key="execution-policy-enable",
            request_id="execution-policy-enable",
        ).policy
        assert enabled is not None
        planned = service.create_lifecycle_dry_run_command(
            project_id="project-postgres",
            command=LifecycleDryRunRequest(
                policy_id=enabled.policy_id,
                policy_etag=enabled.etag,
            ),
            actor=requester,
            idempotency_key="execution-dry-run",
            request_id="execution-dry-run",
        ).execution
        approved = service.approve_lifecycle_execution_command(
            project_id="project-postgres",
            execution_id=planned.execution_id,
            command=ApproveLifecycleExecutionRequest(
                plan_hash=planned.plan_hash,
                justification="independent PostgreSQL integration approval",
            ),
            actor=approver,
            idempotency_key="execution-approve",
            request_id="execution-approve",
        ).execution
        assert approved.approval_id is not None
        queued = service.start_lifecycle_execution_command(
            project_id="project-postgres",
            execution_id=planned.execution_id,
            command=StartLifecycleExecutionRequest(
                approval_id=approved.approval_id,
                plan_hash=planned.plan_hash,
            ),
            actor=requester,
            idempotency_key="execution-start",
            request_id="execution-start",
        ).execution
        assert queued.status.value == "QUEUED"
        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE core.outbox_events
                SET region_code = NULL,
                    envelope = jsonb_set(envelope, '{region_code}', 'null'::jsonb)
                WHERE project_id = %s
                  AND event_type = 'storage.lifecycle.execution.requested.v1'
                """,
                ("project-postgres",),
            )
            connection.commit()
        project_delivery = PostgresOutboxDeliveryRepository(base_factory)
        project_claim = project_delivery.claim_next(
            organization_id=ORGANIZATION_ID,
            project_id="project-postgres",
            region_code="cn-test",
            worker_id="storage-project-worker",
            now=NOW,
            claimed_until=NOW + timedelta(minutes=5),
        )
        assert project_claim is not None
        assert project_claim.event.region_code is None
        project_delivery.mark_dispatched(project_claim, occurred_at=NOW)
        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE storage.lifecycle_execution_items
                SET status = 'FAILED', attempt = 1,
                    last_error = 'TRANSIENT_PROVIDER_ERROR',
                    last_error_code = 'TRANSIENT_PROVIDER_ERROR'
                WHERE project_id = %s AND execution_id = %s
                """,
                ("project-postgres", queued.execution_id),
            )
            cursor.execute(
                """
                UPDATE storage.lifecycle_executions
                SET status = 'FAILED', last_error = 'TRANSIENT_PROVIDER_ERROR'
                WHERE project_id = %s AND execution_id = %s
                """,
                ("project-postgres", queued.execution_id),
            )
            connection.commit()
        queued = service.retry_lifecycle_execution_command(
            project_id="project-postgres",
            execution_id=queued.execution_id,
            command=RetryLifecycleExecutionRequest(
                plan_hash=queued.plan_hash,
                reason="retry the exact approved plan after transient provider recovery",
            ),
            actor=requester,
            idempotency_key="execution-retry",
            request_id="execution-retry",
        ).execution
        assert queued.status.value == "QUEUED"
        request = RepositoryStorageExecutionInputResolver(repository).resolve(
            project_id="project-postgres",
            execution_id=queued.execution_id,
        )
        executor = PostgresLifecycleBatchExecutor(
            base_factory,
            object_operator,
            clock=lambda: NOW + timedelta(minutes=1),
        )
        result = executor.apply_batch(
            LifecycleBatchCommand(
                execution_id=request.execution_id,
                organization_id=request.organization_id,
                project_id=request.project_id,
                region_code=request.region_code,
                policy_id=request.policy_id,
                policy_version=request.policy_version,
                action=request.action,
                production=request.production,
                production_execution_approved=request.production_execution_approved,
                approval_id=request.approval_id,
                plan_hash=request.plan_hash,
                batch_index=0,
                final_batch=True,
                candidates=request.candidates,
            )
        )
        assert result.processed_instance_ids == (managed.object_id,)
        completed = repository.get_execution(
            project_id="project-postgres",
            execution_id=queued.execution_id,
        )
        assert completed is not None
        assert completed.status.value == "COMPLETED"
        assert completed.processed_items == 1
        moved = repository.get_managed_object(
            project_id="project-postgres",
            object_id=managed.object_id,
        )
        assert moved is not None
        assert moved.status is StorageObjectStatus.TRASHED
        assert object_operator.head(moved.object_key) is not None
        assert object_operator.head(managed.object_key) is None

        schedule = service.create_lifecycle_schedule_command(
            project_id="project-postgres",
            command=CreateLifecycleScheduleRequest(
                policy_id=enabled.policy_id,
                interval_seconds=3600,
                first_run_at=NOW + timedelta(minutes=5),
            ),
            actor=requester,
            idempotency_key="execution-schedule-create",
            request_id="execution-schedule-create",
        ).schedule
        assert schedule is not None
        assert (
            repository.enqueue_due_schedules(
                project_id="project-postgres",
                region_code="cn-test",
                now=NOW + timedelta(minutes=6),
                limit=10,
            )
            == 1
        )
        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT envelope
                FROM core.outbox_events
                WHERE project_id = %s
                  AND event_type = 'storage.lifecycle.schedule.due.v1'
                """,
                ("project-postgres",),
            )
            raw_envelope = cursor.fetchone()[0]
        event = DomainEventEnvelope.model_validate(raw_envelope)
        assert event.organization_id == ORGANIZATION_ID
        assert event.region_code == "cn-test"
        schedule_handler = StorageLifecycleScheduleOutboxHandler(service)
        scheduled_execution = schedule_handler(event)
        assert scheduled_execution is not None
        assert scheduled_execution.status.value == "BLOCKED"
        linked_schedule = repository.get_schedule(
            project_id="project-postgres",
            schedule_id=schedule.schedule_id,
        )
        assert linked_schedule is not None
        assert linked_schedule.last_execution_id == scheduled_execution.execution_id
        linked_version = linked_schedule.version
        assert schedule_handler(event) == scheduled_execution
        replayed_schedule = repository.get_schedule(
            project_id="project-postgres",
            schedule_id=schedule.schedule_id,
        )
        assert replayed_schedule is not None
        assert replayed_schedule.version == linked_version
        with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM core.outbox_events WHERE project_id = %s",
                ("project-postgres",),
            )
            assert cursor.fetchone()[0] == 3
            cursor.execute(
                "SELECT count(*) FROM storage.lifecycle_execution_logs "
                "WHERE project_id = %s AND execution_id = %s",
                ("project-postgres", queued.execution_id),
            )
            assert cursor.fetchone()[0] == 5
    finally:
        reset_request_context(token)
        _cleanup(dsn)

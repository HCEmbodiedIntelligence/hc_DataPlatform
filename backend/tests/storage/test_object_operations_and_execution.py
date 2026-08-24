from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext, Role
from hc_data_platform.security.versioning import ResourceVersion
from hc_data_platform.storage.models import (
    ApproveLifecycleExecutionRequest,
    BusinessCapacityCategory,
    CancelLifecycleExecutionRequest,
    CreateLifecyclePolicy,
    CreateLifecycleScheduleRequest,
    LifecycleDryRunRequest,
    LifecyclePolicyAction,
    ManagedMultipartUploadRecord,
    ManagedStorageObjectRecord,
    ObjectRole,
    RestoreStorageObjectRequest,
    StartLifecycleExecutionRequest,
    StorageObjectStatus,
    StorageTier,
    TransitionStorageObjectRequest,
    TrashStorageObjectRequest,
    UpdateLifecycleScheduleRequest,
)
from hc_data_platform.storage.object_store import InMemoryStorageObjectOperator
from hc_data_platform.storage.repository import InMemoryStorageRepository
from hc_data_platform.storage.service import StorageGovernanceService

NOW = datetime(2026, 8, 21, 6, tzinfo=timezone.utc)


def actor(subject_id: str = "storage-requester") -> AuthContext:
    return AuthContext.service(
        subject_id=subject_id,
        roles={Role.ADMIN},
        project_ids={"project-a"},
    )


def build_service() -> tuple[
    StorageGovernanceService,
    InMemoryStorageRepository,
    InMemoryStorageObjectOperator,
]:
    repository = InMemoryStorageRepository()
    operator = InMemoryStorageObjectOperator()
    ids = iter(f"storage-id-{index:03d}" for index in range(100))
    return (
        StorageGovernanceService(
            repository,
            object_operator=operator,
            clock=lambda: NOW,
            id_factory=lambda: next(ids),
            cursor_secret="object-operation-tests",
        ),
        repository,
        operator,
    )


def record(
    body: bytes,
    *,
    object_id: str = "object-1",
    role: ObjectRole = ObjectRole.OTHER,
    references: int = 0,
) -> ManagedStorageObjectRecord:
    return ManagedStorageObjectRecord(
        object_id=object_id,
        project_id="project-a",
        display_key=f"datasets/{object_id}.bin",
        object_key=f"objects/{object_id}.bin",
        original_object_key=f"objects/{object_id}.bin",
        physical_bytes=str(len(body)),
        checksum_sha256=sha256(body).hexdigest(),
        business_category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
        object_role=role,
        storage_tier=StorageTier.HOT,
        status=StorageObjectStatus.ACTIVE,
        active_reference_count=references,
        rebuild_source_id="raw-source" if role is ObjectRole.REBUILDABLE_DERIVATIVE else None,
        version=1,
        etag=ResourceVersion(1).etag,
        created_at=NOW - timedelta(days=90),
        updated_at=NOW - timedelta(days=60),
    )


def seed_object(
    repository: InMemoryStorageRepository,
    operator: InMemoryStorageObjectOperator,
    value: ManagedStorageObjectRecord,
    body: bytes,
) -> None:
    operator.put(value.object_key, body)
    repository.save_managed_object(
        value,
        expected_version=None,
        audit_action="storage.object.registered",
        actor_id="fixture",
        request_id="fixture",
        before=None,
    )


def test_download_trash_restore_and_tier_moves_are_real_recoverable_operations() -> None:
    service, repository, operator = build_service()
    body = b"durable-storage-object"
    initial = record(body)
    seed_object(repository, operator, initial, body)

    grant = service.authorize_object_download(
        project_id="project-a",
        object_id=initial.object_id,
        actor=actor(),
        idempotency_key="download-1",
        request_id="download-1",
    )
    assert grant.checksum_sha256 == sha256(body).hexdigest()
    assert grant.url.startswith("https://objects.invalid/")
    assert "object_key" not in grant.model_dump(mode="json")

    trashed = service.trash_object(
        project_id="project-a",
        object_id=initial.object_id,
        command=TrashStorageObjectRequest(reason="operator requested recoverable cleanup"),
        actor=actor(),
        if_match=initial.etag,
        idempotency_key="trash-1",
        request_id="trash-1",
    )
    assert trashed.status is StorageObjectStatus.TRASHED
    assert trashed.recoverable_until == NOW + timedelta(days=30)
    assert operator.head(initial.object_key) is None
    persisted_trash = repository.get_managed_object(
        project_id="project-a", object_id=initial.object_id
    )
    assert persisted_trash is not None
    assert operator.head(persisted_trash.object_key) is not None

    replay = service.trash_object(
        project_id="project-a",
        object_id=initial.object_id,
        command=TrashStorageObjectRequest(reason="operator requested recoverable cleanup"),
        actor=actor(),
        if_match=initial.etag,
        idempotency_key="trash-1",
        request_id="trash-replay",
    )
    assert replay == trashed

    restored = service.restore_object(
        project_id="project-a",
        object_id=initial.object_id,
        command=RestoreStorageObjectRequest(reason="restore after operator review"),
        actor=actor(),
        if_match=trashed.etag,
        idempotency_key="restore-1",
        request_id="restore-1",
    )
    assert restored.status is StorageObjectStatus.ACTIVE
    assert operator.objects[initial.object_key] == body

    cold = service.transition_object(
        project_id="project-a",
        object_id=initial.object_id,
        command=TransitionStorageObjectRequest(
            action=LifecyclePolicyAction.TRANSITION_TO_COLD,
            reason="move an inactive object to the cold tier",
        ),
        actor=actor(),
        if_match=restored.etag,
        idempotency_key="cold-1",
        request_id="cold-1",
    )
    assert cold.storage_tier is StorageTier.COLD
    archived = service.transition_object(
        project_id="project-a",
        object_id=initial.object_id,
        command=TransitionStorageObjectRequest(
            action=LifecyclePolicyAction.ARCHIVE,
            reason="archive an inactive object after review",
        ),
        actor=actor(),
        if_match=cold.etag,
        idempotency_key="archive-1",
        request_id="archive-1",
    )
    assert archived.status is StorageObjectStatus.ARCHIVED
    assert archived.storage_tier is StorageTier.ARCHIVE


def test_references_retention_holds_and_protected_roles_block_manual_cleanup() -> None:
    service, repository, operator = build_service()
    body = b"protected"
    protected = record(body, role=ObjectRole.RAW, references=1)
    seed_object(repository, operator, protected, body)

    with pytest.raises(ProblemException) as blocked:
        service.trash_object(
            project_id="project-a",
            object_id=protected.object_id,
            command=TrashStorageObjectRequest(reason="must not bypass references"),
            actor=actor(),
            if_match=protected.etag,
            idempotency_key="blocked-trash",
            request_id="blocked-trash",
        )
    assert blocked.value.problem.code == "STORAGE_OBJECT_ACTION_BLOCKED"
    assert set(blocked.value.problem.details["blocked_reasons"]) == {
        "ACTIVE_REFERENCE",
        "PROTECTED_OBJECT",
    }
    assert operator.objects[protected.object_key] == body


def test_multipart_abort_calls_provider_once_and_is_replay_safe() -> None:
    service, repository, operator = build_service()
    multipart = ManagedMultipartUploadRecord(
        multipart_id="multipart-1",
        project_id="project-a",
        display_key="uploads/large.mcap",
        object_key="uploads/large.mcap",
        upload_id="provider-upload-1",
        received_bytes="1048576",
        part_count=1,
        status="ACTIVE",
        started_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(minutes=1),
        version=1,
        etag=ResourceVersion(1).etag,
    )
    repository.seed_managed_multipart(multipart)

    aborted = service.abort_multipart(
        project_id="project-a",
        multipart_id=multipart.multipart_id,
        reason="operator cancelled an abandoned upload",
        actor=actor(),
        if_match=multipart.etag,
        idempotency_key="abort-1",
        request_id="abort-1",
    )
    assert aborted.status == "ABORTED"
    assert (multipart.object_key, multipart.upload_id) in operator.aborted


def test_dry_run_requires_independent_hash_bound_approval_before_queueing() -> None:
    service, repository, operator = build_service()
    body = b"rebuildable-cache"
    candidate = record(body, role=ObjectRole.REBUILDABLE_DERIVATIVE)
    seed_object(repository, operator, candidate, body)
    created = service.create_policy(
        project_id="project-a",
        command=CreateLifecyclePolicy(
            name="rebuildable cache cleanup",
            business_category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
            object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
            action=LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE,
            minimum_age_days=30,
            priority=100,
        ),
        actor=actor(),
        idempotency_key="policy-create",
        request_id="policy-create",
    ).policy
    assert created is not None
    enabled = service.enable_policy(
        project_id="project-a",
        policy_id=created.policy_id,
        actor=actor(),
        if_match=created.etag,
        idempotency_key="policy-enable",
        request_id="policy-enable",
    ).policy
    assert enabled is not None

    planned = service.create_lifecycle_dry_run_command(
        project_id="project-a",
        command=LifecycleDryRunRequest(policy_id=enabled.policy_id, policy_etag=enabled.etag),
        actor=actor(),
        idempotency_key="dry-run-1",
        request_id="dry-run-1",
    ).execution
    assert planned.status.value == "AWAITING_APPROVAL"
    assert planned.total_items == 1
    assert planned.items[0].status == "PENDING"

    with pytest.raises(ProblemException) as self_approval:
        service.approve_lifecycle_execution_command(
            project_id="project-a",
            execution_id=planned.execution_id,
            command=ApproveLifecycleExecutionRequest(
                plan_hash=planned.plan_hash,
                justification="requester cannot approve their own physical execution",
            ),
            actor=actor(),
            idempotency_key="approval-self",
            request_id="approval-self",
        )
    assert self_approval.value.problem.code == "LIFECYCLE_SELF_APPROVAL_DENIED"

    approved = service.approve_lifecycle_execution_command(
        project_id="project-a",
        execution_id=planned.execution_id,
        command=ApproveLifecycleExecutionRequest(
            plan_hash=planned.plan_hash,
            justification="independent reviewer verified the immutable dry-run plan",
        ),
        actor=actor("storage-approver"),
        idempotency_key="approval-1",
        request_id="approval-1",
    ).execution
    assert approved.approval_id is not None
    queued = service.start_lifecycle_execution_command(
        project_id="project-a",
        execution_id=planned.execution_id,
        command=StartLifecycleExecutionRequest(
            approval_id=approved.approval_id,
            plan_hash=planned.plan_hash,
        ),
        actor=actor(),
        idempotency_key="start-1",
        request_id="start-1",
    ).execution
    assert queued.status.value == "QUEUED"
    assert queued.dry_run is False

    logs = service.list_lifecycle_execution_logs(
        project_id="project-a",
        execution_id=queued.execution_id,
        actor=actor(),
        limit=2,
    )
    assert [item.event for item in logs.items] == [
        "storage.lifecycle_execution.dry_run_created",
        "storage.lifecycle_execution.approved",
    ]
    assert logs.page_info.has_next_page is True
    assert logs.page_info.end_cursor is not None
    next_logs = service.list_lifecycle_execution_logs(
        project_id="project-a",
        execution_id=queued.execution_id,
        actor=actor(),
        cursor=logs.page_info.end_cursor,
        limit=2,
    )
    assert [item.event for item in next_logs.items] == ["storage.lifecycle_execution.queued"]

    cancelled = service.cancel_lifecycle_execution_command(
        project_id="project-a",
        execution_id=queued.execution_id,
        command=CancelLifecycleExecutionRequest(
            reason="operator cancelled before worker dispatch",
        ),
        actor=actor(),
        idempotency_key="cancel-1",
        request_id="cancel-1",
    ).execution
    assert cancelled.status.value == "CANCELLED"
    replayed = service.cancel_lifecycle_execution_command(
        project_id="project-a",
        execution_id=queued.execution_id,
        command=CancelLifecycleExecutionRequest(
            reason="operator cancelled before worker dispatch",
        ),
        actor=actor(),
        idempotency_key="cancel-1",
        request_id="cancel-replay",
    )
    assert replayed.replayed is True
    assert replayed.execution == cancelled


def test_schedule_occurrence_creates_only_an_idempotent_dry_run_and_is_manageable() -> None:
    service, repository, operator = build_service()
    body = b"scheduled-rebuildable-cache"
    candidate = record(body, role=ObjectRole.REBUILDABLE_DERIVATIVE)
    seed_object(repository, operator, candidate, body)
    policy = service.create_policy(
        project_id="project-a",
        command=CreateLifecyclePolicy(
            name="scheduled cache cleanup",
            business_category=BusinessCapacityCategory.ANNOTATION_COMPLETE,
            object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
            action=LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE,
            minimum_age_days=30,
            priority=200,
        ),
        actor=actor(),
        idempotency_key="scheduled-policy-create",
        request_id="scheduled-policy-create",
    ).policy
    assert policy is not None
    policy = service.enable_policy(
        project_id="project-a",
        policy_id=policy.policy_id,
        actor=actor(),
        if_match=policy.etag,
        idempotency_key="scheduled-policy-enable",
        request_id="scheduled-policy-enable",
    ).policy
    assert policy is not None
    schedule = service.create_lifecycle_schedule_command(
        project_id="project-a",
        command=CreateLifecycleScheduleRequest(
            policy_id=policy.policy_id,
            interval_seconds=3600,
            first_run_at=NOW + timedelta(minutes=5),
        ),
        actor=actor(),
        idempotency_key="schedule-create",
        request_id="schedule-create",
    ).schedule
    assert schedule is not None

    execution = service.run_due_lifecycle_schedule(
        project_id="project-a",
        schedule_id=schedule.schedule_id,
        scheduled_for=NOW + timedelta(minutes=5),
        actor=actor("storage-schedule-dispatcher"),
        request_id="schedule-due",
    )
    assert execution is not None
    assert execution.status.value == "AWAITING_APPROVAL"
    linked = service.get_lifecycle_schedule(
        project_id="project-a",
        schedule_id=schedule.schedule_id,
        actor=actor(),
    )
    assert linked.last_execution_id == execution.execution_id
    replay = service.run_due_lifecycle_schedule(
        project_id="project-a",
        schedule_id=schedule.schedule_id,
        scheduled_for=NOW + timedelta(minutes=5),
        actor=actor("storage-schedule-dispatcher"),
        request_id="schedule-due-replay",
    )
    assert replay == execution
    assert (
        service.get_lifecycle_schedule(
            project_id="project-a",
            schedule_id=schedule.schedule_id,
            actor=actor(),
        ).version
        == linked.version
    )

    updated = service.update_lifecycle_schedule_command(
        project_id="project-a",
        schedule_id=schedule.schedule_id,
        command=UpdateLifecycleScheduleRequest(
            interval_seconds=7200,
            next_run_at=NOW + timedelta(hours=2),
        ),
        actor=actor(),
        if_match=linked.etag,
        idempotency_key="schedule-update",
        request_id="schedule-update",
    ).schedule
    assert updated is not None
    assert updated.interval_seconds == 7200
    paused = service.pause_lifecycle_schedule_command(
        project_id="project-a",
        schedule_id=schedule.schedule_id,
        actor=actor(),
        if_match=updated.etag,
        idempotency_key="schedule-pause",
        request_id="schedule-pause",
    ).schedule
    assert paused is not None
    assert paused.enabled is False
    deleted = service.delete_lifecycle_schedule_command(
        project_id="project-a",
        schedule_id=schedule.schedule_id,
        actor=actor(),
        if_match=paused.etag,
        idempotency_key="schedule-delete",
        request_id="schedule-delete",
    )
    assert deleted.schedule is None

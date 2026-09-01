from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.storage.models import (
    BusinessCapacityCategory,
    CreateLifecyclePolicy,
    LifecycleExecutionCandidate,
    LifecycleExecutionRequest,
    LifecyclePolicyAction,
    LifecyclePolicyState,
    ObjectRole,
    UpdateLifecyclePolicy,
)
from hc_data_platform.storage.repository import InMemoryStorageRepository
from hc_data_platform.storage.service import StorageGovernanceService

NOW = datetime(2026, 8, 17, 3, tzinfo=timezone.utc)


def operator(project_id: str = "project-a") -> AuthContext:
    return AuthContext.service(
        subject_id="storage-worker-admin",
        capabilities={
            "storage.lifecycle.read",
            "storage.lifecycle.manage",
            "storage.lifecycle.execute",
            "storage.lifecycle.approve",
        },
        project_ids={project_id},
    )


def capability_actor(*capabilities: str, project_id: str = "project-a") -> AuthContext:
    return AuthContext(
        subject_id="storage-capability-actor",
        project_ids=frozenset({project_id}),
        region_codes=frozenset(),
        capabilities=frozenset(capabilities),
        scope_pairs=frozenset({(project_id, None)}),
    )


def command(
    *,
    name: str = "待标注保留复核",
    category: BusinessCapacityCategory = BusinessCapacityCategory.PENDING_ANNOTATION,
    role: ObjectRole = ObjectRole.OTHER,
    action: LifecyclePolicyAction = LifecyclePolicyAction.REVIEW_EXPIRATION,
    priority: int = 100,
) -> CreateLifecyclePolicy:
    return CreateLifecyclePolicy(
        name=name,
        business_category=category,
        object_role=role,
        action=action,
        minimum_age_days=30,
        priority=priority,
    )


def service() -> StorageGovernanceService:
    ids = iter(f"id-{index:03d}" for index in range(100))
    return StorageGovernanceService(
        InMemoryStorageRepository(),
        cursor_secret="test-secret",
        clock=lambda: NOW,
        id_factory=lambda: next(ids),
    )


def test_policy_crud_enable_pause_audit_and_idempotency() -> None:
    target = service()
    actor = operator()
    created = target.create_policy(
        project_id="project-a",
        command=command(),
        actor=actor,
        idempotency_key="create-1",
        request_id="request-create",
    )
    replay = target.create_policy(
        project_id="project-a",
        command=command(),
        actor=actor,
        idempotency_key="create-1",
        request_id="request-create-retry",
    )
    assert replay.replayed is True
    assert replay.policy == created.policy
    assert created.policy is not None

    updated = target.update_policy(
        project_id="project-a",
        policy_id=created.policy.policy_id,
        command=UpdateLifecyclePolicy(
            **{
                **command().model_dump(),
                "name": "待标注保留复核（更新）",
                "minimum_age_days": 45,
            }
        ),
        actor=actor,
        if_match=created.policy.etag,
        idempotency_key="update-1",
        request_id="request-update",
    )
    assert updated.policy is not None
    assert updated.policy.version == 2

    enabled = target.enable_policy(
        project_id="project-a",
        policy_id=created.policy.policy_id,
        actor=actor,
        if_match=updated.policy.etag,
        idempotency_key="enable-1",
        request_id="request-enable",
    )
    assert enabled.policy is not None
    assert enabled.policy.state is LifecyclePolicyState.ENABLED

    with pytest.raises(ProblemException) as cannot_delete:
        target.delete_policy(
            project_id="project-a",
            policy_id=created.policy.policy_id,
            actor=actor,
            if_match=enabled.policy.etag,
            idempotency_key="delete-enabled",
            request_id="request-delete",
        )
    assert cannot_delete.value.problem.code == "LIFECYCLE_POLICY_ENABLED"

    paused = target.pause_policy(
        project_id="project-a",
        policy_id=created.policy.policy_id,
        actor=actor,
        if_match=enabled.policy.etag,
        idempotency_key="pause-1",
        request_id="request-pause",
    )
    assert paused.policy is not None
    assert paused.policy.state is LifecyclePolicyState.PAUSED
    target.delete_policy(
        project_id="project-a",
        policy_id=created.policy.policy_id,
        actor=actor,
        if_match=paused.policy.etag,
        idempotency_key="delete-paused",
        request_id="request-delete",
    )

    audit = target.list_audit(project_id="project-a", actor=actor, limit=2)
    assert len(audit.items) == 2
    assert audit.page_info.has_next_page is True
    next_audit = target.list_audit(
        project_id="project-a",
        actor=actor,
        cursor=audit.page_info.end_cursor,
        limit=10,
    )
    assert len(next_audit.items) == 3
    assert [item.action for item in (*audit.items, *next_audit.items)] == [
        "storage.lifecycle_policy.created",
        "storage.lifecycle_policy.updated",
        "storage.lifecycle_policy.enabled",
        "storage.lifecycle_policy.paused",
        "storage.lifecycle_policy.deleted",
    ]
    previous_audit = target.list_audit(
        project_id="project-a",
        actor=actor,
        cursor=next_audit.page_info.start_cursor,
        limit=2,
    )
    assert previous_audit.items == audit.items


def test_policy_conflict_etag_scope_and_protected_target_negative_cases() -> None:
    target = service()
    actor = operator()
    first = target.create_policy(
        project_id="project-a",
        command=command(name="策略一"),
        actor=actor,
        idempotency_key="create-first",
        request_id="request-first",
    ).policy
    second = target.create_policy(
        project_id="project-a",
        command=command(name="策略二"),
        actor=actor,
        idempotency_key="create-second",
        request_id="request-second",
    ).policy
    assert first is not None and second is not None
    target.enable_policy(
        project_id="project-a",
        policy_id=first.policy_id,
        actor=actor,
        if_match=first.etag,
        idempotency_key="enable-first",
        request_id="request-enable-first",
    )
    with pytest.raises(ProblemException) as conflict:
        target.enable_policy(
            project_id="project-a",
            policy_id=second.policy_id,
            actor=actor,
            if_match=second.etag,
            idempotency_key="enable-second",
            request_id="request-enable-second",
        )
    assert conflict.value.problem.code == "LIFECYCLE_POLICY_CONFLICT"

    with pytest.raises(ProblemException) as stale:
        target.update_policy(
            project_id="project-a",
            policy_id=first.policy_id,
            command=UpdateLifecyclePolicy(**command(name="旧更新").model_dump()),
            actor=actor,
            if_match=first.etag,
            idempotency_key="stale-update",
            request_id="request-stale",
        )
    assert stale.value.problem.code == "ETAG_MISMATCH"

    with pytest.raises(ProblemException) as scope_denied:
        target.list_policies(project_id="project-a", actor=operator("project-b"))
    assert scope_denied.value.problem.code == "SERVICE_SCOPE_REQUIRED"

    with pytest.raises(ValidationError):
        command(
            name="非法 Raw 清理",
            category=BusinessCapacityCategory.RAW,
            role=ObjectRole.RAW,
            action=LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE,
        )


def test_lifecycle_requires_precise_read_and_manage_capabilities() -> None:
    target = service()
    manager = capability_actor("storage.lifecycle.manage")
    created = target.create_policy(
        project_id="project-a",
        command=command(),
        actor=manager,
        idempotency_key="capability-create",
        request_id="capability-create",
    )
    assert created.policy is not None

    readable = target.list_policies(
        project_id="project-a",
        actor=capability_actor("storage.lifecycle.read"),
    )
    assert [item.policy_id for item in readable.items] == [created.policy.policy_id]

    with pytest.raises(ProblemException) as read_denied:
        target.list_policies(
            project_id="project-a",
            actor=capability_actor("storage.overview.read"),
        )
    assert read_denied.value.problem.code == "CAPABILITY_REQUIRED"

    with pytest.raises(ProblemException) as manage_denied:
        target.create_policy(
            project_id="project-a",
            command=command(name="只读不能新建"),
            actor=capability_actor("storage.lifecycle.read"),
            idempotency_key="capability-read-only",
            request_id="capability-read-only",
        )
    assert manage_denied.value.problem.code == "CAPABILITY_REQUIRED"


def test_execution_guard_requires_bound_approval_and_blocks_every_protected_object() -> None:
    request = LifecycleExecutionRequest(
        execution_id="execution-1",
        project_id="project-a",
        policy_id="policy-1",
        policy_version=1,
        action=LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE,
        production=True,
        production_execution_approved=False,
        candidates=(
            LifecycleExecutionCandidate(
                physical_instance_id="raw-object",
                logical_object_id="raw-logical",
                object_role=ObjectRole.RAW,
                rebuild_source_id=None,
                active_reference_count=1,
                protection_verified=False,
            ),
            LifecycleExecutionCandidate(
                physical_instance_id="manifest-object",
                logical_object_id="manifest-logical",
                object_role=ObjectRole.MANIFEST,
                protection_verified=True,
            ),
            LifecycleExecutionCandidate(
                physical_instance_id="published-manifest-object",
                logical_object_id="published-manifest-logical",
                object_role=ObjectRole.PUBLISHED_MANIFEST,
                protection_verified=True,
            ),
        ),
    )

    result = StorageGovernanceService.validate_execution(request)

    assert result.status == "BLOCKED"
    assert "LIFECYCLE_APPROVAL_REQUIRED" in result.blocked_reasons
    assert "PROTECTED_OBJECT:raw-object" in result.blocked_reasons
    assert "PROTECTED_OBJECT:manifest-object" in result.blocked_reasons
    assert "PROTECTED_OBJECT:published-manifest-object" in result.blocked_reasons

    approval_bit_without_bound_evidence_is_rejected = request.model_copy(
        update={
            "production_execution_approved": True,
            "candidates": (
                LifecycleExecutionCandidate(
                    physical_instance_id="rebuildable-cache",
                    logical_object_id="rebuildable-logical",
                    object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                    rebuild_source_id="raw-source",
                    protection_verified=True,
                ),
            ),
        }
    )
    approved_result = StorageGovernanceService.validate_execution(
        approval_bit_without_bound_evidence_is_rejected
    )
    assert approved_result.status == "BLOCKED"
    assert approved_result.blocked_reasons == ("LIFECYCLE_APPROVAL_REQUIRED",)
    assert result.processed_instance_ids == ()

    bound_approval = approval_bit_without_bound_evidence_is_rejected.model_copy(
        update={"approval_id": "approval-1", "plan_hash": "a" * 64}
    )
    validated = StorageGovernanceService.validate_execution(bound_approval)
    assert validated.status == "VALIDATED"
    assert validated.processed_instance_ids == ("rebuildable-cache",)


def test_non_production_rebuildable_cache_can_pass_server_guard() -> None:
    request = LifecycleExecutionRequest(
        execution_id="execution-safe-sandbox",
        project_id="project-a",
        policy_id="policy-cache",
        policy_version=1,
        action=LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE,
        production=False,
        candidates=(
            LifecycleExecutionCandidate(
                physical_instance_id="cache-1",
                logical_object_id="cache-logical-1",
                object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                rebuild_source_id="raw-logical-1",
                active_reference_count=0,
                protection_verified=True,
            ),
        ),
    )
    result = StorageGovernanceService.validate_execution(request)
    assert result.status == "VALIDATED"
    assert result.processed_instance_ids == ("cache-1",)

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from hc_data_platform.core.context import current_request_context, select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec, PageInfo
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    request_fingerprint,
)
from hc_data_platform.security.scope import ScopeGuard
from hc_data_platform.security.versioning import ResourceVersion

from .models import (
    ApproveLifecycleExecutionRequest,
    BusinessCapacityCategory,
    CancelLifecycleExecutionRequest,
    CapacityCategoryTotal,
    CapacityGrowth,
    CapacityHistory,
    CapacityHistoryPoint,
    CapacityInventoryFact,
    CapacityInventoryPage,
    CapacityPortfolio,
    CapacityReconciliation,
    CapacitySnapshot,
    CreateLifecyclePolicy,
    CreateLifecycleScheduleRequest,
    InventoryDisposition,
    LifecycleAuditEvent,
    LifecycleAuditPage,
    LifecycleDryRunRequest,
    LifecycleExecution,
    LifecycleExecutionCandidate,
    LifecycleExecutionItem,
    LifecycleExecutionLogPage,
    LifecycleExecutionPage,
    LifecycleExecutionRequest,
    LifecycleExecutionResult,
    LifecycleExecutionStatus,
    LifecyclePolicy,
    LifecyclePolicyAction,
    LifecyclePolicyPage,
    LifecyclePolicyState,
    LifecycleSchedule,
    LifecycleSchedulePage,
    ManagedMultipartUpload,
    ManagedStorageObject,
    ManagedStorageObjectPage,
    ManagedStorageObjectRecord,
    ProjectCapacitySummary,
    RestoreStorageObjectRequest,
    RetryLifecycleExecutionRequest,
    StartLifecycleExecutionRequest,
    StorageObjectAction,
    StorageObjectDownloadGrant,
    StorageObjectOperation,
    StorageObjectStatus,
    StorageTier,
    TransitionStorageObjectRequest,
    TrashStorageObjectRequest,
    UpdateLifecyclePolicy,
    UpdateLifecycleScheduleRequest,
    evaluate_execution_protection,
)
from .object_store import DisabledStorageObjectOperator, StorageObjectOperator
from .ports import StorageRepository
from .repository import InMemoryStorageRepository

Clock = Callable[[], datetime]

_CAPACITY_READ_CAPABILITY = "storage.overview.read"
_LIFECYCLE_READ_CAPABILITY = "storage.lifecycle.read"
_LIFECYCLE_MANAGE_CAPABILITY = "storage.lifecycle.manage"
_OBJECT_READ_CAPABILITY = "storage.object.read"
_OBJECT_MANAGE_CAPABILITY = "storage.object.manage"
_LIFECYCLE_EXECUTE_CAPABILITY = "storage.lifecycle.execute"
_LIFECYCLE_APPROVE_CAPABILITY = "storage.lifecycle.approve"


@dataclass(frozen=True, slots=True)
class PolicyMutationResult:
    policy: LifecyclePolicy | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class ExecutionMutationResult:
    execution: LifecycleExecution
    replayed: bool


@dataclass(frozen=True, slots=True)
class ScheduleMutationResult:
    schedule: LifecycleSchedule | None
    replayed: bool


def _not_found(resource: str) -> Exception:
    return problem(
        status=404,
        code=f"{resource.upper()}_NOT_FOUND",
        title=f"{resource.replace('_', ' ').title()} not found",
        detail="The requested scoped resource does not exist.",
    )


class StorageGovernanceService:
    def __init__(
        self,
        repository: StorageRepository,
        *,
        cursor_secret: str = "storage-cursor-development-secret",
        idempotency: IdempotencyStore | None = None,
        clock: Clock = lambda: datetime.now(timezone.utc),
        id_factory: Callable[[], str] = lambda: str(uuid4()),
        object_operator: StorageObjectOperator | None = None,
        download_ttl_seconds: int = 300,
    ) -> None:
        self._repository = repository
        self._cursor = CursorCodec(cursor_secret)
        self._idempotency = idempotency or InMemoryIdempotencyStore()
        self._clock = clock
        self._id_factory = id_factory
        self._object_operator = object_operator or DisabledStorageObjectOperator()
        if download_ttl_seconds < 60 or download_ttl_seconds > 3600:
            raise ValueError("download TTL must be between 60 and 3600 seconds")
        self._download_ttl_seconds = download_ttl_seconds

    @classmethod
    def in_memory(cls) -> StorageGovernanceService:
        return cls(InMemoryStorageRepository())

    @property
    def repository(self) -> StorageRepository:
        return self._repository

    def record_inventory_snapshot(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        facts: Sequence[CapacityInventoryFact],
    ) -> CapacitySnapshot:
        """Worker-facing ingestion that validates reconciliation before publication."""

        snapshot = self._reconcile(project_id=project_id, snapshot_id=snapshot_id, facts=facts)
        self._repository.replace_inventory_snapshot(
            snapshot=snapshot,
            facts=facts,
        )
        return snapshot

    def capacity_snapshot(
        self,
        *,
        project_id: str,
        actor: AuthContext,
        snapshot_id: str | None = None,
    ) -> CapacitySnapshot:
        self._authorize(actor, project_id, _CAPACITY_READ_CAPABILITY)
        resolved = snapshot_id or self._repository.latest_snapshot_id(project_id=project_id)
        if resolved is None:
            raise _not_found("capacity_snapshot")
        facts = self._repository.inventory_facts(project_id=project_id, snapshot_id=resolved)
        if not facts:
            raise _not_found("capacity_snapshot")
        return self._reconcile(
            project_id=project_id,
            snapshot_id=resolved,
            facts=facts,
            duplicate_rows_ignored=self._repository.inventory_duplicate_rows_ignored(
                project_id=project_id,
                snapshot_id=resolved,
            ),
        )

    def capacity_history(
        self,
        *,
        project_id: str,
        actor: AuthContext,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> CapacityHistory:
        """Return one latest immutable capacity fact per UTC day.

        The 31-day ceiling is an interactive API boundary. It keeps trend reads
        bounded; longer reporting belongs to a later export/aggregation workflow.
        """

        self._authorize(actor, project_id, _CAPACITY_READ_CAPABILITY)
        now = self._clock().astimezone(timezone.utc)
        end = (window_end or now).astimezone(timezone.utc)
        start = (window_start or end - timedelta(days=30)).astimezone(timezone.utc)
        if start > end or end - start > timedelta(days=31):
            raise problem(
                status=422,
                code="CAPACITY_HISTORY_RANGE_INVALID",
                title="Capacity history range is invalid",
                detail="Select a chronological capacity window no longer than 31 days.",
            )
        items = self._repository.list_capacity_history(
            project_id=project_id,
            window_start=start,
            window_end=end,
            limit=31,
        )
        if not items:
            raise _not_found("capacity_snapshot")
        return CapacityHistory(
            project_id=project_id,
            window_start=start,
            window_end=end,
            items=items,
            growth=self._growth(items),
        )

    def inventory_page(
        self,
        *,
        project_id: str,
        actor: AuthContext,
        snapshot_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CapacityInventoryPage:
        self._authorize(actor, project_id, _CAPACITY_READ_CAPABILITY)
        self._validate_limit(limit)
        resolved = snapshot_id or self._repository.latest_snapshot_id(project_id=project_id)
        if resolved is None:
            raise _not_found("capacity_snapshot")
        anchor, direction = self._decode_cursor(
            cursor,
            kind="inventory",
            project_id=project_id,
            snapshot_id=resolved,
        )
        items = self._repository.list_inventory(
            project_id=project_id,
            snapshot_id=resolved,
            anchor_physical_instance_id=anchor,
            before=direction == "before",
            limit=limit + 1,
        )
        has_more = len(items) > limit
        visible = items[:limit]
        if direction == "before":
            visible = tuple(reversed(visible))
        return CapacityInventoryPage(
            snapshot_id=resolved,
            project_id=project_id,
            items=visible,
            page_info=self._page_info(
                visible,
                has_next=cursor is not None if direction == "before" else has_more,
                has_previous=has_more if direction == "before" else cursor is not None,
                start_cursor_for=lambda item: self._encode_cursor(
                    kind="inventory",
                    project_id=project_id,
                    snapshot_id=resolved,
                    value=item.physical_instance_id,
                    direction="before",
                ),
                end_cursor_for=lambda item: self._encode_cursor(
                    kind="inventory",
                    project_id=project_id,
                    snapshot_id=resolved,
                    value=item.physical_instance_id,
                    direction="after",
                ),
            ),
        )

    def create_policy(
        self,
        *,
        project_id: str,
        command: CreateLifecyclePolicy,
        actor: AuthContext,
        idempotency_key: str,
        request_id: str,
    ) -> PolicyMutationResult:
        self._authorize(actor, project_id, _LIFECYCLE_MANAGE_CAPABILITY)

        def create() -> LifecyclePolicy:
            now = self._clock()
            policy = LifecyclePolicy(
                policy_id=self._id_factory(),
                project_id=project_id,
                state=LifecyclePolicyState.DRAFT,
                version=1,
                etag=ResourceVersion(1).etag,
                created_at=now,
                updated_at=now,
                **command.model_dump(),
            )
            self._require_unique_name(policy)
            audit_event = self._audit_event(
                policy=policy,
                actor=actor,
                action="storage.lifecycle_policy.created",
                request_id=request_id,
                before=None,
                after=policy,
            )
            saved = self._repository.save_policy(
                policy,
                expected_version=None,
                audit_event=audit_event,
            )
            return saved

        outcome = self._idempotency.execute(
            scope=f"storage.lifecycle.policy.create:{project_id}",
            key=idempotency_key,
            payload=command.model_dump(mode="json"),
            action=create,
        )
        return PolicyMutationResult(outcome.value, outcome.replayed)

    def get_policy(
        self,
        *,
        project_id: str,
        policy_id: str,
        actor: AuthContext,
    ) -> LifecyclePolicy:
        self._authorize(actor, project_id, _LIFECYCLE_READ_CAPABILITY)
        policy = self._repository.get_policy(project_id=project_id, policy_id=policy_id)
        if policy is None:
            raise _not_found("lifecycle_policy")
        return policy

    def list_policies(
        self,
        *,
        project_id: str,
        actor: AuthContext,
        cursor: str | None = None,
        limit: int = 50,
    ) -> LifecyclePolicyPage:
        self._authorize(actor, project_id, _LIFECYCLE_READ_CAPABILITY)
        self._validate_limit(limit)
        anchor, direction = self._decode_cursor(cursor, kind="policies", project_id=project_id)
        items = self._repository.list_policies(
            project_id=project_id,
            anchor_policy_id=anchor,
            before=direction == "before",
            limit=limit + 1,
        )
        has_more = len(items) > limit
        visible = items[:limit]
        if direction == "before":
            visible = tuple(reversed(visible))
        return LifecyclePolicyPage(
            project_id=project_id,
            items=visible,
            page_info=self._page_info(
                visible,
                has_next=cursor is not None if direction == "before" else has_more,
                has_previous=has_more if direction == "before" else cursor is not None,
                start_cursor_for=lambda item: self._encode_cursor(
                    kind="policies",
                    project_id=project_id,
                    value=item.policy_id,
                    direction="before",
                ),
                end_cursor_for=lambda item: self._encode_cursor(
                    kind="policies",
                    project_id=project_id,
                    value=item.policy_id,
                    direction="after",
                ),
            ),
        )

    def update_policy(
        self,
        *,
        project_id: str,
        policy_id: str,
        command: UpdateLifecyclePolicy,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> PolicyMutationResult:
        return self._change_policy(
            project_id=project_id,
            policy_id=policy_id,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
            action_name="storage.lifecycle_policy.updated",
            payload={"command": command.model_dump(mode="json"), "if_match": if_match},
            if_match=if_match,
            transform=lambda current, next_version: LifecyclePolicy.model_validate(
                {
                    **current.model_dump(),
                    **command.model_dump(),
                    "version": next_version.value,
                    "etag": next_version.etag,
                    "updated_at": self._clock(),
                }
            ),
        )

    def enable_policy(
        self,
        *,
        project_id: str,
        policy_id: str,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> PolicyMutationResult:
        def enable(current: LifecyclePolicy, version: ResourceVersion) -> LifecyclePolicy:
            if current.state is LifecyclePolicyState.ENABLED:
                return current
            candidate = LifecyclePolicy.model_validate(
                {
                    **current.model_dump(),
                    "state": LifecyclePolicyState.ENABLED,
                    "version": version.value,
                    "etag": version.etag,
                    "updated_at": self._clock(),
                }
            )
            self._require_no_enabled_conflict(candidate)
            return candidate

        return self._change_policy(
            project_id=project_id,
            policy_id=policy_id,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
            action_name="storage.lifecycle_policy.enabled",
            payload={"if_match": if_match},
            if_match=if_match,
            transform=enable,
        )

    def pause_policy(
        self,
        *,
        project_id: str,
        policy_id: str,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> PolicyMutationResult:
        def pause(current: LifecyclePolicy, version: ResourceVersion) -> LifecyclePolicy:
            if current.state is LifecyclePolicyState.PAUSED:
                return current
            return LifecyclePolicy.model_validate(
                {
                    **current.model_dump(),
                    "state": LifecyclePolicyState.PAUSED,
                    "version": version.value,
                    "etag": version.etag,
                    "updated_at": self._clock(),
                }
            )

        return self._change_policy(
            project_id=project_id,
            policy_id=policy_id,
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
            action_name="storage.lifecycle_policy.paused",
            payload={"if_match": if_match},
            if_match=if_match,
            transform=pause,
        )

    def delete_policy(
        self,
        *,
        project_id: str,
        policy_id: str,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> PolicyMutationResult:
        self._authorize(actor, project_id, _LIFECYCLE_MANAGE_CAPABILITY)

        def delete() -> None:
            current = self.get_policy(project_id=project_id, policy_id=policy_id, actor=actor)
            version = ResourceVersion(current.version)
            version.require(if_match)
            if current.state is LifecyclePolicyState.ENABLED:
                raise problem(
                    status=409,
                    code="LIFECYCLE_POLICY_ENABLED",
                    title="Enabled policy cannot be deleted",
                    detail="Pause the lifecycle policy before deleting it.",
                )
            if self._repository.has_schedule_for_policy(
                project_id=project_id,
                policy_id=policy_id,
            ):
                raise problem(
                    status=409,
                    code="LIFECYCLE_POLICY_SCHEDULED",
                    title="Scheduled policy cannot be deleted",
                    detail="Delete every lifecycle schedule before deleting its policy.",
                )
            self._repository.delete_policy(
                project_id=project_id,
                policy_id=policy_id,
                expected_version=current.version,
                audit_event=self._audit_event(
                    policy=current,
                    actor=actor,
                    action="storage.lifecycle_policy.deleted",
                    request_id=request_id,
                    before=current,
                    after=None,
                ),
            )

        outcome = self._idempotency.execute(
            scope=f"storage.lifecycle.policy.delete:{project_id}:{policy_id}",
            key=idempotency_key,
            payload={"if_match": if_match},
            action=delete,
        )
        return PolicyMutationResult(None, outcome.replayed)

    def list_audit(
        self,
        *,
        project_id: str,
        actor: AuthContext,
        cursor: str | None = None,
        limit: int = 50,
    ) -> LifecycleAuditPage:
        self._authorize(actor, project_id, _LIFECYCLE_READ_CAPABILITY)
        self._validate_limit(limit)
        anchor, direction = self._decode_cursor(cursor, kind="audit", project_id=project_id)
        items = self._repository.list_audit(
            project_id=project_id,
            anchor_audit_id=anchor,
            before=direction == "before",
            limit=limit + 1,
        )
        has_more = len(items) > limit
        visible = items[:limit]
        if direction == "before":
            visible = tuple(reversed(visible))
        return LifecycleAuditPage(
            project_id=project_id,
            items=visible,
            page_info=self._page_info(
                visible,
                has_next=cursor is not None if direction == "before" else has_more,
                has_previous=has_more if direction == "before" else cursor is not None,
                start_cursor_for=lambda item: self._encode_cursor(
                    kind="audit",
                    project_id=project_id,
                    value=item.audit_id,
                    direction="before",
                ),
                end_cursor_for=lambda item: self._encode_cursor(
                    kind="audit",
                    project_id=project_id,
                    value=item.audit_id,
                    direction="after",
                ),
            ),
        )

    def capacity_portfolio(
        self,
        *,
        project_ids: Sequence[str],
        actor: AuthContext,
    ) -> CapacityPortfolio:
        normalized = tuple(dict.fromkeys(project_id.strip() for project_id in project_ids))
        if (
            not normalized
            or len(normalized) > 100
            or any(not project_id for project_id in normalized)
        ):
            raise problem(
                status=422,
                code="CAPACITY_PROJECT_FILTER_INVALID",
                title="Capacity project filter is invalid",
                detail="Select between one and one hundred distinct projects.",
            )
        for project_id in normalized:
            self._authorize(actor, project_id, _CAPACITY_READ_CAPABILITY)

        try:
            original_context = current_request_context()
        except RuntimeError:
            original_context = None
        snapshots: list[CapacitySnapshot] = []
        try:
            for project_id in normalized:
                if original_context is not None:
                    select_request_scope(
                        project_id,
                        original_context.region_code,
                        organization_id=original_context.organization_id,
                    )
                snapshot_id = self._repository.latest_snapshot_id(project_id=project_id)
                if snapshot_id is None:
                    continue
                facts = self._repository.inventory_facts(
                    project_id=project_id,
                    snapshot_id=snapshot_id,
                )
                if facts:
                    snapshots.append(
                        self._reconcile(
                            project_id=project_id,
                            snapshot_id=snapshot_id,
                            facts=facts,
                            duplicate_rows_ignored=(
                                self._repository.inventory_duplicate_rows_ignored(
                                    project_id=project_id,
                                    snapshot_id=snapshot_id,
                                )
                            ),
                        )
                    )
        finally:
            if original_context is not None and original_context.project_id is not None:
                select_request_scope(
                    original_context.project_id,
                    original_context.region_code,
                    organization_id=original_context.organization_id,
                )

        category_bytes = {category: 0 for category in BusinessCapacityCategory}
        category_objects = {category: 0 for category in BusinessCapacityCategory}
        items: list[ProjectCapacitySummary] = []
        for snapshot in snapshots:
            for category in snapshot.categories:
                category_bytes[category.category] += int(category.candidate_bytes)
                category_objects[category.category] += category.logical_object_count
            items.append(
                ProjectCapacitySummary(
                    project_id=snapshot.project_id,
                    snapshot_id=snapshot.snapshot_id,
                    observed_at=snapshot.observed_at,
                    physical_total_bytes=snapshot.physical_total_bytes,
                    candidate_business_total_bytes=snapshot.candidate_business_total_bytes,
                    categories=snapshot.categories,
                    balanced=snapshot.reconciliation.balanced,
                )
            )
        categories = tuple(
            CapacityCategoryTotal(
                category=category,
                candidate_bytes=str(category_bytes[category]),
                logical_object_count=category_objects[category],
            )
            for category in BusinessCapacityCategory
        )
        return CapacityPortfolio(
            project_ids=normalized,
            physical_total_bytes=str(sum(int(item.physical_total_bytes) for item in items)),
            candidate_business_total_bytes=str(
                sum(int(item.candidate_business_total_bytes) for item in items)
            ),
            categories=categories,
            items=tuple(items),
        )

    def list_managed_objects(
        self,
        *,
        project_id: str,
        actor: AuthContext,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ManagedStorageObjectPage:
        self._authorize(actor, project_id, _OBJECT_READ_CAPABILITY)
        self._validate_limit(limit)
        anchor, direction = self._decode_cursor(cursor, kind="objects", project_id=project_id)
        records = self._repository.list_managed_objects(
            project_id=project_id,
            anchor_object_id=anchor,
            before=direction == "before",
            limit=limit + 1,
        )
        has_more = len(records) > limit
        visible = records[:limit]
        if direction == "before":
            visible = tuple(reversed(visible))
        now = self._clock()
        return ManagedStorageObjectPage(
            project_id=project_id,
            items=tuple(record.public(now=now) for record in visible),
            page_info=self._page_info(
                visible,
                has_next=cursor is not None if direction == "before" else has_more,
                has_previous=has_more if direction == "before" else cursor is not None,
                start_cursor_for=lambda record: self._encode_cursor(
                    kind="objects",
                    project_id=project_id,
                    value=record.object_id,
                    direction="before",
                ),
                end_cursor_for=lambda record: self._encode_cursor(
                    kind="objects",
                    project_id=project_id,
                    value=record.object_id,
                    direction="after",
                ),
            ),
        )

    def get_managed_object(
        self,
        *,
        project_id: str,
        object_id: str,
        actor: AuthContext,
    ) -> ManagedStorageObject:
        self._authorize(actor, project_id, _OBJECT_READ_CAPABILITY)
        return self._object_record(project_id=project_id, object_id=object_id).public(
            now=self._clock()
        )

    def authorize_object_download(
        self,
        *,
        project_id: str,
        object_id: str,
        actor: AuthContext,
        idempotency_key: str,
        request_id: str,
    ) -> StorageObjectDownloadGrant:
        self._authorize(actor, project_id, _OBJECT_READ_CAPABILITY)
        record = self._object_record(project_id=project_id, object_id=object_id)
        if record.status is not StorageObjectStatus.ACTIVE:
            raise problem(
                status=409,
                code="STORAGE_OBJECT_RESTORE_REQUIRED",
                title="Storage object is not online",
                detail="Restore the object before requesting a download authorization.",
            )
        operation, _ = self._begin_object_operation(
            project_id=project_id,
            object_id=object_id,
            multipart_id=None,
            action=StorageObjectAction.DOWNLOAD.value,
            payload={"object_id": object_id, "version": record.version},
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        try:
            url = self._object_operator.presign_read(record.object_key, self._download_ttl_seconds)
            now = self._clock()
            self._repository.finish_object_operation(
                project_id=project_id,
                operation_id=operation.operation_id,
                status="SUCCEEDED",
                error_code=None,
                completed_at=now,
            )
            return StorageObjectDownloadGrant(
                object_id=record.object_id,
                url=url,
                expires_at=now + timedelta(seconds=self._download_ttl_seconds),
                physical_bytes=record.physical_bytes,
                checksum_sha256=record.checksum_sha256,
            )
        except Exception:
            self._finish_operation_failed(operation)
            raise

    def trash_object(
        self,
        *,
        project_id: str,
        object_id: str,
        command: TrashStorageObjectRequest,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ManagedStorageObject:
        self._authorize(actor, project_id, _OBJECT_MANAGE_CAPABILITY)
        record = self._object_record(project_id=project_id, object_id=object_id)
        operation, replayed = self._begin_object_operation(
            project_id=project_id,
            object_id=object_id,
            multipart_id=None,
            action=StorageObjectAction.TRASH.value,
            payload={"command": command.model_dump(mode="json"), "if_match": if_match},
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        if replayed and operation.status == "SUCCEEDED":
            return self._object_record(project_id=project_id, object_id=object_id).public(
                now=self._clock()
            )
        ResourceVersion(record.version).require(if_match)
        self._require_object_action(record, StorageObjectAction.TRASH)
        now = self._clock()
        destination = self._managed_destination("trash", record)
        try:
            self._move_provider_object(record.object_key, destination, record.physical_bytes)
            updated = record.model_copy(
                update={
                    "object_key": destination,
                    "status": StorageObjectStatus.TRASHED,
                    "recoverable_until": now + timedelta(days=command.recoverable_days),
                    "version": record.version + 1,
                    "etag": ResourceVersion(record.version + 1).etag,
                    "updated_at": now,
                }
            )
            saved = self._repository.save_managed_object(
                updated,
                expected_version=record.version,
                audit_action="storage.object.trashed",
                actor_id=actor.subject_id,
                request_id=request_id,
                before=record,
            )
            self._repository.finish_object_operation(
                project_id=project_id,
                operation_id=operation.operation_id,
                status="SUCCEEDED",
                error_code=None,
                completed_at=now,
            )
            return saved.public(now=now)
        except Exception:
            self._finish_operation_failed(operation)
            raise

    def restore_object(
        self,
        *,
        project_id: str,
        object_id: str,
        command: RestoreStorageObjectRequest,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ManagedStorageObject:
        self._authorize(actor, project_id, _OBJECT_MANAGE_CAPABILITY)
        record = self._object_record(project_id=project_id, object_id=object_id)
        operation, replayed = self._begin_object_operation(
            project_id=project_id,
            object_id=object_id,
            multipart_id=None,
            action=StorageObjectAction.RESTORE.value,
            payload={"command": command.model_dump(mode="json"), "if_match": if_match},
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        if replayed and operation.status == "SUCCEEDED":
            return self._object_record(project_id=project_id, object_id=object_id).public(
                now=self._clock()
            )
        ResourceVersion(record.version).require(if_match)
        self._require_object_action(record, StorageObjectAction.RESTORE)
        now = self._clock()
        try:
            self._move_provider_object(
                record.object_key,
                record.original_object_key,
                record.physical_bytes,
                allow_existing=operation.attempt > 0,
            )
            updated = record.model_copy(
                update={
                    "object_key": record.original_object_key,
                    "status": StorageObjectStatus.ACTIVE,
                    "storage_tier": StorageTier.HOT,
                    "recoverable_until": None,
                    "version": record.version + 1,
                    "etag": ResourceVersion(record.version + 1).etag,
                    "updated_at": now,
                }
            )
            saved = self._repository.save_managed_object(
                updated,
                expected_version=record.version,
                audit_action="storage.object.restored",
                actor_id=actor.subject_id,
                request_id=request_id,
                before=record,
            )
            self._repository.finish_object_operation(
                project_id=project_id,
                operation_id=operation.operation_id,
                status="SUCCEEDED",
                error_code=None,
                completed_at=now,
            )
            return saved.public(now=now)
        except Exception:
            self._finish_operation_failed(operation)
            raise

    def transition_object(
        self,
        *,
        project_id: str,
        object_id: str,
        command: TransitionStorageObjectRequest,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ManagedStorageObject:
        self._authorize(actor, project_id, _OBJECT_MANAGE_CAPABILITY)
        record = self._object_record(project_id=project_id, object_id=object_id)
        action = (
            StorageObjectAction.ARCHIVE
            if command.action is LifecyclePolicyAction.ARCHIVE
            else StorageObjectAction.TRANSITION_TO_COLD
        )
        operation, replayed = self._begin_object_operation(
            project_id=project_id,
            object_id=object_id,
            multipart_id=None,
            action=action.value,
            payload={"command": command.model_dump(mode="json"), "if_match": if_match},
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        if replayed and operation.status == "SUCCEEDED":
            return self._object_record(project_id=project_id, object_id=object_id).public(
                now=self._clock()
            )
        ResourceVersion(record.version).require(if_match)
        self._require_object_action(record, action)
        now = self._clock()
        destination_kind = "archive" if action is StorageObjectAction.ARCHIVE else "cold"
        destination = self._managed_destination(destination_kind, record)
        try:
            self._move_provider_object(record.object_key, destination, record.physical_bytes)
            archived = action is StorageObjectAction.ARCHIVE
            updated = record.model_copy(
                update={
                    "object_key": destination,
                    "status": (
                        StorageObjectStatus.ARCHIVED if archived else StorageObjectStatus.ACTIVE
                    ),
                    "storage_tier": StorageTier.ARCHIVE if archived else StorageTier.COLD,
                    "version": record.version + 1,
                    "etag": ResourceVersion(record.version + 1).etag,
                    "updated_at": now,
                }
            )
            saved = self._repository.save_managed_object(
                updated,
                expected_version=record.version,
                audit_action=(
                    "storage.object.archived" if archived else "storage.object.tier_transitioned"
                ),
                actor_id=actor.subject_id,
                request_id=request_id,
                before=record,
            )
            self._repository.finish_object_operation(
                project_id=project_id,
                operation_id=operation.operation_id,
                status="SUCCEEDED",
                error_code=None,
                completed_at=now,
            )
            return saved.public(now=now)
        except Exception:
            self._finish_operation_failed(operation)
            raise

    def abort_multipart(
        self,
        *,
        project_id: str,
        multipart_id: str,
        reason: str,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ManagedMultipartUpload:
        self._authorize(actor, project_id, _OBJECT_MANAGE_CAPABILITY)
        record = self._repository.get_managed_multipart(
            project_id=project_id, multipart_id=multipart_id
        )
        if record is None:
            raise _not_found("storage_multipart")
        operation, replayed = self._begin_object_operation(
            project_id=project_id,
            object_id=None,
            multipart_id=multipart_id,
            action="ABORT_MULTIPART",
            payload={"reason": reason, "if_match": if_match},
            actor=actor,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        if replayed and operation.status == "SUCCEEDED":
            refreshed = self._repository.get_managed_multipart(
                project_id=project_id, multipart_id=multipart_id
            )
            assert refreshed is not None
            return refreshed.public()
        ResourceVersion(record.version).require(if_match)
        if record.status != "ACTIVE":
            raise problem(
                status=409,
                code="STORAGE_MULTIPART_NOT_ACTIVE",
                title="Multipart upload is not active",
                detail="Only an active multipart upload can be aborted.",
            )
        now = self._clock()
        try:
            self._object_operator.abort_multipart(record.object_key, record.upload_id)
            updated = record.model_copy(
                update={
                    "status": "ABORTED",
                    "version": record.version + 1,
                    "etag": ResourceVersion(record.version + 1).etag,
                    "updated_at": now,
                }
            )
            saved = self._repository.save_managed_multipart(
                updated,
                expected_version=record.version,
                audit_action="storage.multipart.aborted",
                actor_id=actor.subject_id,
                request_id=request_id,
            )
            self._repository.finish_object_operation(
                project_id=project_id,
                operation_id=operation.operation_id,
                status="SUCCEEDED",
                error_code=None,
                completed_at=now,
            )
            return saved.public()
        except Exception:
            self._finish_operation_failed(operation)
            raise

    def create_lifecycle_dry_run(
        self,
        *,
        project_id: str,
        command: LifecycleDryRunRequest,
        actor: AuthContext,
        request_id: str,
    ) -> LifecycleExecution:
        self._authorize(actor, project_id, _LIFECYCLE_EXECUTE_CAPABILITY)
        policy = self.get_policy(project_id=project_id, policy_id=command.policy_id, actor=actor)
        ResourceVersion(policy.version).require(command.policy_etag)
        if policy.state is not LifecyclePolicyState.ENABLED:
            raise problem(
                status=409,
                code="LIFECYCLE_POLICY_NOT_ENABLED",
                title="Lifecycle policy is not enabled",
                detail="Enable the policy before creating an execution plan.",
            )
        if not policy.action.physical:
            raise problem(
                status=409,
                code="LIFECYCLE_POLICY_HAS_NO_PHYSICAL_ACTION",
                title="Lifecycle policy has no physical action",
                detail="Retain and review policies do not create physical executions.",
            )
        now = self._clock()
        candidates = self._repository.policy_candidates(
            project_id=project_id,
            policy=policy,
            older_than=now - timedelta(days=policy.minimum_age_days),
            limit=command.limit,
        )
        item_pairs: list[tuple[ManagedStorageObjectRecord, LifecycleExecutionItem]] = []
        for candidate in candidates:
            guard = evaluate_execution_protection(
                LifecycleExecutionRequest(
                    execution_id="dry-run",
                    project_id=project_id,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    action=policy.action,
                    production=False,
                    candidates=(self._execution_candidate(candidate, now=now),),
                )
            )
            item_pairs.append(
                (
                    candidate,
                    LifecycleExecutionItem(
                        object_id=candidate.object_id,
                        physical_instance_id=candidate.object_id,
                        status="PENDING" if guard.status == "VALIDATED" else "BLOCKED",
                        attempt=0,
                        blocked_reasons=guard.blocked_reasons,
                    ),
                )
            )
        plan_document = {
            "project_id": project_id,
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "action": policy.action.value,
            "items": [item.model_dump(mode="json") for _, item in item_pairs],
        }
        plan_hash = canonical_hash(plan_document)
        blocked_items = sum(item.status == "BLOCKED" for _, item in item_pairs)
        pending_items = len(item_pairs) - blocked_items
        execution = LifecycleExecution(
            execution_id=self._id_factory(),
            project_id=project_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            action=policy.action,
            status=(
                LifecycleExecutionStatus.BLOCKED
                if pending_items == 0
                else LifecycleExecutionStatus.AWAITING_APPROVAL
            ),
            dry_run=True,
            plan_hash=plan_hash,
            requested_by=actor.subject_id,
            total_items=len(item_pairs),
            processed_items=0,
            blocked_items=blocked_items,
            failed_items=0,
            next_batch=0,
            items=tuple(item for _, item in item_pairs),
            created_at=now,
            updated_at=now,
        )
        return self._repository.create_execution(
            execution,
            candidates=tuple(candidate for candidate, _ in item_pairs),
            actor_id=actor.subject_id,
            request_id=request_id,
        )

    def create_lifecycle_dry_run_command(
        self,
        *,
        project_id: str,
        command: LifecycleDryRunRequest,
        actor: AuthContext,
        idempotency_key: str,
        request_id: str,
    ) -> ExecutionMutationResult:
        outcome = self._idempotency.execute(
            scope=f"storage.lifecycle.execution.dry-run:{project_id}:{command.policy_id}",
            key=idempotency_key,
            payload=command.model_dump(mode="json"),
            action=lambda: self.create_lifecycle_dry_run(
                project_id=project_id,
                command=command,
                actor=actor,
                request_id=request_id,
            ),
        )
        return ExecutionMutationResult(execution=outcome.value, replayed=outcome.replayed)

    def approve_lifecycle_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        command: ApproveLifecycleExecutionRequest,
        actor: AuthContext,
        request_id: str,
    ) -> LifecycleExecution:
        self._authorize(actor, project_id, _LIFECYCLE_APPROVE_CAPABILITY)
        current = self._execution_record(project_id=project_id, execution_id=execution_id)
        if current.status is not LifecycleExecutionStatus.AWAITING_APPROVAL:
            raise problem(
                status=409,
                code="LIFECYCLE_EXECUTION_NOT_APPROVABLE",
                title="Lifecycle execution is not approvable",
                detail="Only a current dry-run plan can be approved.",
            )
        if current.plan_hash != command.plan_hash:
            raise problem(
                status=412,
                code="LIFECYCLE_PLAN_CHANGED",
                title="Lifecycle execution plan changed",
                detail="The approval does not match the current immutable dry-run plan.",
            )
        now = self._clock()
        return self._repository.approve_execution(
            project_id=project_id,
            execution_id=execution_id,
            approval_id=self._id_factory(),
            plan_hash=command.plan_hash,
            approver_id=actor.subject_id,
            justification=command.justification,
            approved_at=now,
            expires_at=now + timedelta(hours=1),
            request_id=request_id,
        )

    def approve_lifecycle_execution_command(
        self,
        *,
        project_id: str,
        execution_id: str,
        command: ApproveLifecycleExecutionRequest,
        actor: AuthContext,
        idempotency_key: str,
        request_id: str,
    ) -> ExecutionMutationResult:
        outcome = self._idempotency.execute(
            scope=f"storage.lifecycle.execution.approve:{project_id}:{execution_id}",
            key=idempotency_key,
            payload=command.model_dump(mode="json"),
            action=lambda: self.approve_lifecycle_execution(
                project_id=project_id,
                execution_id=execution_id,
                command=command,
                actor=actor,
                request_id=request_id,
            ),
        )
        return ExecutionMutationResult(execution=outcome.value, replayed=outcome.replayed)

    def start_lifecycle_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        command: StartLifecycleExecutionRequest,
        actor: AuthContext,
        request_id: str,
    ) -> LifecycleExecution:
        self._authorize(actor, project_id, _LIFECYCLE_EXECUTE_CAPABILITY)
        current = self._execution_record(project_id=project_id, execution_id=execution_id)
        if current.status is not LifecycleExecutionStatus.APPROVED:
            raise problem(
                status=409,
                code="LIFECYCLE_EXECUTION_NOT_APPROVED",
                title="Lifecycle execution is not approved",
                detail="An independent, unexpired approval is required before execution.",
            )
        return self._repository.queue_execution(
            project_id=project_id,
            execution_id=execution_id,
            approval_id=command.approval_id,
            plan_hash=command.plan_hash,
            queued_at=self._clock(),
            request_id=request_id,
        )

    def start_lifecycle_execution_command(
        self,
        *,
        project_id: str,
        execution_id: str,
        command: StartLifecycleExecutionRequest,
        actor: AuthContext,
        idempotency_key: str,
        request_id: str,
    ) -> ExecutionMutationResult:
        outcome = self._idempotency.execute(
            scope=f"storage.lifecycle.execution.start:{project_id}:{execution_id}",
            key=idempotency_key,
            payload=command.model_dump(mode="json"),
            action=lambda: self.start_lifecycle_execution(
                project_id=project_id,
                execution_id=execution_id,
                command=command,
                actor=actor,
                request_id=request_id,
            ),
        )
        return ExecutionMutationResult(execution=outcome.value, replayed=outcome.replayed)

    def get_lifecycle_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        actor: AuthContext,
    ) -> LifecycleExecution:
        self._authorize(actor, project_id, _LIFECYCLE_READ_CAPABILITY)
        return self._execution_record(project_id=project_id, execution_id=execution_id)

    def list_lifecycle_executions(
        self,
        *,
        project_id: str,
        actor: AuthContext,
        cursor: str | None = None,
        limit: int = 50,
    ) -> LifecycleExecutionPage:
        self._authorize(actor, project_id, _LIFECYCLE_READ_CAPABILITY)
        self._validate_limit(limit)
        anchor, direction = self._decode_cursor(cursor, kind="executions", project_id=project_id)
        items = self._repository.list_executions(
            project_id=project_id,
            anchor_execution_id=anchor,
            before=direction == "before",
            limit=limit + 1,
        )
        has_more = len(items) > limit
        visible = items[:limit]
        if direction == "before":
            visible = tuple(reversed(visible))
        return LifecycleExecutionPage(
            project_id=project_id,
            items=visible,
            page_info=self._page_info(
                visible,
                has_next=cursor is not None if direction == "before" else has_more,
                has_previous=has_more if direction == "before" else cursor is not None,
                start_cursor_for=lambda item: self._encode_cursor(
                    kind="executions",
                    project_id=project_id,
                    value=item.execution_id,
                    direction="before",
                ),
                end_cursor_for=lambda item: self._encode_cursor(
                    kind="executions",
                    project_id=project_id,
                    value=item.execution_id,
                    direction="after",
                ),
            ),
        )

    def list_lifecycle_execution_logs(
        self,
        *,
        project_id: str,
        execution_id: str,
        actor: AuthContext,
        cursor: str | None = None,
        limit: int = 50,
    ) -> LifecycleExecutionLogPage:
        self._authorize(actor, project_id, _LIFECYCLE_READ_CAPABILITY)
        self._validate_limit(limit)
        self._execution_record(project_id=project_id, execution_id=execution_id)
        anchor, direction = self._decode_cursor(
            cursor,
            kind=f"execution-logs:{execution_id}",
            project_id=project_id,
        )
        try:
            anchor_sequence = None if anchor is None else int(anchor)
        except ValueError as exc:
            raise problem(
                status=400,
                code="PAGINATION_CURSOR_INVALID",
                title="Pagination cursor is invalid",
                detail="The pagination cursor does not identify an execution log.",
            ) from exc
        items = self._repository.list_execution_logs(
            project_id=project_id,
            execution_id=execution_id,
            anchor_sequence=anchor_sequence,
            before=direction == "before",
            limit=limit + 1,
        )
        has_more = len(items) > limit
        visible = items[:limit]
        if direction == "before":
            visible = tuple(reversed(visible))
        return LifecycleExecutionLogPage(
            project_id=project_id,
            execution_id=execution_id,
            items=visible,
            page_info=self._page_info(
                visible,
                has_next=cursor is not None if direction == "before" else has_more,
                has_previous=has_more if direction == "before" else cursor is not None,
                start_cursor_for=lambda item: self._encode_cursor(
                    kind=f"execution-logs:{execution_id}",
                    project_id=project_id,
                    value=str(item.sequence),
                    direction="before",
                ),
                end_cursor_for=lambda item: self._encode_cursor(
                    kind=f"execution-logs:{execution_id}",
                    project_id=project_id,
                    value=str(item.sequence),
                    direction="after",
                ),
            ),
        )

    def cancel_lifecycle_execution_command(
        self,
        *,
        project_id: str,
        execution_id: str,
        command: CancelLifecycleExecutionRequest,
        actor: AuthContext,
        idempotency_key: str,
        request_id: str,
    ) -> ExecutionMutationResult:
        self._authorize(actor, project_id, _LIFECYCLE_EXECUTE_CAPABILITY)

        def cancel() -> LifecycleExecution:
            current = self._execution_record(
                project_id=project_id,
                execution_id=execution_id,
            )
            if current.status not in {
                LifecycleExecutionStatus.AWAITING_APPROVAL,
                LifecycleExecutionStatus.APPROVED,
                LifecycleExecutionStatus.QUEUED,
                LifecycleExecutionStatus.RUNNING,
                LifecycleExecutionStatus.BLOCKED,
                LifecycleExecutionStatus.FAILED,
            }:
                raise problem(
                    status=409,
                    code="LIFECYCLE_EXECUTION_NOT_CANCELLABLE",
                    title="Lifecycle execution cannot be cancelled",
                    detail="Only a non-terminal lifecycle execution can be cancelled.",
                )
            return self._repository.cancel_execution(
                project_id=project_id,
                execution_id=execution_id,
                actor_id=actor.subject_id,
                reason=command.reason,
                cancelled_at=self._clock(),
                request_id=request_id,
            )

        outcome = self._idempotency.execute(
            scope=f"storage.lifecycle.execution.cancel:{project_id}:{execution_id}",
            key=idempotency_key,
            payload=command.model_dump(mode="json"),
            action=cancel,
        )
        return ExecutionMutationResult(execution=outcome.value, replayed=outcome.replayed)

    def retry_lifecycle_execution_command(
        self,
        *,
        project_id: str,
        execution_id: str,
        command: RetryLifecycleExecutionRequest,
        actor: AuthContext,
        idempotency_key: str,
        request_id: str,
    ) -> ExecutionMutationResult:
        self._authorize(actor, project_id, _LIFECYCLE_EXECUTE_CAPABILITY)

        def retry() -> LifecycleExecution:
            current = self._execution_record(
                project_id=project_id,
                execution_id=execution_id,
            )
            if (
                current.status
                not in {LifecycleExecutionStatus.BLOCKED, LifecycleExecutionStatus.FAILED}
                or current.plan_hash != command.plan_hash
                or current.approval_id is None
            ):
                raise problem(
                    status=409,
                    code="LIFECYCLE_EXECUTION_NOT_RETRYABLE",
                    title="Lifecycle execution cannot be retried",
                    detail="Only an approved failed or blocked production execution is retryable.",
                )
            return self._repository.retry_execution(
                project_id=project_id,
                execution_id=execution_id,
                plan_hash=command.plan_hash,
                actor_id=actor.subject_id,
                reason=command.reason,
                retried_at=self._clock(),
                request_id=request_id,
            )

        outcome = self._idempotency.execute(
            scope=f"storage.lifecycle.execution.retry:{project_id}:{execution_id}",
            key=idempotency_key,
            payload=command.model_dump(mode="json"),
            action=retry,
        )
        return ExecutionMutationResult(execution=outcome.value, replayed=outcome.replayed)

    def create_lifecycle_schedule_command(
        self,
        *,
        project_id: str,
        command: CreateLifecycleScheduleRequest,
        actor: AuthContext,
        idempotency_key: str,
        request_id: str,
    ) -> ScheduleMutationResult:
        self._authorize(actor, project_id, _LIFECYCLE_MANAGE_CAPABILITY)

        def create() -> LifecycleSchedule:
            policy = self.get_policy(
                project_id=project_id,
                policy_id=command.policy_id,
                actor=actor,
            )
            if policy.state is not LifecyclePolicyState.ENABLED or not policy.action.physical:
                raise problem(
                    status=409,
                    code="LIFECYCLE_POLICY_NOT_SCHEDULABLE",
                    title="Lifecycle policy cannot be scheduled",
                    detail="Only an enabled physical lifecycle policy can be scheduled.",
                )
            now = self._clock()
            next_run_at = command.first_run_at or now + timedelta(seconds=command.interval_seconds)
            if next_run_at <= now:
                raise problem(
                    status=422,
                    code="LIFECYCLE_SCHEDULE_TIME_INVALID",
                    title="Lifecycle schedule time is invalid",
                    detail="The first scheduled run must be in the future.",
                )
            schedule = LifecycleSchedule(
                schedule_id=self._id_factory(),
                project_id=project_id,
                policy_id=policy.policy_id,
                interval_seconds=command.interval_seconds,
                enabled=True,
                next_run_at=next_run_at,
                version=1,
                etag=ResourceVersion(1).etag,
                created_at=now,
                updated_at=now,
            )
            return self._repository.save_schedule(
                schedule,
                expected_version=None,
                actor_id=actor.subject_id,
                action="storage.lifecycle_schedule.created",
                request_id=request_id,
            )

        outcome = self._idempotency.execute(
            scope=f"storage.lifecycle.schedule.create:{project_id}",
            key=idempotency_key,
            payload=command.model_dump(mode="json"),
            action=create,
        )
        return ScheduleMutationResult(schedule=outcome.value, replayed=outcome.replayed)

    def get_lifecycle_schedule(
        self,
        *,
        project_id: str,
        schedule_id: str,
        actor: AuthContext,
    ) -> LifecycleSchedule:
        self._authorize(actor, project_id, _LIFECYCLE_READ_CAPABILITY)
        return self._schedule_record(project_id=project_id, schedule_id=schedule_id)

    def list_lifecycle_schedules(
        self,
        *,
        project_id: str,
        actor: AuthContext,
        cursor: str | None = None,
        limit: int = 50,
    ) -> LifecycleSchedulePage:
        self._authorize(actor, project_id, _LIFECYCLE_READ_CAPABILITY)
        self._validate_limit(limit)
        anchor, direction = self._decode_cursor(cursor, kind="schedules", project_id=project_id)
        items = self._repository.list_schedules(
            project_id=project_id,
            anchor_schedule_id=anchor,
            before=direction == "before",
            limit=limit + 1,
        )
        has_more = len(items) > limit
        visible = items[:limit]
        if direction == "before":
            visible = tuple(reversed(visible))
        return LifecycleSchedulePage(
            project_id=project_id,
            items=visible,
            page_info=self._page_info(
                visible,
                has_next=cursor is not None if direction == "before" else has_more,
                has_previous=has_more if direction == "before" else cursor is not None,
                start_cursor_for=lambda item: self._encode_cursor(
                    kind="schedules",
                    project_id=project_id,
                    value=item.schedule_id,
                    direction="before",
                ),
                end_cursor_for=lambda item: self._encode_cursor(
                    kind="schedules",
                    project_id=project_id,
                    value=item.schedule_id,
                    direction="after",
                ),
            ),
        )

    def update_lifecycle_schedule_command(
        self,
        *,
        project_id: str,
        schedule_id: str,
        command: UpdateLifecycleScheduleRequest,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ScheduleMutationResult:
        return self._change_schedule(
            project_id=project_id,
            schedule_id=schedule_id,
            actor=actor,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=request_id,
            action="storage.lifecycle_schedule.updated",
            payload=command.model_dump(mode="json"),
            transform=lambda current, version, now: current.model_copy(
                update={
                    "interval_seconds": command.interval_seconds or current.interval_seconds,
                    "next_run_at": command.next_run_at or current.next_run_at,
                    "version": version.value,
                    "etag": version.etag,
                    "updated_at": now,
                }
            ),
        )

    def enable_lifecycle_schedule_command(
        self,
        *,
        project_id: str,
        schedule_id: str,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ScheduleMutationResult:
        def enable(
            current: LifecycleSchedule,
            version: ResourceVersion,
            now: datetime,
        ) -> LifecycleSchedule:
            if current.enabled:
                return current
            return current.model_copy(
                update={
                    "enabled": True,
                    "next_run_at": max(
                        current.next_run_at,
                        now + timedelta(seconds=current.interval_seconds),
                    ),
                    "version": version.value,
                    "etag": version.etag,
                    "updated_at": now,
                }
            )

        return self._change_schedule(
            project_id=project_id,
            schedule_id=schedule_id,
            actor=actor,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=request_id,
            action="storage.lifecycle_schedule.enabled",
            payload={},
            transform=enable,
        )

    def pause_lifecycle_schedule_command(
        self,
        *,
        project_id: str,
        schedule_id: str,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ScheduleMutationResult:
        return self._change_schedule(
            project_id=project_id,
            schedule_id=schedule_id,
            actor=actor,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=request_id,
            action="storage.lifecycle_schedule.paused",
            payload={},
            transform=lambda current, version, now: (
                current
                if not current.enabled
                else current.model_copy(
                    update={
                        "enabled": False,
                        "version": version.value,
                        "etag": version.etag,
                        "updated_at": now,
                    }
                )
            ),
        )

    def delete_lifecycle_schedule_command(
        self,
        *,
        project_id: str,
        schedule_id: str,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> ScheduleMutationResult:
        self._authorize(actor, project_id, _LIFECYCLE_MANAGE_CAPABILITY)

        def delete() -> LifecycleSchedule:
            current = self._schedule_record(project_id=project_id, schedule_id=schedule_id)
            ResourceVersion(current.version).require(if_match)
            if current.enabled:
                raise problem(
                    status=409,
                    code="LIFECYCLE_SCHEDULE_ENABLED",
                    title="Lifecycle schedule is enabled",
                    detail="Pause the schedule before deleting it.",
                )
            self._repository.delete_schedule(
                project_id=project_id,
                schedule_id=schedule_id,
                expected_version=current.version,
                actor_id=actor.subject_id,
                request_id=request_id,
            )
            return current

        outcome = self._idempotency.execute(
            scope=f"storage.lifecycle.schedule.delete:{project_id}:{schedule_id}",
            key=idempotency_key,
            payload={"if_match": if_match},
            action=delete,
        )
        return ScheduleMutationResult(schedule=None, replayed=outcome.replayed)

    def run_due_lifecycle_schedule(
        self,
        *,
        project_id: str,
        schedule_id: str,
        scheduled_for: datetime,
        actor: AuthContext,
        request_id: str,
    ) -> LifecycleExecution | None:
        schedule = self._repository.get_schedule(
            project_id=project_id,
            schedule_id=schedule_id,
        )
        if schedule is None or not schedule.enabled:
            return None
        policy = self._repository.get_policy(project_id=project_id, policy_id=schedule.policy_id)
        if (
            policy is None
            or policy.state is not LifecyclePolicyState.ENABLED
            or not policy.action.physical
        ):
            return None
        outcome = self.create_lifecycle_dry_run_command(
            project_id=project_id,
            command=LifecycleDryRunRequest(
                policy_id=policy.policy_id,
                policy_etag=policy.etag,
            ),
            actor=actor,
            idempotency_key=canonical_hash(
                {
                    "schedule_id": schedule_id,
                    "scheduled_for": scheduled_for.isoformat(),
                }
            ),
            request_id=request_id,
        )
        self._repository.link_schedule_execution(
            project_id=project_id,
            schedule_id=schedule_id,
            execution_id=outcome.execution.execution_id,
            linked_at=self._clock(),
        )
        return outcome.execution

    def _change_schedule(
        self,
        *,
        project_id: str,
        schedule_id: str,
        actor: AuthContext,
        if_match: str,
        idempotency_key: str,
        request_id: str,
        action: str,
        payload: Any,
        transform: Callable[
            [LifecycleSchedule, ResourceVersion, datetime],
            LifecycleSchedule,
        ],
    ) -> ScheduleMutationResult:
        self._authorize(actor, project_id, _LIFECYCLE_MANAGE_CAPABILITY)

        def change() -> LifecycleSchedule:
            current = self._schedule_record(project_id=project_id, schedule_id=schedule_id)
            version = ResourceVersion(current.version).next(if_match)
            now = self._clock()
            candidate = transform(current, version, now)
            if candidate is current:
                return current
            if candidate.next_run_at <= now:
                raise problem(
                    status=422,
                    code="LIFECYCLE_SCHEDULE_TIME_INVALID",
                    title="Lifecycle schedule time is invalid",
                    detail="The next scheduled run must be in the future.",
                )
            return self._repository.save_schedule(
                candidate,
                expected_version=current.version,
                actor_id=actor.subject_id,
                action=action,
                request_id=request_id,
            )

        outcome = self._idempotency.execute(
            scope=f"{action}:{project_id}:{schedule_id}",
            key=idempotency_key,
            payload={"payload": payload, "if_match": if_match},
            action=change,
        )
        return ScheduleMutationResult(schedule=outcome.value, replayed=outcome.replayed)

    @staticmethod
    def validate_execution(request: LifecycleExecutionRequest) -> LifecycleExecutionResult:
        """Fail-closed Worker guard shared with the persisted dry-run plan."""
        return evaluate_execution_protection(request)

    def _object_record(self, *, project_id: str, object_id: str) -> ManagedStorageObjectRecord:
        record = self._repository.get_managed_object(project_id=project_id, object_id=object_id)
        if record is None:
            raise _not_found("storage_object")
        return record

    def _execution_record(self, *, project_id: str, execution_id: str) -> LifecycleExecution:
        execution = self._repository.get_execution(project_id=project_id, execution_id=execution_id)
        if execution is None:
            raise _not_found("lifecycle_execution")
        return execution

    def _schedule_record(self, *, project_id: str, schedule_id: str) -> LifecycleSchedule:
        schedule = self._repository.get_schedule(
            project_id=project_id,
            schedule_id=schedule_id,
        )
        if schedule is None:
            raise _not_found("lifecycle_schedule")
        return schedule

    def _begin_object_operation(
        self,
        *,
        project_id: str,
        object_id: str | None,
        multipart_id: str | None,
        action: str,
        payload: Any,
        actor: AuthContext,
        idempotency_key: str,
        request_id: str,
    ) -> tuple[StorageObjectOperation, bool]:
        operation = StorageObjectOperation(
            operation_id=self._id_factory(),
            project_id=project_id,
            object_id=object_id,
            multipart_id=multipart_id,
            action=action,
            status="PENDING",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint(payload),
            actor_id=actor.subject_id,
            request_id=request_id,
            attempt=0,
            created_at=self._clock(),
        )
        return self._repository.begin_object_operation(operation)

    def _finish_operation_failed(self, operation: StorageObjectOperation) -> None:
        try:
            self._repository.finish_object_operation(
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                status="FAILED",
                error_code="STORAGE_OBJECT_OPERATION_FAILED",
                completed_at=self._clock(),
            )
        except Exception:
            # Preserve the provider/domain exception. The durable operation remains
            # PENDING if even failure recording is unavailable and is safe to retry.
            return

    def _require_object_action(
        self,
        record: ManagedStorageObjectRecord,
        action: StorageObjectAction,
    ) -> None:
        if action in record.public(now=self._clock()).allowed_actions:
            return
        reasons: list[str] = []
        if record.active_reference_count:
            reasons.append("ACTIVE_REFERENCE")
        if record.legal_hold:
            reasons.append("LEGAL_HOLD")
        if record.governance_hold:
            reasons.append("GOVERNANCE_HOLD")
        if record.retention_until is not None and record.retention_until > self._clock():
            reasons.append("RETENTION_ACTIVE")
        if record.object_role.protected and action is StorageObjectAction.TRASH:
            reasons.append("PROTECTED_OBJECT")
        raise problem(
            status=409,
            code="STORAGE_OBJECT_ACTION_BLOCKED",
            title="Storage object action is blocked",
            detail="The current object state or governance protection blocks this action.",
            details={"blocked_reasons": reasons or ["INVALID_STATE"]},
        )

    @staticmethod
    def _managed_destination(kind: str, record: ManagedStorageObjectRecord) -> str:
        identity = canonical_hash(
            {
                "project_id": record.project_id,
                "object_id": record.object_id,
                "original_object_key": record.original_object_key,
                "kind": kind,
            }
        )
        return f"_hc_governance/{kind}/{identity}"

    def _move_provider_object(
        self,
        source_key: str,
        destination_key: str,
        expected_bytes: str,
        *,
        allow_existing: bool = True,
    ) -> None:
        source = self._object_operator.head(source_key)
        destination = self._object_operator.head(destination_key)
        expected = int(expected_bytes)
        if destination is not None:
            if not allow_existing or destination.size != expected:
                raise problem(
                    status=409,
                    code="STORAGE_OBJECT_DESTINATION_CONFLICT",
                    title="Storage object destination conflict",
                    detail="The recoverable destination already contains another object.",
                )
        else:
            if source is None:
                raise problem(
                    status=409,
                    code="STORAGE_OBJECT_PROVIDER_MISSING",
                    title="Storage object is missing",
                    detail="Neither the source nor the recoverable destination is available.",
                )
            destination = self._object_operator.copy(source_key, destination_key)
            if destination.size != expected:
                raise problem(
                    status=409,
                    code="STORAGE_OBJECT_COPY_MISMATCH",
                    title="Storage object copy did not verify",
                    detail="The recoverable copy has a different physical length.",
                )
        if source is not None:
            self._object_operator.delete(source_key)

    @staticmethod
    def _execution_candidate(
        record: ManagedStorageObjectRecord, *, now: datetime
    ) -> LifecycleExecutionCandidate:
        return LifecycleExecutionCandidate(
            physical_instance_id=record.object_id,
            logical_object_id=record.object_id,
            object_role=record.object_role,
            rebuild_source_id=record.rebuild_source_id,
            active_reference_count=record.active_reference_count,
            protection_verified=True,
            retention_active=(record.retention_until is not None and record.retention_until > now),
            legal_hold=record.legal_hold,
            governance_hold=record.governance_hold,
        )

    @staticmethod
    def _growth(items: tuple[CapacityHistoryPoint, ...]) -> CapacityGrowth | None:
        if len(items) < 2:
            return None
        first, last = items[0], items[-1]
        elapsed_seconds = int((last.observed_at - first.observed_at).total_seconds())
        if elapsed_seconds <= 0:
            return None
        change = int(last.candidate_business_total_bytes) - int(
            first.candidate_business_total_bytes
        )
        magnitude = (abs(change) * 86_400) // elapsed_seconds
        per_day = magnitude if change >= 0 else -magnitude
        return CapacityGrowth(
            from_snapshot_id=first.snapshot_id,
            from_observed_at=first.observed_at,
            to_snapshot_id=last.snapshot_id,
            to_observed_at=last.observed_at,
            candidate_change_bytes=str(change),
            elapsed_seconds=elapsed_seconds,
            candidate_bytes_per_day=str(per_day),
        )

    def _change_policy(
        self,
        *,
        project_id: str,
        policy_id: str,
        actor: AuthContext,
        idempotency_key: str,
        request_id: str,
        action_name: str,
        payload: Any,
        if_match: str,
        transform: Callable[[LifecyclePolicy, ResourceVersion], LifecyclePolicy],
    ) -> PolicyMutationResult:
        self._authorize(actor, project_id, _LIFECYCLE_MANAGE_CAPABILITY)

        def change() -> LifecyclePolicy:
            current = self.get_policy(project_id=project_id, policy_id=policy_id, actor=actor)
            next_version = ResourceVersion(current.version).next(if_match)
            candidate = transform(current, next_version)
            if candidate is current:
                return current
            self._require_unique_name(candidate)
            if candidate.state is LifecyclePolicyState.ENABLED:
                self._require_no_enabled_conflict(candidate)
            audit_event = self._audit_event(
                policy=candidate,
                actor=actor,
                action=action_name,
                request_id=request_id,
                before=current,
                after=candidate,
            )
            saved = self._repository.save_policy(
                candidate,
                expected_version=current.version,
                audit_event=audit_event,
            )
            return saved

        outcome = self._idempotency.execute(
            scope=f"{action_name}:{project_id}:{policy_id}",
            key=idempotency_key,
            payload=payload,
            action=change,
        )
        return PolicyMutationResult(outcome.value, outcome.replayed)

    def _require_unique_name(self, candidate: LifecyclePolicy) -> None:
        if any(
            policy.policy_id != candidate.policy_id
            and policy.name.casefold() == candidate.name.casefold()
            for policy in self._repository.all_policies(project_id=candidate.project_id)
        ):
            raise problem(
                status=409,
                code="LIFECYCLE_POLICY_NAME_CONFLICT",
                title="Lifecycle policy name conflict",
                detail="Policy names must be unique within one project.",
            )

    def _require_no_enabled_conflict(self, candidate: LifecyclePolicy) -> None:
        conflict = next(
            (
                policy
                for policy in self._repository.all_policies(project_id=candidate.project_id)
                if policy.policy_id != candidate.policy_id
                and policy.state is LifecyclePolicyState.ENABLED
                and policy.business_category is candidate.business_category
                and policy.object_role is candidate.object_role
                and policy.priority == candidate.priority
            ),
            None,
        )
        if conflict is not None:
            raise problem(
                status=409,
                code="LIFECYCLE_POLICY_CONFLICT",
                title="Lifecycle policy conflict",
                detail="Another enabled policy has the same target and priority.",
                details={"conflicting_policy_id": conflict.policy_id},
            )

    def _audit_event(
        self,
        *,
        policy: LifecyclePolicy,
        actor: AuthContext,
        action: str,
        request_id: str,
        before: LifecyclePolicy | None,
        after: LifecyclePolicy | None,
    ) -> LifecycleAuditEvent:
        return LifecycleAuditEvent(
            audit_id=self._id_factory(),
            project_id=policy.project_id,
            policy_id=policy.policy_id,
            actor_id=actor.subject_id,
            action=action,
            before_digest=None
            if before is None
            else canonical_hash(before.model_dump(mode="json")),
            after_digest=None if after is None else canonical_hash(after.model_dump(mode="json")),
            request_id=request_id,
            details={
                "state": None if after is None else after.state.value,
                "version": None if after is None else after.version,
            },
            occurred_at=self._clock(),
        )

    @staticmethod
    def _authorize(
        actor: AuthContext,
        project_id: str,
        capability: str,
    ) -> None:
        organization_id: str | None = None
        region_code: str | None = None
        try:
            request_scope = current_request_context()
        except RuntimeError:
            # Direct service callers retain the legacy project-level contract.
            pass
        else:
            if request_scope.project_id not in (None, project_id):
                raise problem(
                    status=403,
                    code="PROJECT_SCOPE_DENIED",
                    title="Project access denied",
                    detail="The selected request scope does not match this project.",
                )
            organization_id = request_scope.organization_id
            region_code = request_scope.region_code

        ScopeGuard.require(actor, project_id, region_code, organization_id)
        actor.require_capability(capability, project_id, organization_id)

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if limit < 1 or limit > 100:
            raise problem(
                status=422,
                code="PAGE_LIMIT_INVALID",
                title="Page limit invalid",
                detail="limit must be between 1 and 100.",
            )

    def _encode_cursor(
        self,
        *,
        kind: str,
        project_id: str,
        value: str,
        direction: str,
        snapshot_id: str | None = None,
    ) -> str:
        return self._cursor.encode(
            {
                "kind": kind,
                "project_id": project_id,
                "snapshot_id": snapshot_id,
                "value": value,
                "direction": direction,
            }
        )

    def _decode_cursor(
        self,
        cursor: str | None,
        *,
        kind: str,
        project_id: str,
        snapshot_id: str | None = None,
    ) -> tuple[str | None, str]:
        if cursor is None:
            return None, "after"
        payload = self._cursor.decode(cursor)
        if (
            payload.get("kind") != kind
            or payload.get("project_id") != project_id
            or payload.get("snapshot_id") != snapshot_id
            or not isinstance(payload.get("value"), str)
            or payload.get("direction") not in {"after", "before"}
        ):
            raise problem(
                status=400,
                code="CURSOR_SCOPE_MISMATCH",
                title="Pagination cursor scope mismatch",
                detail="The cursor belongs to another resource or project scope.",
            )
        return str(payload["value"]), str(payload["direction"])

    @staticmethod
    def _page_info(
        items: Sequence[Any],
        *,
        has_next: bool,
        has_previous: bool,
        start_cursor_for: Callable[[Any], str],
        end_cursor_for: Callable[[Any], str],
    ) -> PageInfo:
        return PageInfo(
            has_next_page=has_next,
            has_previous_page=has_previous,
            start_cursor=None if not items else start_cursor_for(items[0]),
            end_cursor=None if not items else end_cursor_for(items[-1]),
        )

    @staticmethod
    def _reconcile(
        *,
        project_id: str,
        snapshot_id: str,
        facts: Sequence[CapacityInventoryFact],
        duplicate_rows_ignored: int | None = None,
    ) -> CapacitySnapshot:
        if not facts:
            raise problem(
                status=422,
                code="CAPACITY_SNAPSHOT_EMPTY",
                title="Capacity snapshot is empty",
                detail="At least one physical inventory fact is required.",
            )
        if any(fact.project_id != project_id or fact.snapshot_id != snapshot_id for fact in facts):
            raise problem(
                status=409,
                code="CAPACITY_SNAPSHOT_SCOPE_CONFLICT",
                title="Capacity snapshot scope conflict",
                detail="Every inventory fact must belong to the selected project and snapshot.",
            )

        unique_physical: dict[str, CapacityInventoryFact] = {}
        duplicate_rows = 0
        for fact in facts:
            existing = unique_physical.get(fact.physical_instance_id)
            if existing is None:
                unique_physical[fact.physical_instance_id] = fact
                continue
            if existing != fact:
                raise problem(
                    status=409,
                    code="CAPACITY_PHYSICAL_ID_CONFLICT",
                    title="Physical inventory identity conflict",
                    detail="One physical instance ID has inconsistent inventory facts.",
                    details={"physical_instance_id": fact.physical_instance_id},
                )
            duplicate_rows += 1

        if duplicate_rows_ignored is not None:
            duplicate_rows = duplicate_rows_ignored

        temporary = [
            fact
            for fact in unique_physical.values()
            if fact.disposition is InventoryDisposition.TEMPORARY
        ]
        business = [
            fact
            for fact in unique_physical.values()
            if fact.disposition is not InventoryDisposition.TEMPORARY
        ]
        logical_groups: dict[str, list[CapacityInventoryFact]] = {}
        for fact in business:
            assert fact.logical_object_id is not None
            logical_groups.setdefault(fact.logical_object_id, []).append(fact)

        canonical: list[CapacityInventoryFact] = []
        replica_instance_count = 0
        replica_overhead = 0
        for logical_id, group in logical_groups.items():
            categories = {fact.business_category for fact in group}
            sizes = {fact.physical_bytes for fact in group}
            if len(categories) != 1 or len(sizes) != 1:
                raise problem(
                    status=409,
                    code="CAPACITY_LOGICAL_OBJECT_CONFLICT",
                    title="Logical capacity identity conflict",
                    detail="Replicas of one logical object must agree on size and category.",
                    details={"logical_object_id": logical_id},
                )
            selected = min(
                group,
                key=lambda fact: (
                    fact.disposition is not InventoryDisposition.PRIMARY,
                    fact.physical_instance_id,
                ),
            )
            canonical.append(selected)
            extras = len(group) - 1
            replica_instance_count += extras
            replica_overhead += extras * int(selected.physical_bytes)

        category_totals = tuple(
            CapacityCategoryTotal(
                category=category,
                candidate_bytes=str(
                    sum(
                        int(fact.physical_bytes)
                        for fact in canonical
                        if fact.business_category is category
                    )
                ),
                logical_object_count=sum(
                    1 for fact in canonical if fact.business_category is category
                ),
            )
            for category in BusinessCapacityCategory
        )
        candidate_total = sum(int(fact.physical_bytes) for fact in canonical)
        temporary_bytes = sum(int(fact.physical_bytes) for fact in temporary)
        physical_total = sum(int(fact.physical_bytes) for fact in unique_physical.values())
        balanced = physical_total == candidate_total + replica_overhead + temporary_bytes
        observed_at = max(fact.observed_at for fact in unique_physical.values())
        return CapacitySnapshot(
            snapshot_id=snapshot_id,
            project_id=project_id,
            observed_at=observed_at,
            physical_total_bytes=str(physical_total),
            physical_instance_count=len(unique_physical),
            candidate_business_total_bytes=str(candidate_total),
            candidate_logical_object_count=len(canonical),
            categories=category_totals,
            reconciliation=CapacityReconciliation(
                replica_overhead_bytes=str(replica_overhead),
                replica_instance_count=replica_instance_count,
                temporary_bytes=str(temporary_bytes),
                temporary_instance_count=len(temporary),
                duplicate_inventory_rows_ignored=duplicate_rows,
                balanced=balanced,
            ),
        )

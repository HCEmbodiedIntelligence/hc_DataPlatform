from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from threading import RLock

from hc_data_platform.core.errors import problem
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.versioning import ResourceVersion

from .models import (
    CapacityHistoryPoint,
    CapacityInventoryFact,
    CapacitySnapshot,
    LifecycleAuditEvent,
    LifecycleExecution,
    LifecycleExecutionLog,
    LifecycleExecutionStatus,
    LifecyclePolicy,
    LifecycleSchedule,
    ManagedMultipartUploadRecord,
    ManagedStorageObjectRecord,
    StorageObjectOperation,
)


def _version_conflict() -> Exception:
    return problem(
        status=412,
        code="LIFECYCLE_POLICY_VERSION_CONFLICT",
        title="Lifecycle policy changed",
        detail="The lifecycle policy changed after it was read.",
    )


class InMemoryStorageRepository:
    """Thread-safe scoped fake with PostgreSQL-shaped compare-and-swap behavior."""

    def __init__(self) -> None:
        self._inventory: dict[tuple[str, str], tuple[CapacityInventoryFact, ...]] = {}
        self._inventory_digests: dict[tuple[str, str], str] = {}
        self._snapshots: dict[tuple[str, str], CapacitySnapshot] = {}
        self._latest_snapshot: dict[str, str] = {}
        self._policies: dict[tuple[str, str], LifecyclePolicy] = {}
        self._audit: dict[str, list[LifecycleAuditEvent]] = {}
        self._managed_objects: dict[tuple[str, str], ManagedStorageObjectRecord] = {}
        self._multipart: dict[tuple[str, str], ManagedMultipartUploadRecord] = {}
        self._operations: dict[tuple[str, str], StorageObjectOperation] = {}
        self._operation_replay: dict[tuple[str, str, str, str], str] = {}
        self._executions: dict[tuple[str, str], LifecycleExecution] = {}
        self._execution_logs: dict[tuple[str, str], list[LifecycleExecutionLog]] = {}
        self._approvals: dict[tuple[str, str], tuple[str, str, datetime, bool]] = {}
        self._schedules: dict[tuple[str, str], LifecycleSchedule] = {}
        self._lock = RLock()

    def replace_inventory_snapshot(
        self,
        *,
        snapshot: CapacitySnapshot,
        facts: Sequence[CapacityInventoryFact],
    ) -> None:
        project_id = snapshot.project_id
        snapshot_id = snapshot.snapshot_id
        if any(fact.project_id != project_id or fact.snapshot_id != snapshot_id for fact in facts):
            raise ValueError("inventory facts must match the selected project and snapshot")
        unique = {fact.physical_instance_id: fact for fact in facts}
        digest = canonical_hash(
            {
                "snapshot": snapshot.model_dump(mode="json"),
                "facts": [unique[key].model_dump(mode="json") for key in sorted(unique)],
            }
        )
        key = (project_id, snapshot_id)
        with self._lock:
            existing_digest = self._inventory_digests.get(key)
            if existing_digest is not None:
                if existing_digest != digest:
                    raise problem(
                        status=409,
                        code="CAPACITY_SNAPSHOT_IMMUTABLE",
                        title="Capacity snapshot is immutable",
                        detail="The snapshot ID was already published with different facts.",
                    )
                return
            self._inventory[key] = tuple(facts)
            self._inventory_digests[key] = digest
            self._snapshots[key] = snapshot
            latest_id = self._latest_snapshot.get(project_id)
            latest = None if latest_id is None else self._snapshots[(project_id, latest_id)]
            if latest is None or (snapshot.observed_at, snapshot.snapshot_id) > (
                latest.observed_at,
                latest.snapshot_id,
            ):
                self._latest_snapshot[project_id] = snapshot_id

    def latest_snapshot_id(self, *, project_id: str) -> str | None:
        with self._lock:
            return self._latest_snapshot.get(project_id)

    def list_capacity_history(
        self,
        *,
        project_id: str,
        window_start: datetime,
        window_end: datetime,
        limit: int,
    ) -> tuple[CapacityHistoryPoint, ...]:
        with self._lock:
            snapshots = [
                snapshot
                for (candidate_project, _), snapshot in self._snapshots.items()
                if candidate_project == project_id
                and window_start <= snapshot.observed_at <= window_end
            ]
        latest_by_day: dict[object, CapacitySnapshot] = {}
        for snapshot in snapshots:
            day = snapshot.observed_at.astimezone(timezone.utc).date()
            current = latest_by_day.get(day)
            if current is None or (snapshot.observed_at, snapshot.snapshot_id) > (
                current.observed_at,
                current.snapshot_id,
            ):
                latest_by_day[day] = snapshot
        return tuple(
            CapacityHistoryPoint(
                snapshot_id=snapshot.snapshot_id,
                observed_at=snapshot.observed_at,
                physical_total_bytes=snapshot.physical_total_bytes,
                candidate_business_total_bytes=snapshot.candidate_business_total_bytes,
            )
            for snapshot in sorted(
                latest_by_day.values(),
                key=lambda item: (item.observed_at, item.snapshot_id),
            )[:limit]
        )

    def inventory_facts(
        self,
        *,
        project_id: str,
        snapshot_id: str,
    ) -> tuple[CapacityInventoryFact, ...]:
        with self._lock:
            return self._inventory.get((project_id, snapshot_id), ())

    def inventory_duplicate_rows_ignored(
        self,
        *,
        project_id: str,
        snapshot_id: str,
    ) -> int:
        with self._lock:
            snapshot = self._snapshots.get((project_id, snapshot_id))
            return (
                0 if snapshot is None else snapshot.reconciliation.duplicate_inventory_rows_ignored
            )

    def list_inventory(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        anchor_physical_instance_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[CapacityInventoryFact, ...]:
        unique = {
            fact.physical_instance_id: fact
            for fact in self.inventory_facts(project_id=project_id, snapshot_id=snapshot_id)
        }
        facts = sorted(unique.values(), key=lambda item: item.physical_instance_id)
        if anchor_physical_instance_id is not None:
            facts = [
                fact
                for fact in facts
                if (
                    fact.physical_instance_id < anchor_physical_instance_id
                    if before
                    else fact.physical_instance_id > anchor_physical_instance_id
                )
            ]
        if before:
            facts.reverse()
        return tuple(facts[:limit])

    def get_policy(self, *, project_id: str, policy_id: str) -> LifecyclePolicy | None:
        with self._lock:
            return self._policies.get((project_id, policy_id))

    def list_policies(
        self,
        *,
        project_id: str,
        anchor_policy_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecyclePolicy, ...]:
        with self._lock:
            policies = sorted(
                (
                    policy
                    for (candidate_project, _), policy in self._policies.items()
                    if candidate_project == project_id
                ),
                key=lambda item: item.policy_id,
            )
        if anchor_policy_id is not None:
            policies = [
                policy
                for policy in policies
                if (
                    policy.policy_id < anchor_policy_id
                    if before
                    else policy.policy_id > anchor_policy_id
                )
            ]
        if before:
            policies.reverse()
        return tuple(policies[:limit])

    def all_policies(self, *, project_id: str) -> tuple[LifecyclePolicy, ...]:
        return self.list_policies(
            project_id=project_id,
            anchor_policy_id=None,
            before=False,
            limit=max(len(self._policies), 1),
        )

    def save_policy(
        self,
        policy: LifecyclePolicy,
        *,
        expected_version: int | None,
        audit_event: LifecycleAuditEvent,
    ) -> LifecyclePolicy:
        key = (policy.project_id, policy.policy_id)
        with self._lock:
            current = self._policies.get(key)
            if expected_version is None:
                if current is not None:
                    raise problem(
                        status=409,
                        code="LIFECYCLE_POLICY_ID_CONFLICT",
                        title="Lifecycle policy ID conflict",
                        detail="The lifecycle policy ID already exists in this project.",
                    )
            elif current is None or current.version != expected_version:
                raise _version_conflict()
            self._policies[key] = policy
            self._audit.setdefault(audit_event.project_id, []).append(audit_event)
            return policy

    def delete_policy(
        self,
        *,
        project_id: str,
        policy_id: str,
        expected_version: int,
        audit_event: LifecycleAuditEvent,
    ) -> None:
        key = (project_id, policy_id)
        with self._lock:
            current = self._policies.get(key)
            if current is None or current.version != expected_version:
                raise _version_conflict()
            del self._policies[key]
            self._audit.setdefault(audit_event.project_id, []).append(audit_event)

    def append_audit(self, event: LifecycleAuditEvent) -> None:
        with self._lock:
            self._audit.setdefault(event.project_id, []).append(event)

    def list_audit(
        self,
        *,
        project_id: str,
        anchor_audit_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleAuditEvent, ...]:
        with self._lock:
            events = sorted(self._audit.get(project_id, ()), key=lambda item: item.audit_id)
        if anchor_audit_id is not None:
            events = [
                event
                for event in events
                if (
                    event.audit_id < anchor_audit_id if before else event.audit_id > anchor_audit_id
                )
            ]
        if before:
            events.reverse()
        return tuple(events[:limit])

    def get_managed_object(
        self, *, project_id: str, object_id: str
    ) -> ManagedStorageObjectRecord | None:
        with self._lock:
            return self._managed_objects.get((project_id, object_id))

    def list_managed_objects(
        self,
        *,
        project_id: str,
        anchor_object_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[ManagedStorageObjectRecord, ...]:
        with self._lock:
            values = sorted(
                (
                    record
                    for (candidate_project, _), record in self._managed_objects.items()
                    if candidate_project == project_id
                ),
                key=lambda record: record.object_id,
            )
        if anchor_object_id is not None:
            values = [
                record
                for record in values
                if (
                    record.object_id < anchor_object_id
                    if before
                    else record.object_id > anchor_object_id
                )
            ]
        if before:
            values.reverse()
        return tuple(values[:limit])

    def save_managed_object(
        self,
        record: ManagedStorageObjectRecord,
        *,
        expected_version: int | None,
        audit_action: str,
        actor_id: str,
        request_id: str,
        before: ManagedStorageObjectRecord | None,
    ) -> ManagedStorageObjectRecord:
        key = (record.project_id, record.object_id)
        with self._lock:
            current = self._managed_objects.get(key)
            if expected_version is None:
                if current is not None:
                    raise _version_conflict()
            elif current is None or current.version != expected_version:
                raise _version_conflict()
            self._managed_objects[key] = record
            self._audit.setdefault(record.project_id, []).append(
                LifecycleAuditEvent(
                    audit_id=f"object-{len(self._audit.get(record.project_id, ())):08d}",
                    project_id=record.project_id,
                    policy_id=record.object_id,
                    actor_id=actor_id,
                    action=audit_action,
                    before_digest=None
                    if before is None
                    else canonical_hash(before.model_dump(mode="json")),
                    after_digest=canonical_hash(record.model_dump(mode="json")),
                    request_id=request_id,
                    occurred_at=record.updated_at,
                )
            )
            return record

    def get_managed_multipart(
        self, *, project_id: str, multipart_id: str
    ) -> ManagedMultipartUploadRecord | None:
        with self._lock:
            return self._multipart.get((project_id, multipart_id))

    def save_managed_multipart(
        self,
        record: ManagedMultipartUploadRecord,
        *,
        expected_version: int,
        audit_action: str,
        actor_id: str,
        request_id: str,
    ) -> ManagedMultipartUploadRecord:
        del audit_action, actor_id, request_id
        key = (record.project_id, record.multipart_id)
        with self._lock:
            current = self._multipart.get(key)
            if current is None or current.version != expected_version:
                raise _version_conflict()
            self._multipart[key] = record
            return record

    def seed_managed_multipart(self, record: ManagedMultipartUploadRecord) -> None:
        with self._lock:
            self._multipart[(record.project_id, record.multipart_id)] = record

    def begin_object_operation(
        self, operation: StorageObjectOperation
    ) -> tuple[StorageObjectOperation, bool]:
        resource_id = operation.object_id or operation.multipart_id
        assert resource_id is not None
        replay_key = (
            operation.project_id,
            operation.action,
            resource_id,
            operation.idempotency_key,
        )
        with self._lock:
            existing_id = self._operation_replay.get(replay_key)
            if existing_id is not None:
                existing = self._operations[(operation.project_id, existing_id)]
                if existing.request_fingerprint != operation.request_fingerprint:
                    raise problem(
                        status=409,
                        code="IDEMPOTENCY_KEY_REUSED",
                        title="Idempotency key conflict",
                        detail="The idempotency key was already used for another command.",
                    )
                return existing, True
            self._operations[(operation.project_id, operation.operation_id)] = operation
            self._operation_replay[replay_key] = operation.operation_id
            return operation, False

    def finish_object_operation(
        self,
        *,
        project_id: str,
        operation_id: str,
        status: str,
        error_code: str | None,
        completed_at: datetime,
    ) -> StorageObjectOperation:
        with self._lock:
            key = (project_id, operation_id)
            operation = self._operations[key]
            updated = operation.model_copy(
                update={
                    "status": status,
                    "error_code": error_code,
                    "attempt": operation.attempt + 1,
                    "completed_at": completed_at,
                }
            )
            self._operations[key] = updated
            return updated

    def policy_candidates(
        self,
        *,
        project_id: str,
        policy: LifecyclePolicy,
        older_than: datetime,
        limit: int,
    ) -> tuple[ManagedStorageObjectRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        record
                        for (candidate_project, _), record in self._managed_objects.items()
                        if candidate_project == project_id
                        and record.business_category is policy.business_category
                        and record.object_role is policy.object_role
                        and record.updated_at <= older_than
                    ),
                    key=lambda record: record.object_id,
                )[:limit]
            )

    def create_execution(
        self,
        execution: LifecycleExecution,
        *,
        candidates: Sequence[ManagedStorageObjectRecord],
        actor_id: str,
        request_id: str,
    ) -> LifecycleExecution:
        del candidates, actor_id, request_id
        key = (execution.project_id, execution.execution_id)
        with self._lock:
            if key in self._executions:
                raise problem(
                    status=409,
                    code="LIFECYCLE_EXECUTION_ID_CONFLICT",
                    title="Lifecycle execution conflict",
                    detail="The execution identity already exists.",
                )
            self._executions[key] = execution
            self._append_execution_log(
                execution,
                level="INFO",
                event="storage.lifecycle_execution.dry_run_created",
                details={
                    "total_items": execution.total_items,
                    "blocked_items": execution.blocked_items,
                },
                occurred_at=execution.created_at,
            )
            return execution

    def get_execution(self, *, project_id: str, execution_id: str) -> LifecycleExecution | None:
        with self._lock:
            return self._executions.get((project_id, execution_id))

    def approve_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        approval_id: str,
        plan_hash: str,
        approver_id: str,
        justification: str,
        approved_at: datetime,
        expires_at: datetime,
        request_id: str,
    ) -> LifecycleExecution:
        del justification, request_id
        with self._lock:
            key = (project_id, execution_id)
            current = self._executions.get(key)
            if current is None:
                raise _version_conflict()
            if current.plan_hash != plan_hash:
                raise _version_conflict()
            if current.requested_by == approver_id:
                raise problem(
                    status=409,
                    code="LIFECYCLE_SELF_APPROVAL_DENIED",
                    title="Independent approval required",
                    detail="The requester cannot approve the same physical execution.",
                )
            updated = current.model_copy(
                update={
                    "approval_id": approval_id,
                    "approved_by": approver_id,
                    "status": LifecycleExecutionStatus.APPROVED,
                    "updated_at": approved_at,
                }
            )
            self._executions[key] = updated
            self._approvals[(project_id, approval_id)] = (
                execution_id,
                plan_hash,
                expires_at,
                False,
            )
            self._append_execution_log(
                updated,
                level="INFO",
                event="storage.lifecycle_execution.approved",
                details={"approval_id": approval_id},
                occurred_at=approved_at,
            )
            return updated

    def queue_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        approval_id: str,
        plan_hash: str,
        queued_at: datetime,
        request_id: str,
    ) -> LifecycleExecution:
        del request_id
        with self._lock:
            approval_key = (project_id, approval_id)
            approval = self._approvals.get(approval_key)
            current = self._executions.get((project_id, execution_id))
            if (
                approval is None
                or current is None
                or approval[0] != execution_id
                or approval[1] != plan_hash
                or approval[2] <= queued_at
                or approval[3]
            ):
                raise problem(
                    status=409,
                    code="LIFECYCLE_APPROVAL_INVALID",
                    title="Lifecycle approval is invalid",
                    detail="The approval is expired, consumed, or bound to another plan.",
                )
            self._approvals[approval_key] = (*approval[:3], True)
            updated = current.model_copy(
                update={
                    "status": LifecycleExecutionStatus.QUEUED,
                    "dry_run": False,
                    "updated_at": queued_at,
                }
            )
            self._executions[(project_id, execution_id)] = updated
            self._append_execution_log(
                updated,
                level="INFO",
                event="storage.lifecycle_execution.queued",
                details={"approval_id": approval_id},
                occurred_at=queued_at,
            )
            return updated

    def list_executions(
        self,
        *,
        project_id: str,
        anchor_execution_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleExecution, ...]:
        with self._lock:
            values = sorted(
                (
                    execution
                    for (candidate_project, _), execution in self._executions.items()
                    if candidate_project == project_id
                ),
                key=lambda execution: execution.execution_id,
            )
        if anchor_execution_id is not None:
            values = [
                execution
                for execution in values
                if (
                    execution.execution_id < anchor_execution_id
                    if before
                    else execution.execution_id > anchor_execution_id
                )
            ]
        if before:
            values.reverse()
        return tuple(values[:limit])

    def list_execution_logs(
        self,
        *,
        project_id: str,
        execution_id: str,
        anchor_sequence: int | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleExecutionLog, ...]:
        with self._lock:
            values = list(self._execution_logs.get((project_id, execution_id), ()))
        if anchor_sequence is not None:
            values = [
                value
                for value in values
                if (
                    value.sequence < anchor_sequence if before else value.sequence > anchor_sequence
                )
            ]
        values.sort(key=lambda value: value.sequence, reverse=before)
        return tuple(values[:limit])

    def cancel_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        actor_id: str,
        reason: str,
        cancelled_at: datetime,
        request_id: str,
    ) -> LifecycleExecution:
        del request_id
        with self._lock:
            key = (project_id, execution_id)
            current = self._executions.get(key)
            if current is None or current.status not in {
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
            updated = current.model_copy(
                update={
                    "status": LifecycleExecutionStatus.CANCELLED,
                    "updated_at": cancelled_at,
                }
            )
            self._executions[key] = updated
            self._append_execution_log(
                updated,
                level="WARNING",
                event="storage.lifecycle_execution.cancelled",
                details={"actor_id": actor_id, "reason": reason},
                occurred_at=cancelled_at,
            )
            return updated

    def retry_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        plan_hash: str,
        actor_id: str,
        reason: str,
        retried_at: datetime,
        request_id: str,
    ) -> LifecycleExecution:
        del request_id
        with self._lock:
            key = (project_id, execution_id)
            current = self._executions.get(key)
            if (
                current is None
                or current.status
                not in {LifecycleExecutionStatus.BLOCKED, LifecycleExecutionStatus.FAILED}
                or current.plan_hash != plan_hash
                or current.approval_id is None
            ):
                raise problem(
                    status=409,
                    code="LIFECYCLE_EXECUTION_NOT_RETRYABLE",
                    title="Lifecycle execution cannot be retried",
                    detail="Only an approved failed or blocked production execution is retryable.",
                )
            updated = current.model_copy(
                update={
                    "status": LifecycleExecutionStatus.QUEUED,
                    "updated_at": retried_at,
                }
            )
            self._executions[key] = updated
            self._append_execution_log(
                updated,
                level="WARNING",
                event="storage.lifecycle_execution.retry_queued",
                details={"actor_id": actor_id, "reason": reason},
                occurred_at=retried_at,
            )
            return updated

    def _append_execution_log(
        self,
        execution: LifecycleExecution,
        *,
        level: str,
        event: str,
        details: dict[str, str | int | bool | None],
        occurred_at: datetime,
    ) -> None:
        key = (execution.project_id, execution.execution_id)
        values = self._execution_logs.setdefault(key, [])
        values.append(
            LifecycleExecutionLog(
                sequence=len(values) + 1,
                project_id=execution.project_id,
                execution_id=execution.execution_id,
                level=level,
                event=event,
                details=details,
                occurred_at=occurred_at,
            )
        )

    def get_schedule(self, *, project_id: str, schedule_id: str) -> LifecycleSchedule | None:
        with self._lock:
            return self._schedules.get((project_id, schedule_id))

    def list_schedules(
        self,
        *,
        project_id: str,
        anchor_schedule_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleSchedule, ...]:
        with self._lock:
            values = sorted(
                (
                    schedule
                    for (candidate_project, _), schedule in self._schedules.items()
                    if candidate_project == project_id
                ),
                key=lambda schedule: schedule.schedule_id,
            )
        if anchor_schedule_id is not None:
            values = [
                schedule
                for schedule in values
                if (
                    schedule.schedule_id < anchor_schedule_id
                    if before
                    else schedule.schedule_id > anchor_schedule_id
                )
            ]
        if before:
            values.reverse()
        return tuple(values[:limit])

    def save_schedule(
        self,
        schedule: LifecycleSchedule,
        *,
        expected_version: int | None,
        actor_id: str,
        action: str,
        request_id: str,
    ) -> LifecycleSchedule:
        del actor_id, action, request_id
        key = (schedule.project_id, schedule.schedule_id)
        with self._lock:
            current = self._schedules.get(key)
            if expected_version is None:
                if current is not None:
                    raise _version_conflict()
            elif current is None or current.version != expected_version:
                raise _version_conflict()
            self._schedules[key] = schedule
            return schedule

    def has_schedule_for_policy(self, *, project_id: str, policy_id: str) -> bool:
        with self._lock:
            return any(
                candidate_project == project_id and schedule.policy_id == policy_id
                for (candidate_project, _), schedule in self._schedules.items()
            )

    def delete_schedule(
        self,
        *,
        project_id: str,
        schedule_id: str,
        expected_version: int,
        actor_id: str,
        request_id: str,
    ) -> None:
        del actor_id, request_id
        key = (project_id, schedule_id)
        with self._lock:
            current = self._schedules.get(key)
            if current is None or current.version != expected_version:
                raise _version_conflict()
            del self._schedules[key]

    def link_schedule_execution(
        self,
        *,
        project_id: str,
        schedule_id: str,
        execution_id: str,
        linked_at: datetime,
    ) -> LifecycleSchedule:
        key = (project_id, schedule_id)
        with self._lock:
            current = self._schedules.get(key)
            if current is None:
                raise _version_conflict()
            if current.last_execution_id == execution_id:
                return current
            version = ResourceVersion(current.version + 1)
            updated = current.model_copy(
                update={
                    "last_execution_id": execution_id,
                    "version": version.value,
                    "etag": version.etag,
                    "updated_at": linked_at,
                }
            )
            self._schedules[key] = updated
            return updated

    def enqueue_due_schedules(
        self,
        *,
        project_id: str,
        region_code: str,
        now: datetime,
        limit: int,
    ) -> int:
        del region_code
        count = 0
        with self._lock:
            due = sorted(
                (
                    schedule
                    for (candidate_project, _), schedule in self._schedules.items()
                    if candidate_project == project_id
                    and schedule.enabled
                    and schedule.next_run_at <= now
                ),
                key=lambda schedule: (schedule.next_run_at, schedule.schedule_id),
            )[:limit]
            for schedule in due:
                elapsed = max(0, int((now - schedule.next_run_at).total_seconds()))
                periods = elapsed // schedule.interval_seconds + 1
                version = ResourceVersion(schedule.version + 1)
                self._schedules[(project_id, schedule.schedule_id)] = schedule.model_copy(
                    update={
                        "next_run_at": schedule.next_run_at
                        + timedelta(seconds=periods * schedule.interval_seconds),
                        "version": version.value,
                        "etag": version.etag,
                        "updated_at": now,
                    }
                )
                count += 1
        return count

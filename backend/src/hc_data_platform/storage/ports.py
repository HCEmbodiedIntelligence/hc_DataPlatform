from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from .models import (
    CapacityHistoryPoint,
    CapacityInventoryFact,
    CapacitySnapshot,
    LifecycleAuditEvent,
    LifecycleExecution,
    LifecycleExecutionLog,
    LifecyclePolicy,
    LifecycleSchedule,
    ManagedMultipartUploadRecord,
    ManagedStorageObjectRecord,
    StorageObjectOperation,
)


class StorageRepository(Protocol):
    def replace_inventory_snapshot(
        self,
        *,
        snapshot: CapacitySnapshot,
        facts: Sequence[CapacityInventoryFact],
    ) -> None: ...

    def latest_snapshot_id(self, *, project_id: str) -> str | None: ...

    def list_capacity_history(
        self,
        *,
        project_id: str,
        window_start: datetime,
        window_end: datetime,
        limit: int,
    ) -> tuple[CapacityHistoryPoint, ...]: ...

    def inventory_facts(
        self,
        *,
        project_id: str,
        snapshot_id: str,
    ) -> tuple[CapacityInventoryFact, ...]: ...

    def inventory_duplicate_rows_ignored(
        self,
        *,
        project_id: str,
        snapshot_id: str,
    ) -> int: ...

    def list_inventory(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        anchor_physical_instance_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[CapacityInventoryFact, ...]: ...

    def get_policy(self, *, project_id: str, policy_id: str) -> LifecyclePolicy | None: ...

    def list_policies(
        self,
        *,
        project_id: str,
        anchor_policy_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecyclePolicy, ...]: ...

    def all_policies(self, *, project_id: str) -> tuple[LifecyclePolicy, ...]: ...

    def save_policy(
        self,
        policy: LifecyclePolicy,
        *,
        expected_version: int | None,
        audit_event: LifecycleAuditEvent,
    ) -> LifecyclePolicy: ...

    def delete_policy(
        self,
        *,
        project_id: str,
        policy_id: str,
        expected_version: int,
        audit_event: LifecycleAuditEvent,
    ) -> None: ...

    def append_audit(self, event: LifecycleAuditEvent) -> None: ...

    def list_audit(
        self,
        *,
        project_id: str,
        anchor_audit_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleAuditEvent, ...]: ...

    def get_managed_object(
        self, *, project_id: str, object_id: str
    ) -> ManagedStorageObjectRecord | None: ...

    def list_managed_objects(
        self,
        *,
        project_id: str,
        anchor_object_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[ManagedStorageObjectRecord, ...]: ...

    def save_managed_object(
        self,
        record: ManagedStorageObjectRecord,
        *,
        expected_version: int | None,
        audit_action: str,
        actor_id: str,
        request_id: str,
        before: ManagedStorageObjectRecord | None,
    ) -> ManagedStorageObjectRecord: ...

    def get_managed_multipart(
        self, *, project_id: str, multipart_id: str
    ) -> ManagedMultipartUploadRecord | None: ...

    def save_managed_multipart(
        self,
        record: ManagedMultipartUploadRecord,
        *,
        expected_version: int,
        audit_action: str,
        actor_id: str,
        request_id: str,
    ) -> ManagedMultipartUploadRecord: ...

    def begin_object_operation(
        self, operation: StorageObjectOperation
    ) -> tuple[StorageObjectOperation, bool]: ...

    def finish_object_operation(
        self,
        *,
        project_id: str,
        operation_id: str,
        status: str,
        error_code: str | None,
        completed_at: datetime,
    ) -> StorageObjectOperation: ...

    def policy_candidates(
        self,
        *,
        project_id: str,
        policy: LifecyclePolicy,
        older_than: datetime,
        limit: int,
    ) -> tuple[ManagedStorageObjectRecord, ...]: ...

    def create_execution(
        self,
        execution: LifecycleExecution,
        *,
        candidates: Sequence[ManagedStorageObjectRecord],
        actor_id: str,
        request_id: str,
    ) -> LifecycleExecution: ...

    def get_execution(self, *, project_id: str, execution_id: str) -> LifecycleExecution | None: ...

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
    ) -> LifecycleExecution: ...

    def queue_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        approval_id: str,
        plan_hash: str,
        queued_at: datetime,
        request_id: str,
    ) -> LifecycleExecution: ...

    def list_executions(
        self,
        *,
        project_id: str,
        anchor_execution_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleExecution, ...]: ...

    def list_execution_logs(
        self,
        *,
        project_id: str,
        execution_id: str,
        anchor_sequence: int | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleExecutionLog, ...]: ...

    def cancel_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        actor_id: str,
        reason: str,
        cancelled_at: datetime,
        request_id: str,
    ) -> LifecycleExecution: ...

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
    ) -> LifecycleExecution: ...

    def get_schedule(self, *, project_id: str, schedule_id: str) -> LifecycleSchedule | None: ...

    def list_schedules(
        self,
        *,
        project_id: str,
        anchor_schedule_id: str | None,
        before: bool,
        limit: int,
    ) -> tuple[LifecycleSchedule, ...]: ...

    def has_schedule_for_policy(self, *, project_id: str, policy_id: str) -> bool: ...

    def save_schedule(
        self,
        schedule: LifecycleSchedule,
        *,
        expected_version: int | None,
        actor_id: str,
        action: str,
        request_id: str,
    ) -> LifecycleSchedule: ...

    def delete_schedule(
        self,
        *,
        project_id: str,
        schedule_id: str,
        expected_version: int,
        actor_id: str,
        request_id: str,
    ) -> None: ...

    def link_schedule_execution(
        self,
        *,
        project_id: str,
        schedule_id: str,
        execution_id: str,
        linked_at: datetime,
    ) -> LifecycleSchedule: ...

    def enqueue_due_schedules(
        self,
        *,
        project_id: str,
        region_code: str,
        now: datetime,
        limit: int,
    ) -> int: ...

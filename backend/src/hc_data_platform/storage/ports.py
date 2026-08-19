from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .models import (
    CapacityInventoryFact,
    CapacitySnapshot,
    LifecycleAuditEvent,
    LifecyclePolicy,
)


class StorageRepository(Protocol):
    def replace_inventory_snapshot(
        self,
        *,
        snapshot: CapacitySnapshot,
        facts: Sequence[CapacityInventoryFact],
    ) -> None: ...

    def latest_snapshot_id(self, *, project_id: str) -> str | None: ...

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

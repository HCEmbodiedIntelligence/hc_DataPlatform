from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from hc_data_platform.core.errors import problem
from hc_data_platform.security.audit import canonical_hash

from .models import (
    CapacityInventoryFact,
    CapacitySnapshot,
    LifecycleAuditEvent,
    LifecyclePolicy,
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
            self._latest_snapshot[project_id] = snapshot_id

    def latest_snapshot_id(self, *, project_id: str) -> str | None:
        with self._lock:
            return self._latest_snapshot.get(project_id)

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

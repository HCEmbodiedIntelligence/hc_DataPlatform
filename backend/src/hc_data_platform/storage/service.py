from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec, PageInfo
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.auth import AuthContext, Permission
from hc_data_platform.security.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from hc_data_platform.security.scope import ScopeGuard
from hc_data_platform.security.versioning import ResourceVersion

from .models import (
    BusinessCapacityCategory,
    CapacityCategoryTotal,
    CapacityInventoryFact,
    CapacityInventoryPage,
    CapacityReconciliation,
    CapacitySnapshot,
    CreateLifecyclePolicy,
    InventoryDisposition,
    LifecycleAuditEvent,
    LifecycleAuditPage,
    LifecycleExecutionRequest,
    LifecycleExecutionResult,
    LifecyclePolicy,
    LifecyclePolicyPage,
    LifecyclePolicyState,
    UpdateLifecyclePolicy,
    evaluate_execution_protection,
)
from .ports import StorageRepository
from .repository import InMemoryStorageRepository

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class PolicyMutationResult:
    policy: LifecyclePolicy | None
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
    ) -> None:
        self._repository = repository
        self._cursor = CursorCodec(cursor_secret)
        self._idempotency = idempotency or InMemoryIdempotencyStore()
        self._clock = clock
        self._id_factory = id_factory

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
        self._authorize(actor, project_id, Permission.READ)
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

    def inventory_page(
        self,
        *,
        project_id: str,
        actor: AuthContext,
        snapshot_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CapacityInventoryPage:
        self._authorize(actor, project_id, Permission.READ)
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
        self._authorize(actor, project_id, Permission.ADMINISTER)

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
        self._authorize(actor, project_id, Permission.READ)
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
        self._authorize(actor, project_id, Permission.READ)
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
        self._authorize(actor, project_id, Permission.ADMINISTER)

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
        self._authorize(actor, project_id, Permission.READ)
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

    @staticmethod
    def validate_execution(request: LifecycleExecutionRequest) -> LifecycleExecutionResult:
        """Fail-closed Worker guard. This is not an impact-reporting capability."""
        return evaluate_execution_protection(request)

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
        self._authorize(actor, project_id, Permission.ADMINISTER)

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
    def _authorize(actor: AuthContext, project_id: str, permission: Permission) -> None:
        actor.require_permission(permission)
        ScopeGuard.require(actor, project_id)

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

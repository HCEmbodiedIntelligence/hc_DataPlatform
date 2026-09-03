"""PostgreSQL-checkpointed, MinIO/S3-backed lifecycle batch execution."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.versioning import ResourceVersion

from .errors import LifecycleExecutionBlocked
from .models import (
    LifecycleBatchCommand,
    LifecycleBatchResult,
    LifecycleExecutionCandidate,
    LifecycleExecutionRequest,
    LifecyclePolicyAction,
    ObjectRole,
    evaluate_execution_protection,
)
from .object_store import StorageObjectOperator


class PostgresLifecycleBatchExecutor:
    """Execute one immutable batch and checkpoint it under the same execution identity."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        object_operator: StorageObjectOperator,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._connection_factory = connection_factory
        self._object_operator = object_operator
        self._clock = clock

    def apply_batch(self, command: LifecycleBatchCommand) -> LifecycleBatchResult:
        token = bind_request_context(
            RequestContext(
                organization_id=command.organization_id,
                project_id=command.project_id,
                region_code=command.region_code,
                subject_id="storage-lifecycle-worker",
                request_id=f"storage-lifecycle/{command.execution_id}/{command.batch_index}",
                service_identity=True,
            )
        )
        try:
            return self._apply_scoped(command)
        finally:
            reset_request_context(token)

    def _apply_scoped(self, command: LifecycleBatchCommand) -> LifecycleBatchResult:
        now = self._clock()
        connection = self._connection_factory()
        processed: list[str] = []
        replayed = True
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT action, policy_version, approval_id, plan_hash, status
                    FROM storage.lifecycle_executions
                    WHERE project_id = %s AND execution_id = %s
                    FOR UPDATE
                    """,
                    (command.project_id, command.execution_id),
                )
                execution = cursor.fetchone()
                if execution is None:
                    raise LifecycleExecutionBlocked("lifecycle execution does not exist")
                if (
                    str(execution[0]) != command.action.value
                    or int(execution[1]) != command.policy_version
                    or str(execution[2]) != command.approval_id
                    or str(execution[3]) != command.plan_hash
                    or str(execution[4]) not in {"QUEUED", "RUNNING", "FAILED"}
                ):
                    raise LifecycleExecutionBlocked(
                        "lifecycle execution no longer matches the approved immutable plan"
                    )
                cursor.execute(
                    """
                    UPDATE storage.lifecycle_executions
                    SET status = 'RUNNING', updated_at = %s
                    WHERE project_id = %s AND execution_id = %s
                    """,
                    (now, command.project_id, command.execution_id),
                )
                for candidate in command.candidates:
                    cursor.execute(
                        """
                        SELECT status
                        FROM storage.lifecycle_execution_items
                        WHERE project_id = %s AND execution_id = %s
                          AND physical_instance_id = %s
                        FOR UPDATE
                        """,
                        (
                            command.project_id,
                            command.execution_id,
                            candidate.physical_instance_id,
                        ),
                    )
                    item = cursor.fetchone()
                    if item is None:
                        raise LifecycleExecutionBlocked("approved lifecycle item is missing")
                    if str(item[0]) == "PROCESSED":
                        processed.append(candidate.physical_instance_id)
                        continue
                    replayed = False
                    record = self._load_object_for_update(
                        cursor,
                        project_id=command.project_id,
                        object_id=candidate.physical_instance_id,
                    )
                    current_candidate = LifecycleExecutionCandidate(
                        physical_instance_id=str(record["object_id"]),
                        logical_object_id=str(record["object_id"]),
                        object_role=ObjectRole(str(record["object_role"])),
                        rebuild_source_id=(
                            None
                            if record["rebuild_source_id"] is None
                            else str(record["rebuild_source_id"])
                        ),
                        active_reference_count=int(record["active_reference_count"]),
                        protection_verified=True,
                        retention_active=(
                            record["retention_until"] is not None
                            and record["retention_until"] > now
                        ),
                        legal_hold=bool(record["legal_hold"]),
                        governance_hold=bool(record["governance_hold"]),
                    )
                    guard = evaluate_execution_protection(
                        LifecycleExecutionRequest(
                            execution_id=command.execution_id,
                            organization_id=command.organization_id,
                            project_id=command.project_id,
                            region_code=command.region_code,
                            policy_id=command.policy_id,
                            policy_version=command.policy_version,
                            action=command.action,
                            production=command.production,
                            production_execution_approved=(command.production_execution_approved),
                            approval_id=command.approval_id,
                            plan_hash=command.plan_hash,
                            candidates=(current_candidate,),
                        )
                    )
                    if guard.status == "BLOCKED":
                        self._block_item(cursor, command, candidate, guard.blocked_reasons, now)
                        self._log(
                            cursor,
                            command,
                            "storage.lifecycle_execution.item_blocked",
                            {
                                "physical_instance_id": candidate.physical_instance_id,
                                "blocked_reasons": guard.blocked_reasons,
                            },
                            now,
                            level="WARNING",
                        )
                        connection.commit()
                        raise LifecycleExecutionBlocked(";".join(guard.blocked_reasons))
                    try:
                        self._apply_object_action(cursor, command, record, now)
                    except Exception as exc:
                        self._fail_item(cursor, command, candidate, exc, now)
                        self._log(
                            cursor,
                            command,
                            "storage.lifecycle_execution.item_failed",
                            {
                                "physical_instance_id": candidate.physical_instance_id,
                                "error_code": getattr(
                                    exc,
                                    "code",
                                    "LIFECYCLE_OBJECT_OPERATION_FAILED",
                                ),
                            },
                            now,
                            level="ERROR",
                        )
                        connection.commit()
                        raise
                    cursor.execute(
                        """
                        UPDATE storage.lifecycle_execution_items
                        SET status = 'PROCESSED', attempt = attempt + 1,
                            last_error = NULL, last_error_code = NULL, updated_at = %s
                        WHERE project_id = %s AND execution_id = %s
                          AND physical_instance_id = %s
                        """,
                        (
                            now,
                            command.project_id,
                            command.execution_id,
                            candidate.physical_instance_id,
                        ),
                    )
                    processed.append(candidate.physical_instance_id)
                status = "COMPLETED" if command.final_batch else "RUNNING"
                cursor.execute(
                    """
                    UPDATE storage.lifecycle_executions
                    SET status = %s, next_batch = GREATEST(next_batch, %s),
                        updated_at = %s, completed_at = CASE WHEN %s THEN %s ELSE NULL END
                    WHERE project_id = %s AND execution_id = %s
                    """,
                    (
                        status,
                        command.batch_index + 1,
                        now,
                        command.final_batch,
                        now,
                        command.project_id,
                        command.execution_id,
                    ),
                )
                self._log(
                    cursor,
                    command,
                    "storage.lifecycle_execution.batch_completed",
                    {
                        "batch_index": command.batch_index,
                        "processed_items": len(processed),
                        "replayed": replayed,
                        "final_batch": command.final_batch,
                    },
                    now,
                )
            connection.commit()
            return LifecycleBatchResult(
                execution_id=command.execution_id,
                batch_index=command.batch_index,
                processed_instance_ids=tuple(processed),
                replayed=replayed,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _load_object_for_update(cursor: Any, *, project_id: str, object_id: str) -> dict[str, Any]:
        cursor.execute(
            """
            SELECT object_id, object_key, original_object_key, physical_bytes,
                   object_role, storage_tier, status, active_reference_count,
                   retention_until, legal_hold, governance_hold, rebuild_source_id,
                   version, checksum_sha256
            FROM storage.managed_objects
            WHERE project_id = %s AND object_id = %s
            FOR UPDATE
            """,
            (project_id, object_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise LifecycleExecutionBlocked("managed lifecycle object is missing")
        names = [column.name for column in cursor.description]
        return dict(zip(names, raw, strict=True))

    def _apply_object_action(
        self,
        cursor: Any,
        command: LifecycleBatchCommand,
        record: dict[str, Any],
        now: datetime,
    ) -> None:
        action = command.action
        kind = {
            LifecyclePolicyAction.ARCHIVE: "archive",
            LifecyclePolicyAction.TRANSITION_TO_COLD: "cold",
            LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE: "trash",
        }[action]
        destination_identity = canonical_hash(
            {
                "project_id": command.project_id,
                "object_id": record["object_id"],
                "original_object_key": record["original_object_key"],
                "kind": kind,
            }
        )
        destination = f"_hc_governance/{kind}/{destination_identity}"
        self._move(
            str(record["object_key"]),
            destination,
            int(record["physical_bytes"]),
        )
        version = int(record["version"]) + 1
        status = {
            LifecyclePolicyAction.ARCHIVE: "ARCHIVED",
            LifecyclePolicyAction.TRANSITION_TO_COLD: "ACTIVE",
            LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE: "TRASHED",
        }[action]
        tier = {
            LifecyclePolicyAction.ARCHIVE: "ARCHIVE",
            LifecyclePolicyAction.TRANSITION_TO_COLD: "COLD",
            LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE: str(record["storage_tier"]),
        }[action]
        recoverable_until = (
            now + timedelta(days=30)
            if action is LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE
            else None
        )
        cursor.execute(
            """
            UPDATE storage.managed_objects
            SET object_key = %s, status = %s, storage_tier = %s,
                recoverable_until = %s, version = %s, etag = %s, updated_at = %s
            WHERE project_id = %s AND object_id = %s AND version = %s
            """,
            (
                destination,
                status,
                tier,
                recoverable_until,
                version,
                ResourceVersion(version).etag,
                now,
                command.project_id,
                record["object_id"],
                record["version"],
            ),
        )
        if cursor.rowcount != 1:
            raise LifecycleExecutionBlocked("managed object changed during execution")
        action_name = {
            LifecyclePolicyAction.ARCHIVE: "storage.lifecycle.object.archived",
            LifecyclePolicyAction.TRANSITION_TO_COLD: "storage.lifecycle.object.tier_transitioned",
            LifecyclePolicyAction.CLEAN_REBUILDABLE_CACHE: "storage.lifecycle.object.trashed",
        }[action]
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action, resource_type,
                resource_id, request_id, before_hash, after_hash, details, occurred_at
            ) VALUES (%s, %s, NULL, 'storage-lifecycle-worker', %s, 'storage_object',
                      %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                str(uuid4()),
                command.project_id,
                action_name,
                record["object_id"],
                f"storage-lifecycle/{command.execution_id}/{command.batch_index}",
                canonical_hash(
                    {
                        "status": record["status"],
                        "storage_tier": record["storage_tier"],
                        "version": record["version"],
                    }
                ),
                canonical_hash({"status": status, "storage_tier": tier, "version": version}),
                json.dumps(
                    {
                        "execution_id": command.execution_id,
                        "policy_id": command.policy_id,
                        "batch_index": command.batch_index,
                    },
                    sort_keys=True,
                ),
                now,
            ),
        )

    def _move(self, source_key: str, destination_key: str, expected_bytes: int) -> None:
        source = self._object_operator.head(source_key)
        destination = self._object_operator.head(destination_key)
        if destination is None:
            if source is None:
                raise FileNotFoundError("lifecycle source and checkpoint destination are missing")
            destination = self._object_operator.copy(source_key, destination_key)
        if destination.size != expected_bytes:
            raise RuntimeError("lifecycle destination size does not match the durable object")
        if source is not None:
            self._object_operator.delete(source_key)

    @staticmethod
    def _block_item(
        cursor: Any,
        command: LifecycleBatchCommand,
        candidate: LifecycleExecutionCandidate,
        reasons: tuple[str, ...],
        now: datetime,
    ) -> None:
        cursor.execute(
            """
            UPDATE storage.lifecycle_execution_items
            SET status = 'BLOCKED', attempt = attempt + 1,
                blocked_reasons = %s::jsonb, updated_at = %s
            WHERE project_id = %s AND execution_id = %s AND physical_instance_id = %s
            """,
            (
                json.dumps(reasons),
                now,
                command.project_id,
                command.execution_id,
                candidate.physical_instance_id,
            ),
        )
        cursor.execute(
            """
            UPDATE storage.lifecycle_executions
            SET status = 'BLOCKED', updated_at = %s, last_error = %s
            WHERE project_id = %s AND execution_id = %s
            """,
            (now, "LIFECYCLE_PROTECTION_BLOCKED", command.project_id, command.execution_id),
        )

    @staticmethod
    def _fail_item(
        cursor: Any,
        command: LifecycleBatchCommand,
        candidate: LifecycleExecutionCandidate,
        error: Exception,
        now: datetime,
    ) -> None:
        error_code = getattr(error, "code", "LIFECYCLE_OBJECT_OPERATION_FAILED")
        if not isinstance(error_code, str) or len(error_code) > 128:
            error_code = "LIFECYCLE_OBJECT_OPERATION_FAILED"
        cursor.execute(
            """
            UPDATE storage.lifecycle_execution_items
            SET status = 'FAILED', attempt = attempt + 1,
                last_error = %s, last_error_code = %s, updated_at = %s
            WHERE project_id = %s AND execution_id = %s AND physical_instance_id = %s
            """,
            (
                error_code,
                error_code,
                now,
                command.project_id,
                command.execution_id,
                candidate.physical_instance_id,
            ),
        )
        cursor.execute(
            """
            UPDATE storage.lifecycle_executions
            SET status = 'FAILED', updated_at = %s, last_error = %s
            WHERE project_id = %s AND execution_id = %s
            """,
            (now, error_code, command.project_id, command.execution_id),
        )

    @staticmethod
    def _log(
        cursor: Any,
        command: LifecycleBatchCommand,
        event: str,
        details: dict[str, object],
        now: datetime,
        *,
        level: str = "INFO",
    ) -> None:
        cursor.execute(
            """
            INSERT INTO storage.lifecycle_execution_logs (
                project_id, execution_id, level, event, details, occurred_at
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                command.project_id,
                command.execution_id,
                level,
                event,
                json.dumps(details, sort_keys=True),
                now,
            ),
        )

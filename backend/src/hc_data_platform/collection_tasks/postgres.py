"""PostgreSQL repository for collection tasks and fact-based progress."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.security.audit import canonical_hash

from .models import (
    CollectionTarget,
    CollectionTaskRecord,
    CollectionTaskStatus,
    ProgressFacts,
)
from .repository import task_cancelled, task_closed, task_not_found, version_conflict


class PostgresCollectionTaskRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def create(self, task: CollectionTaskRecord) -> CollectionTaskRecord:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO collection_tasks.collection_tasks (
                        collection_task_id, organization_id, project_id, created_by,
                        task_code, name, task_type,
                        scenario, description, target_json, quality_threshold, status,
                        version, create_fingerprint, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        lpad(nextval('collection_tasks.task_code_sequence')::text, 8, '0'),
                        %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (organization_id, project_id, collection_task_id) DO NOTHING
                    """,
                    (
                        task.collection_task_id,
                        task.organization_id,
                        task.project_id,
                        task.created_by,
                        task.name,
                        task.type,
                        task.scenario,
                        task.description,
                        self._target_json(task.target),
                        task.quality_threshold,
                        task.status.value,
                        task.version,
                        task.create_fingerprint,
                        task.created_at,
                        task.updated_at,
                    ),
                )
                inserted = cursor.rowcount == 1
                record = self._select_task(
                    cursor,
                    task.organization_id,
                    task.project_id,
                    task.collection_task_id,
                )
                if record is None:
                    raise task_not_found()
                if record.create_fingerprint != task.create_fingerprint:
                    raise problem(
                        status=409,
                        code="IDEMPOTENCY_KEY_REUSED",
                        title="Idempotency key reused",
                        detail="The create identity already exists with different content.",
                    )
                if inserted:
                    self._audit(cursor, action="collection_task.created", before=None, after=record)
            connection.commit()
            return record
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list(
        self,
        *,
        organization_id: str,
        project_id: str,
        status: CollectionTaskStatus | None,
        limit: int,
        after: tuple[datetime, str] | None,
    ) -> tuple[tuple[CollectionTaskRecord, ...], bool]:
        clauses = ["organization_id = %s", "project_id = %s"]
        params: list[object] = [organization_id, project_id]
        if status is not None:
            clauses.append("status = %s")
            params.append(status.value)
        if after is not None:
            clauses.append("(created_at, collection_task_id) < (%s, %s)")
            params.extend(after)
        params.append(limit + 1)
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT {self._columns()}
                    FROM collection_tasks.collection_tasks
                    WHERE {" AND ".join(clauses)}
                    ORDER BY created_at DESC, collection_task_id DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                records = tuple(self._record(row) for row in cursor.fetchall())
        finally:
            connection.close()
        return records[:limit], len(records) > limit

    def get(
        self,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
    ) -> CollectionTaskRecord | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                return self._select_task(cursor, organization_id, project_id, collection_task_id)
        finally:
            connection.close()

    def update(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
        changes: dict[str, object],
    ) -> CollectionTaskRecord:
        column_names = {
            "name": "name",
            "type": "task_type",
            "scenario": "scenario",
            "description": "description",
            "target": "target_json",
            "quality_threshold": "quality_threshold",
        }
        set_clauses: list[str] = []
        params: list[object] = []
        for field, value in changes.items():
            column = column_names[field]
            if field == "target":
                set_clauses.append(f"{column} = %s::jsonb")
                params.append(self._target_json(value))
            else:
                set_clauses.append(f"{column} = %s")
                params.append(value)
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                current = self._select_task(
                    cursor,
                    organization_id,
                    project_id,
                    collection_task_id,
                    for_update=True,
                )
                if current is None:
                    raise task_not_found()
                if current.status is CollectionTaskStatus.CLOSED:
                    raise task_closed()
                if current.status is CollectionTaskStatus.CANCELLED:
                    raise task_cancelled()
                if current.version != expected_version:
                    raise version_conflict(current.version)
                cursor.execute(
                    f"""
                    UPDATE collection_tasks.collection_tasks
                    SET {", ".join(set_clauses)}, version = version + 1, updated_at = now()
                    WHERE organization_id = %s AND project_id = %s AND collection_task_id = %s
                    RETURNING {self._columns()}
                    """,
                    (*params, organization_id, project_id, collection_task_id),
                )
                updated = self._record(cursor.fetchone())
                self._audit(
                    cursor,
                    action="collection_task.updated",
                    before=current,
                    after=updated,
                )
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
    ) -> CollectionTaskRecord:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                current = self._select_task(
                    cursor,
                    organization_id,
                    project_id,
                    collection_task_id,
                    for_update=True,
                )
                if current is None:
                    raise task_not_found()
                if current.status is CollectionTaskStatus.CLOSED:
                    connection.commit()
                    return current
                if current.status is CollectionTaskStatus.CANCELLED:
                    raise task_cancelled()
                if current.version != expected_version:
                    raise version_conflict(current.version)
                cursor.execute(
                    f"""
                    UPDATE collection_tasks.collection_tasks
                    SET status = 'CLOSED', version = version + 1, updated_at = now()
                    WHERE organization_id = %s AND project_id = %s AND collection_task_id = %s
                    RETURNING {self._columns()}
                    """,
                    (organization_id, project_id, collection_task_id),
                )
                closed = self._record(cursor.fetchone())
                self._audit(
                    cursor,
                    action="collection_task.closed",
                    before=current,
                    after=closed,
                )
                self._notify_lifecycle_transition(cursor, current, closed)
            connection.commit()
            return closed
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def cancel(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
    ) -> CollectionTaskRecord:
        return self._transition(
            organization_id=organization_id,
            project_id=project_id,
            collection_task_id=collection_task_id,
            expected_version=expected_version,
            target_status=CollectionTaskStatus.CANCELLED,
            action="collection_task.cancelled",
        )

    def reopen(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
    ) -> CollectionTaskRecord:
        return self._transition(
            organization_id=organization_id,
            project_id=project_id,
            collection_task_id=collection_task_id,
            expected_version=expected_version,
            target_status=CollectionTaskStatus.ACTIVE,
            action="collection_task.reopened",
        )

    def _transition(
        self,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        expected_version: int,
        target_status: CollectionTaskStatus,
        action: str,
    ) -> CollectionTaskRecord:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                current = self._select_task(
                    cursor,
                    organization_id,
                    project_id,
                    collection_task_id,
                    for_update=True,
                )
                if current is None:
                    raise task_not_found()
                if current.status is target_status:
                    connection.commit()
                    return current
                if target_status is CollectionTaskStatus.CANCELLED:
                    if current.status is CollectionTaskStatus.CLOSED:
                        raise task_closed()
                    if current.status is not CollectionTaskStatus.ACTIVE:
                        raise task_cancelled()
                elif target_status is CollectionTaskStatus.ACTIVE:
                    if current.status not in {
                        CollectionTaskStatus.CLOSED,
                        CollectionTaskStatus.CANCELLED,
                    }:
                        raise task_closed()
                else:
                    raise ValueError(
                        f"unsupported collection task transition target {target_status.value}"
                    )
                if current.version != expected_version:
                    raise version_conflict(current.version)
                cursor.execute(
                    f"""
                    UPDATE collection_tasks.collection_tasks
                    SET status = %s, version = version + 1, updated_at = now()
                    WHERE organization_id = %s AND project_id = %s AND collection_task_id = %s
                    RETURNING {self._columns()}
                    """,
                    (target_status.value, organization_id, project_id, collection_task_id),
                )
                updated = self._record(cursor.fetchone())
                self._audit(cursor, action=action, before=current, after=updated)
                self._notify_lifecycle_transition(cursor, current, updated)
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def progress(
        self,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        region_code: str,
    ) -> ProgressFacts:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                if (
                    self._select_task(cursor, organization_id, project_id, collection_task_id)
                    is None
                ):
                    raise task_not_found()
                cursor.execute(
                    """
                    WITH received AS (
                        SELECT DISTINCT rollout.project_id, rollout.region_code,
                                        rollout.rollout_id, object.data_package_id
                        FROM ingest.collection_jobs job
                        JOIN ingest.rollouts rollout
                          ON rollout.project_id = job.project_id
                         AND rollout.collection_job_id = job.collection_job_id
                        JOIN ingest.rollout_objects object
                          ON object.project_id = rollout.project_id
                         AND object.rollout_id = rollout.rollout_id
                        WHERE job.project_id = %s AND job.task_id = %s
                          AND job.region_code = %s
                    ), counts AS (
                        SELECT count(*)::bigint AS received_count,
                               count(*) FILTER (WHERE summary.status = 'PASS')::bigint
                                   AS pass_count,
                               count(*) FILTER (WHERE summary.status = 'RISK')::bigint
                                   AS risk_count,
                               count(*) FILTER (WHERE summary.status = 'REJECT')::bigint
                                   AS reject_count
                        FROM received
                        LEFT JOIN quality_rollout_summaries summary
                          ON summary.project_id = received.project_id
                         AND summary.region_code = received.region_code
                         AND summary.rollout_id = received.rollout_id
                    ), duration_rows AS (
                        SELECT received.data_package_id,
                               CASE
                                   WHEN NULLIF(
                                       discovery.preflight_json #>> '{time_range,start_time}', ''
                                   ) IS NOT NULL
                                    AND NULLIF(
                                       discovery.preflight_json #>> '{time_range,end_time}', ''
                                   ) IS NOT NULL
                                   THEN extract(
                                       epoch FROM (
                                           (
                                               discovery.preflight_json #>> ARRAY[
                                                   'time_range',
                                                   'end_time'
                                               ]
                                           )::timestamptz
                                           - (
                                               discovery.preflight_json #>> ARRAY[
                                                   'time_range',
                                                   'start_time'
                                               ]
                                           )::timestamptz
                                       )
                                   )
                                   ELSE NULL
                               END AS duration_seconds
                        FROM received
                        LEFT JOIN ingest.manifest_discoveries discovery
                          ON discovery.project_id = received.project_id
                         AND discovery.region_code = received.region_code
                         AND discovery.data_package_id = received.data_package_id
                    ), duration_facts AS (
                        SELECT sum(duration_seconds)::double precision AS captured_duration_seconds,
                               count(*) FILTER (WHERE duration_seconds IS NOT NULL)::bigint
                                   AS duration_observed_package_count,
                               count(*) FILTER (WHERE duration_seconds IS NULL)::bigint
                                   AS duration_unknown_package_count
                        FROM duration_rows
                    ), discoveries AS (
                        SELECT discovery.preflight_json
                        FROM received
                        JOIN ingest.manifest_discoveries discovery
                          ON discovery.project_id = received.project_id
                         AND discovery.region_code = received.region_code
                         AND discovery.data_package_id = received.data_package_id
                    )
                    SELECT now(), counts.received_count, counts.pass_count,
                           counts.risk_count, counts.reject_count,
                           duration_facts.captured_duration_seconds,
                           duration_facts.duration_observed_package_count,
                           duration_facts.duration_unknown_package_count,
                           COALESCE((
                               SELECT array_agg(DISTINCT source_id ORDER BY source_id)
                               FROM (
                                   SELECT preflight_json #>> '{identifiers,robot_id}' AS source_id
                                   FROM discoveries
                                   UNION
                                   SELECT preflight_json
                                              #>> '{identifiers,pico_instance_id}' AS source_id
                                   FROM discoveries
                               ) sources
                               WHERE source_id IS NOT NULL AND source_id <> ''
                           ), ARRAY[]::text[]),
                           COALESCE((
                               SELECT array_agg(DISTINCT camera->>'camera_id'
                                                ORDER BY camera->>'camera_id')
                               FROM discoveries,
                                    jsonb_array_elements(
                                        COALESCE(
                                            preflight_json #> '{discovery,cameras}',
                                            '[]'::jsonb
                                        )
                                    ) camera
                               WHERE camera->>'camera_id' IS NOT NULL
                           ), ARRAY[]::text[]),
                           COALESCE((
                               SELECT array_agg(DISTINCT topic->>'name' ORDER BY topic->>'name')
                               FROM discoveries,
                                    jsonb_array_elements(
                                        COALESCE(
                                            preflight_json #> '{discovery,topics}',
                                            '[]'::jsonb
                                        )
                                    ) topic
                               WHERE topic->>'name' IS NOT NULL
                           ), ARRAY[]::text[])
                    FROM counts
                    CROSS JOIN duration_facts
                    """,
                    (project_id, collection_task_id, region_code),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("progress aggregate did not return a row")
                return ProgressFacts(
                    as_of=row[0],
                    received_package_count=int(row[1]),
                    pass_count=int(row[2]),
                    risk_count=int(row[3]),
                    reject_count=int(row[4]),
                    captured_duration_seconds=(None if row[5] is None else float(row[5])),
                    duration_observed_package_count=int(row[6]),
                    duration_unknown_package_count=int(row[7]),
                    device_ids=tuple(map(str, row[8])),
                    camera_ids=tuple(map(str, row[9])),
                    topic_names=tuple(map(str, row[10])),
                )
        finally:
            connection.close()

    @staticmethod
    def _columns() -> str:
        return (
            "collection_task_id, organization_id, project_id, created_by, task_code, name, "
            "task_type, scenario, "
            "description, target_json, quality_threshold, status, version, "
            "create_fingerprint, created_at, updated_at"
        )

    def _select_task(
        self,
        cursor: Any,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
        *,
        for_update: bool = False,
    ) -> CollectionTaskRecord | None:
        suffix = " FOR UPDATE" if for_update else ""
        cursor.execute(
            f"""
            SELECT {self._columns()}
            FROM collection_tasks.collection_tasks
            WHERE organization_id = %s AND project_id = %s AND collection_task_id = %s{suffix}
            """,
            (organization_id, project_id, collection_task_id),
        )
        row = cursor.fetchone()
        return None if row is None else self._record(row)

    @staticmethod
    def _record(row: object) -> CollectionTaskRecord:
        values: tuple[object, ...] = tuple(row)  # type: ignore[arg-type]
        target = values[9]
        if isinstance(target, str):
            target = json.loads(target)
        return CollectionTaskRecord(
            collection_task_id=str(values[0]),
            organization_id=str(values[1]),
            project_id=str(values[2]),
            created_by=None if values[3] is None else str(values[3]),
            task_code=str(values[4]),
            name=str(values[5]),
            type=str(values[6]),
            scenario=str(values[7]),
            description=str(values[8]),
            target=None if target is None else CollectionTarget.model_validate(target),
            quality_threshold=(None if values[10] is None else float(cast(float, values[10]))),
            status=CollectionTaskStatus(str(values[11])),
            version=cast(int, values[12]),
            create_fingerprint=str(values[13]),
            created_at=values[14],
            updated_at=values[15],
        )

    @staticmethod
    def _target_json(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, CollectionTarget):
            payload = value.model_dump(mode="json")
        else:
            payload = CollectionTarget.model_validate(value).model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _notify_lifecycle_transition(
        cursor: Any,
        before: CollectionTaskRecord,
        after: CollectionTaskRecord,
    ) -> None:
        """Persist an owner notification with the status update, never as an outbox side effect.

        A task created by an older service or a non-account automation identity has no
        recipient.  It remains auditable, but cannot safely manufacture an inbox owner.
        Session-authenticated API creation supplies a UUID principal, and the unique
        recipient/event key makes an idempotent command incapable of duplicating an event.
        """

        if before.status is after.status or after.created_by is None:
            return
        try:
            recipient_id = UUID(after.created_by)
        except ValueError:
            return
        kind = {
            CollectionTaskStatus.CLOSED: "COLLECTION_TASK_CLOSED",
            CollectionTaskStatus.CANCELLED: "COLLECTION_TASK_CANCELLED",
            CollectionTaskStatus.ACTIVE: "COLLECTION_TASK_REOPENED",
        }[after.status]
        event_key = (
            f"collection-task:{after.collection_task_id}:{after.status.value.lower()}:"
            f"v{after.version}"
        )
        context = current_request_context()
        cursor.execute("SELECT set_config('app.subject_id', %s, true)", (str(recipient_id),))
        try:
            cursor.execute(
                """
                INSERT INTO access_control.account_notifications (
                    notification_id, recipient_id, kind, organization_id, project_id,
                    access_request_id, resource_type, resource_id, event_key, state
                ) VALUES (
                    %s::uuid, %s::uuid, %s, %s, %s,
                    NULL, 'COLLECTION_TASK', %s, %s, 'UNREAD'
                )
                ON CONFLICT (recipient_id, event_key) DO NOTHING
                """,
                (
                    str(uuid4()),
                    str(recipient_id),
                    kind,
                    after.organization_id,
                    after.project_id,
                    after.collection_task_id,
                    event_key,
                ),
            )
        except Exception:
            # The enclosing lifecycle transaction must roll back the status update,
            # audit row, and notification together.  Do not mask its root cause by
            # issuing another command against PostgreSQL's aborted transaction.
            raise
        else:
            cursor.execute(
                "SELECT set_config('app.subject_id', %s, true)",
                (context.subject_id or "",),
            )

    @staticmethod
    def _audit(
        cursor: Any,
        *,
        action: str,
        before: CollectionTaskRecord | None,
        after: CollectionTaskRecord,
    ) -> None:
        context = current_request_context()
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action, resource_type,
                resource_id, request_id, before_hash, after_hash, details, occurred_at
            ) VALUES (%s, %s, NULL, %s, %s, 'collection_task', %s, %s, %s, %s, '{}'::jsonb, now())
            """,
            (
                str(uuid4()),
                after.project_id,
                context.subject_id or "unknown",
                action,
                after.collection_task_id,
                context.request_id,
                None if before is None else canonical_hash(before.public().model_dump(mode="json")),
                canonical_hash(after.public().model_dump(mode="json")),
            ),
        )

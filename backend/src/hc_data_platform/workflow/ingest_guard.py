"""Bound shared MCAP/LeRobot preparation and serialize final Dataset publication."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from hc_data_platform.core.context import current_request_context
from hc_data_platform.security.outbox import OutboxBackpressure

from .models import IngestRolloutWorkflowInput, JobRecord, WorkflowJobPersistenceActivityInput
from .names import INGEST_ROLLOUT_WORKFLOW
from .postgres import PostgresWorkflowJobRepository


class IngestDatasetBusy(OutboxBackpressure):
    code = "INGEST_DATASET_BUSY"


class PostgresIngestDispatchGuard:
    """Reserve a durable PENDING job before launch; outbox retries resume that same ID.

    New workflows prepare independently up to a bounded capacity. Legacy inputs
    still reserve the whole Dataset until their version-bound work completes.
    """

    def __init__(self, connection_factory: Callable[[], Any], *, max_active: int = 8) -> None:
        if max_active < 1:
            raise ValueError("ingest capacity must be positive")
        self._connections = connection_factory
        self._max_active = max_active

    def reserve(
        self,
        request: IngestRolloutWorkflowInput,
        workflow_id: str,
        *,
        retry_terminal: bool = False,
    ) -> None:
        context = current_request_context()
        if (
            context.organization_id != request.organization_id
            or context.project_id != request.project_id
            or context.region_code != request.region_code
            or not context.service_identity
            or request.organization_id is None
        ):
            raise ValueError("ingest dispatch reservation scope does not match worker context")
        key = json.dumps(
            ["ingest-dispatch-v1", request.organization_id, request.project_id, request.dataset_id]
        ).encode()
        lock_id = int.from_bytes(hashlib.sha256(key).digest()[:8], "big", signed=True)
        connection = self._connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", (lock_id,))
                if not cursor.fetchone()[0]:
                    raise IngestDatasetBusy("another dispatcher is reserving this Dataset")
                cursor.execute(
                    "SELECT status FROM workflow.jobs WHERE organization_id=%s "
                    "AND project_id=%s AND workflow_id=%s",
                    (request.organization_id, request.project_id, workflow_id),
                )
                existing = cursor.fetchone()
                if existing is not None and (
                    not retry_terminal or existing[0] not in {"TECHNICAL_FAILED", "CANCELLED"}
                ):
                    # A dispatch retry must reconnect to its existing execution,
                    # including a reservation persisted before a launcher crash.
                    return
                parallel = getattr(request, "parallel_preparation", False)
                selection = (
                    "SELECT count(*), "
                    "bool_or(COALESCE(job.result->>'parallel_preparation', 'false') <> 'true')"
                    if parallel
                    else "SELECT 1"
                )
                cursor.execute(
                    f"""
                    {selection}
                      FROM workflow.jobs job
                      JOIN ingest.rollouts rollout
                        ON rollout.organization_id=job.organization_id
                       AND rollout.project_id=job.project_id
                       AND rollout.rollout_id=job.resource_id
                      JOIN ingest.collection_jobs collection
                        ON collection.organization_id=rollout.organization_id
                       AND collection.project_id=rollout.project_id
                       AND collection.collection_job_id=rollout.collection_job_id
                      JOIN collection_tasks.collection_tasks task
                        ON task.organization_id=collection.organization_id
                       AND task.project_id=collection.project_id
                       AND task.collection_task_id=collection.task_id
                     WHERE job.organization_id=%s AND job.project_id=%s
                       AND job.workflow_id<>%s
                       AND task.dataset_id=%s AND job.job_type=%s
                       AND job.status IN ('PENDING','RUNNING')
                     LIMIT 1
                    """,
                    (
                        request.organization_id,
                        request.project_id,
                        workflow_id,
                        request.dataset_id,
                        INGEST_ROLLOUT_WORKFLOW,
                    ),
                )
                active = cursor.fetchone()
                if (parallel and active and (active[0] >= self._max_active or active[1])) or (
                    not parallel and active is not None
                ):
                    raise IngestDatasetBusy("Dataset preparation capacity is occupied")
                cursor.execute(
                    """
                    SELECT 1 FROM lance_dataset_versions version
                     WHERE version.project_id=%s AND version.dataset_id=%s
                       AND version.rollout_id<>%s
                       AND NOT EXISTS (
                           SELECT 1 FROM dataset_registry.dataset_version_content_projections page
                            WHERE page.organization_id=%s AND page.project_id=version.project_id
                              AND page.region_code=%s AND page.dataset_id=version.dataset_id
                              AND page.version_id='version_lance_' || version.version::text
                       ) LIMIT 1
                    """,
                    (
                        request.project_id,
                        request.dataset_id,
                        request.rollout_id,
                        request.organization_id,
                        request.region_code,
                    ),
                )
                if cursor.fetchone() is not None:
                    raise IngestDatasetBusy("an earlier Dataset commit is awaiting publication")
                now = datetime.now(timezone.utc)
                # This separate committed transaction makes the reservation
                # durable before the advisory lock is released and before RPC.
                PostgresWorkflowJobRepository(self._connections).put_job(
                    WorkflowJobPersistenceActivityInput(
                        organization_id=request.organization_id,
                        region_code=request.region_code,
                        job=JobRecord(
                            workflow_id=workflow_id,
                            job_type=INGEST_ROLLOUT_WORKFLOW,
                            project_id=request.project_id,
                            resource_id=request.rollout_id,
                            stage="dispatch_pending",
                            result={"parallel_preparation": True} if parallel else None,
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class IngestCommitPending(RuntimeError):
    code = "INGEST_COMMIT_PENDING"


class PostgresIngestCommitGuard:
    """Hold only the final publication, including media binding and projection.

    This lock is separate from the catalog's writer lock, which remains the
    final authority for atomic version allocation and immutable storage receipts.
    """

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connections = connection_factory

    @contextmanager
    def acquire(self, scope: Any, dataset_id: str, rollout_id: str) -> Iterator[None]:
        context = current_request_context()
        if not context.service_identity or (
            context.organization_id,
            context.project_id,
            context.region_code,
        ) != (scope.organization_id, scope.project_id, scope.region_code):
            raise ValueError("Dataset commit lock scope does not match worker context")
        key = json.dumps(
            ["ingest-publication-v1", scope.organization_id, scope.project_id, dataset_id]
        )
        connection = self._connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
                if not cursor.fetchone()[0]:
                    raise IngestCommitPending("another Dataset publication is in progress")
                cursor.execute(
                    """SELECT 1 FROM lance_dataset_versions v
                       WHERE v.project_id=%s AND v.dataset_id=%s AND v.rollout_id<>%s
                       AND NOT EXISTS (
                         SELECT 1 FROM dataset_registry.dataset_version_content_projections p
                         WHERE p.organization_id=%s AND p.project_id=v.project_id
                         AND p.region_code=%s AND p.dataset_id=v.dataset_id
                         AND p.version_id='version_lance_' || v.version::text
                       ) LIMIT 1""",
                    (
                        scope.project_id,
                        dataset_id,
                        rollout_id,
                        scope.organization_id,
                        scope.region_code,
                    ),
                )
                if cursor.fetchone() is not None:
                    raise IngestCommitPending("the preceding Dataset publication needs recovery")
                yield
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

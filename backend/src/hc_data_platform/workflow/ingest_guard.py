"""Keep one raw-ingest writer active per Dataset while its media version is reserved."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from hc_data_platform.core.context import current_request_context

from .models import IngestRolloutWorkflowInput, JobRecord, WorkflowJobPersistenceActivityInput
from .names import INGEST_ROLLOUT_WORKFLOW
from .postgres import PostgresWorkflowJobRepository


class IngestDatasetBusy(RuntimeError):
    code = "INGEST_DATASET_BUSY"


class PostgresIngestDispatchGuard:
    """Reserve a durable PENDING job before launch; outbox retries resume that same ID.

    Media artifacts include the next Dataset version, so two ingest workflows
    cannot reserve that version concurrently. Waiting uploads stay in the outbox
    instead of occupying activity slots or consuming activity queue timeouts.
    """

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connections = connection_factory

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
                    not retry_terminal or existing[0] != "TECHNICAL_FAILED"
                ):
                    # A dispatch retry must reconnect to its existing execution,
                    # including a reservation persisted before a launcher crash.
                    return
                cursor.execute(
                    """
                    SELECT 1
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
                if cursor.fetchone() is not None:
                    raise IngestDatasetBusy("a raw ingest is still writing this Dataset")
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

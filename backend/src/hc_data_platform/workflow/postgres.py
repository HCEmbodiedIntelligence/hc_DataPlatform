"""PostgreSQL write adapter for durable Temporal workflow job snapshots."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.context import current_request_context

from .models import WorkflowJobPersistenceActivityInput

UPSERT_WORKFLOW_JOB = """
INSERT INTO workflow.jobs (
    job_id,
    organization_id,
    workflow_id,
    workflow_run_id,
    workflow_version,
    project_id,
    resource_id,
    job_type,
    status,
    stage,
    attempt,
    result,
    error_code,
    error_message,
    cancellation_requested,
    created_at,
    updated_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s
)
ON CONFLICT (workflow_id) DO UPDATE SET
    workflow_run_id = EXCLUDED.workflow_run_id,
    workflow_version = EXCLUDED.workflow_version,
    resource_id = EXCLUDED.resource_id,
    job_type = EXCLUDED.job_type,
    status = EXCLUDED.status,
    stage = EXCLUDED.stage,
    attempt = EXCLUDED.attempt,
    result = EXCLUDED.result,
    error_code = EXCLUDED.error_code,
    error_message = EXCLUDED.error_message,
    cancellation_requested = EXCLUDED.cancellation_requested,
    updated_at = EXCLUDED.updated_at
WHERE workflow.jobs.organization_id = EXCLUDED.organization_id
  AND workflow.jobs.project_id = EXCLUDED.project_id
  AND workflow.jobs.updated_at <= EXCLUDED.updated_at
""".strip()


class PostgresWorkflowJobRepository:
    """Idempotently mirror workflow-owned state without becoming its source of truth."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def put_job(self, request: WorkflowJobPersistenceActivityInput) -> None:
        context = current_request_context()
        job = request.job
        if (
            context.organization_id != request.organization_id
            or context.project_id != job.project_id
            or context.region_code != request.region_code
            or not context.service_identity
        ):
            raise ValueError("workflow job persistence scope does not match worker context")

        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    UPSERT_WORKFLOW_JOB,
                    (
                        uuid5(NAMESPACE_URL, f"hc-data-platform/workflow-job/{job.workflow_id}"),
                        request.organization_id,
                        job.workflow_id,
                        job.workflow_run_id,
                        job.workflow_version,
                        job.project_id,
                        job.resource_id,
                        job.job_type,
                        job.status.value,
                        job.stage,
                        job.attempt,
                        None if job.result is None else json.dumps(job.result, sort_keys=True),
                        job.error_code,
                        job.error_message,
                        job.cancellation_requested,
                        job.created_at,
                        job.updated_at,
                    ),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

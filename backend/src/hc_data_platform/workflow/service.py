from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Protocol, cast

from hc_data_platform.core.errors import ProblemException, problem

from .models import (
    TERMINAL_JOB_STATUSES,
    JobListV1,
    JobRecord,
    JobStatus,
    parse_workflow_id,
)


class JobStatusPort(Protocol):
    def get(self, job_id: str) -> JobRecord: ...

    def list(
        self,
        *,
        project_id: str | None = None,
        status: JobStatus | None = None,
    ) -> JobListV1: ...

    def cancel(self, job_id: str) -> JobRecord: ...


class WorkflowLauncher(JobStatusPort, Protocol):
    def start(
        self,
        *,
        workflow_id: str,
        job_type: str,
        project_id: str,
        resource_id: str,
        runner: Callable[[], dict[str, Any]],
    ) -> JobRecord: ...


class InMemoryWorkflowLauncher:
    """Thread-safe reference launcher with deterministic workflow deduplication."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._by_workflow: dict[str, str] = {}
        self._lock = RLock()

    def start(
        self,
        *,
        workflow_id: str,
        job_type: str,
        project_id: str,
        resource_id: str,
        runner: Callable[[], dict[str, Any]],
    ) -> JobRecord:
        with self._lock:
            existing_id = self._by_workflow.get(workflow_id)
            if existing_id is not None:
                return self._jobs[existing_id]
            now = datetime.now(timezone.utc)
            job = JobRecord(
                workflow_id=workflow_id,
                job_type=job_type,
                project_id=project_id,
                resource_id=resource_id,
                status=JobStatus.RUNNING,
                stage="running",
                attempt=1,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.job_id] = job
            self._by_workflow[workflow_id] = job.job_id
        try:
            runner_result = dict(runner())
            status_value = runner_result.pop("_job_status", JobStatus.SUCCEEDED)
            updated = job.model_copy(
                update={
                    "status": JobStatus(status_value),
                    "stage": "completed",
                    "result": runner_result,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        except Exception as exc:
            if isinstance(exc, ProblemException):
                error_code = exc.problem.code
                error_message = exc.problem.title
            else:
                error_code = "WORKFLOW_EXECUTION_FAILED"
                error_message = "The workflow execution failed."
            updated = job.model_copy(
                update={
                    "status": JobStatus.TECHNICAL_FAILED,
                    "stage": "technical_failed",
                    "error_code": error_code,
                    "error_message": error_message,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        with self._lock:
            current = self._jobs[job.job_id]
            if current.status is JobStatus.CANCELLED:
                return current
            self._jobs[job.job_id] = updated
        return updated

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise _job_not_found() from exc

    def list(
        self,
        *,
        project_id: str | None = None,
        status: JobStatus | None = None,
    ) -> JobListV1:
        with self._lock:
            jobs = tuple(self._jobs.values())
        filtered = tuple(
            sorted(
                (
                    job
                    for job in jobs
                    if (project_id is None or job.project_id == project_id)
                    and (status is None or job.status is status)
                ),
                key=lambda job: (job.created_at, job.job_id),
                reverse=True,
            )
        )
        return JobListV1(items=filtered, total=len(filtered))

    def cancel(self, job_id: str) -> JobRecord:
        job = self.get(job_id)
        if job.status in TERMINAL_JOB_STATUSES:
            return job
        cancelled = job.model_copy(
            update={
                "status": JobStatus.CANCELLED,
                "stage": "cancelled",
                "cancellation_requested": True,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        with self._lock:
            self._jobs[job_id] = cancelled
        return cancelled


class TemporalWorkflowLauncher:
    """Durable launcher/status adapter backed directly by Temporal history."""

    def __init__(
        self,
        target: str,
        namespace: str = "default",
        task_queue: str = "hc-data-pipeline",
        client: Any | None = None,
    ) -> None:
        self.target = target
        self.namespace = namespace
        self.task_queue = task_queue
        self._client: Any | None = client
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._connect_lock:
            if self._client is None:
                try:
                    from temporalio.client import Client
                    from temporalio.contrib.pydantic import pydantic_data_converter
                except ImportError as exc:
                    raise RuntimeError("install the 'workflow' extra to use Temporal") from exc
                self._client = await Client.connect(
                    self.target,
                    namespace=self.namespace,
                    data_converter=pydantic_data_converter,
                )
        return self._client

    async def start(
        self,
        *,
        workflow_name: str,
        workflow_input: Any,
        workflow_id: str,
        job_type: str,
        project_id: str,
        resource_id: str,
    ) -> JobRecord:
        try:
            from temporalio.common import WorkflowIDReusePolicy
            from temporalio.exceptions import WorkflowAlreadyStartedError
        except ImportError as exc:
            raise RuntimeError("install the 'workflow' extra to use Temporal") from exc

        client = await self.connect()
        try:
            handle = await client.start_workflow(
                workflow_name,
                workflow_input,
                id=workflow_id,
                task_queue=self.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            return await self.get(workflow_id)

        now = datetime.now(timezone.utc)
        return JobRecord(
            job_id=workflow_id,
            workflow_id=workflow_id,
            workflow_run_id=handle.run_id,
            job_type=job_type,
            project_id=project_id,
            resource_id=resource_id,
            status=JobStatus.PENDING,
            stage="pending",
            created_at=now,
            updated_at=now,
        )

    async def get(self, job_id: str) -> JobRecord:
        try:
            from temporalio.client import (
                WorkflowExecutionStatus,
                WorkflowQueryFailedError,
                WorkflowQueryRejectedError,
            )
        except ImportError as exc:
            raise RuntimeError("install the 'workflow' extra to use Temporal") from exc
        client = await self.connect()
        handle = client.get_workflow_handle(job_id)
        try:
            description = await handle.describe()
        except Exception as exc:
            if _is_temporal_not_found(exc):
                raise _job_not_found() from exc
            raise

        execution_status = _description_status(description)
        if execution_status is WorkflowExecutionStatus.COMPLETED:
            completed_handle = client.get_workflow_handle(job_id, result_type=JobRecord)
            return cast(JobRecord, await completed_handle.result())

        try:
            job = await handle.query(
                "job",
                result_type=JobRecord,
                rpc_timeout=timedelta(seconds=2),
            )
        except (WorkflowQueryFailedError, WorkflowQueryRejectedError):
            job = _job_from_description(job_id, description)
        except Exception as exc:
            if not _is_temporal_query_unavailable(exc):
                raise
            job = _job_from_description(job_id, description)
        return _apply_temporal_execution_status(job, execution_status)

    async def list(
        self,
        *,
        project_id: str | None = None,
        status: JobStatus | None = None,
    ) -> JobListV1:
        client = await self.connect()
        jobs: list[JobRecord] = []
        async for execution in client.list_workflows(limit=200):
            try:
                parse_workflow_id(execution.id)
            except ValueError:
                continue
            job = await self.get(execution.id)
            if project_id is not None and job.project_id != project_id:
                continue
            if status is not None and job.status is not status:
                continue
            jobs.append(job)
        jobs.sort(key=lambda item: (item.created_at, item.job_id), reverse=True)
        return JobListV1(items=tuple(jobs), total=len(jobs))

    async def cancel(self, job_id: str) -> JobRecord:
        job = await self.get(job_id)
        if job.status in TERMINAL_JOB_STATUSES:
            return job
        client = await self.connect()
        handle = client.get_workflow_handle(job_id)
        await handle.cancel(reason="cancelled through HC Data Platform Job API")
        return job.model_copy(
            update={
                "status": JobStatus.CANCELLED,
                "stage": "cancelled",
                "cancellation_requested": True,
                "updated_at": datetime.now(timezone.utc),
            }
        )


def _job_not_found() -> Exception:
    return problem(
        status=404,
        code="JOB_NOT_FOUND",
        title="Job not found",
        detail="The requested workflow job does not exist.",
    )


def _is_temporal_not_found(error: BaseException) -> bool:
    try:
        from temporalio.service import RPCError, RPCStatusCode
    except ImportError:
        return False
    return isinstance(error, RPCError) and error.status is RPCStatusCode.NOT_FOUND


def _is_temporal_query_unavailable(error: BaseException) -> bool:
    try:
        from temporalio.service import RPCError, RPCStatusCode
    except ImportError:
        return False
    return isinstance(error, RPCError) and error.status in {
        RPCStatusCode.DEADLINE_EXCEEDED,
        RPCStatusCode.UNAVAILABLE,
    }


def _job_from_description(job_id: str, description: Any) -> JobRecord:
    info = description.raw_description.workflow_execution_info
    try:
        kind, version, project_id, resource_id = parse_workflow_id(job_id)
    except ValueError:
        kind, version, project_id, resource_id = (
            info.type.name,
            "v1",
            "unknown",
            job_id,
        )
    start_time = info.start_time.ToDatetime().replace(tzinfo=timezone.utc)
    close_time = (
        info.close_time.ToDatetime().replace(tzinfo=timezone.utc)
        if info.HasField("close_time")
        else None
    )
    return JobRecord(
        job_id=job_id,
        workflow_id=job_id,
        workflow_run_id=info.execution.run_id,
        workflow_version=version,
        job_type=kind,
        project_id=project_id,
        resource_id=resource_id,
        status=JobStatus.RUNNING,
        stage="running",
        created_at=start_time,
        updated_at=close_time or datetime.now(timezone.utc),
    )


def _description_status(description: Any) -> Any:
    try:
        from temporalio.client import WorkflowExecutionStatus
    except ImportError:
        return None
    raw_status = description.raw_description.workflow_execution_info.status
    try:
        return WorkflowExecutionStatus(raw_status)
    except ValueError:
        return None


def _apply_temporal_execution_status(job: JobRecord, execution_status: Any) -> JobRecord:
    try:
        from temporalio.client import WorkflowExecutionStatus
    except ImportError:
        return job
    if execution_status is WorkflowExecutionStatus.CANCELED:
        return job.model_copy(
            update={
                "status": JobStatus.CANCELLED,
                "stage": "cancelled",
                "cancellation_requested": True,
            }
        )
    if execution_status in {
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.TERMINATED,
        WorkflowExecutionStatus.TIMED_OUT,
    }:
        return job.model_copy(
            update={
                "status": JobStatus.TECHNICAL_FAILED,
                "stage": "technical_failed",
                "error_code": execution_status.name,
            }
        )
    return job

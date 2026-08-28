"""Provider-neutral asynchronous automatic-annotation job orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Literal, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.auth import AuthContext, Role

from .models import (
    AnnotationOperation,
    AnnotationRevision,
    AnnotationTag,
    AnnotationTask,
    AutoAnnotationCapability,
    AutoAnnotationProviderDescriptor,
    AutoAnnotationSamplingReference,
)
from .service import AnnotationService

Clock = Callable[[], datetime]


class AutoAnnotationInputSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start_step: int = Field(default=0, ge=0)
    end_step: int | None = Field(default=None, gt=0)
    modalities: tuple[str, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_range_and_modalities(self) -> AutoAnnotationInputSelection:
        if self.end_step is not None and self.start_step >= self.end_step:
            raise ValueError("end_step must be greater than start_step")
        if len(self.modalities) != len(set(self.modalities)):
            raise ValueError("modalities must be unique")
        if any(not value or len(value) > 256 for value in self.modalities):
            raise ValueError("modalities must contain bounded non-empty names")
        return self


class AutoAnnotationUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_units: int = Field(ge=0)
    output_units: int = Field(ge=0)
    cost_micros: int = Field(ge=0)


class AutoAnnotationProviderRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: int = Field(ge=1)
    rollout_id: str = Field(min_length=1)
    source_revision: int = Field(ge=0)
    base_lance_version: int = Field(ge=1)
    base_step_count: int = Field(ge=1)
    tag_schema_id: str = Field(min_length=1)
    tag_schema_version: int = Field(ge=1)
    model: str = Field(min_length=1, max_length=256)
    input_selection: AutoAnnotationInputSelection
    sampling_reference: AutoAnnotationSamplingReference | None = None


class AutoAnnotationProviderResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tags: tuple[AnnotationTag, ...] = ()
    operations: tuple[AnnotationOperation, ...] = ()
    usage: AutoAnnotationUsage


class AutoAnnotationJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    region_code: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    source_revision: int = Field(ge=0)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    input_selection: AutoAnnotationInputSelection
    sampling_reference: AutoAnnotationSamplingReference | None = None
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "APPLIED"]
    progress_percent: int = Field(ge=0, le=100)
    estimated_cost_micros: int = Field(ge=0)
    usage: AutoAnnotationUsage | None = None
    tags: tuple[AnnotationTag, ...] | None = None
    operations: tuple[AnnotationOperation, ...] | None = None
    applied_revision: int | None = Field(default=None, ge=1)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=512)
    created_by: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_state(self) -> AutoAnnotationJob:
        has_result = (
            self.tags is not None and self.operations is not None and self.usage is not None
        )
        if (self.status in {"SUCCEEDED", "APPLIED"}) != has_result:
            raise ValueError("only successful jobs contain provider results")
        if (self.status == "APPLIED") != (self.applied_revision is not None):
            raise ValueError("only applied jobs contain an applied revision")
        return self


class AutoAnnotationProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def models(self) -> tuple[str, ...]: ...

    def estimate_cost_micros(
        self,
        *,
        model: str,
        selection: AutoAnnotationInputSelection,
        base_step_count: int,
    ) -> int: ...

    def annotate(self, request: AutoAnnotationProviderRequest) -> AutoAnnotationProviderResult: ...


class HttpAutoAnnotationProvider:
    """Production connector for an internal or third-party provider gateway."""

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        models: Sequence[str],
        api_key: str | None,
        timeout_seconds: float = 60.0,
        cost_micros_per_1000_steps: int = 1_000,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("automatic annotation provider endpoint must be absolute HTTP(S)")
        self._name = name
        self._endpoint = endpoint
        self._models = tuple(models)
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._cost_per_1000 = cost_micros_per_1000_steps
        if not self._name or not self._models or len(set(self._models)) != len(self._models):
            raise ValueError("provider name and unique models are required")

    @property
    def name(self) -> str:
        return self._name

    @property
    def models(self) -> tuple[str, ...]:
        return self._models

    def estimate_cost_micros(
        self,
        *,
        model: str,
        selection: AutoAnnotationInputSelection,
        base_step_count: int,
    ) -> int:
        self._require_model(model)
        selected = (selection.end_step or base_step_count) - selection.start_step
        return max(1, (selected * self._cost_per_1000 + 999) // 1000)

    def annotate(self, request: AutoAnnotationProviderRequest) -> AutoAnnotationProviderResult:
        self._require_model(request.model)
        # The job identifier is stable across an expired worker lease being
        # redelivered. Provider gateways must use it to deduplicate a request,
        # so a worker crash cannot create a second billable inference.
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": request.job_id,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        command = Request(
            self._endpoint,
            data=request.model_dump_json().encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(command, timeout=self._timeout) as response:  # noqa: S310
                payload = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AutoAnnotationProviderUnavailable(
                "the configured automatic annotation provider did not return a valid result"
            ) from exc
        return AutoAnnotationProviderResult.model_validate(payload)

    def _require_model(self, model: str) -> None:
        if model not in self._models:
            raise AutoAnnotationProviderUnavailable("the selected provider model is unavailable")


class DeterministicAutoAnnotationProvider:
    """Deterministic provider for orchestration tests; it is never auto-composed in production."""

    name = "deterministic-fake"
    models = ("fake-v1",)

    def __init__(self, result: AutoAnnotationProviderResult) -> None:
        self.result = result
        self.requests: list[AutoAnnotationProviderRequest] = []

    def estimate_cost_micros(
        self,
        *,
        model: str,
        selection: AutoAnnotationInputSelection,
        base_step_count: int,
    ) -> int:
        del selection, base_step_count
        if model not in self.models:
            raise AutoAnnotationProviderUnavailable("fake model unavailable")
        return self.result.usage.cost_micros

    def annotate(self, request: AutoAnnotationProviderRequest) -> AutoAnnotationProviderResult:
        self.requests.append(request)
        return self.result


class AutoAnnotationProviderUnavailable(RuntimeError):
    pass


class AutoAnnotationOutboxHandler:
    """Run one durable job outside the API process through the core outbox."""

    EVENT_TYPE = "annotation.auto_annotation.requested.v1"

    def __init__(self, jobs: AutoAnnotationJobService) -> None:
        self._jobs = jobs

    def __call__(self, event: DomainEventEnvelope) -> None:
        if event.event_type != self.EVENT_TYPE or event.region_code is None:
            raise ValueError("automatic annotation outbox event has an invalid scope")
        task_id = _required_outbox_payload(event, "task_id")
        job_id = _required_outbox_payload(event, "job_id")
        if event.aggregate_id != job_id:
            raise ValueError("automatic annotation outbox aggregate does not match job")
        worker = AuthContext.service(
            subject_id="auto-annotation-dispatcher",
            roles={Role.ANNOTATOR},
            project_ids={event.project_id},
            region_codes={event.region_code},
        )
        self._jobs.run(
            auth=worker,
            task_id=task_id,
            job_id=job_id,
            request_id=event.trace_id or event.event_id,
        )


class AutoAnnotationJobRepository(Protocol):
    def create(
        self,
        job: AutoAnnotationJob,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        max_concurrent_jobs: int,
        max_jobs_per_hour: int,
        daily_cost_limit_micros: int,
        execution_event: DomainEventEnvelope,
    ) -> tuple[AutoAnnotationJob, bool]:
        """Atomically reserve quota and return ``(job, was_created)``."""
        ...

    def get(
        self,
        *,
        project_id: str,
        region_code: str,
        job_id: str,
    ) -> AutoAnnotationJob | None: ...

    def save(
        self,
        job: AutoAnnotationJob,
        *,
        expected_statuses: frozenset[str],
    ) -> bool: ...

    def quota_usage(
        self,
        *,
        project_id: str,
        region_code: str,
        now: datetime,
    ) -> tuple[int, int, int]:
        """Return active jobs, jobs in the trailing hour, and today's cost micros."""
        ...

    def requeue(
        self,
        job: AutoAnnotationJob,
        *,
        expected_statuses: frozenset[str],
        execution_event: DomainEventEnvelope,
    ) -> bool: ...

    def append_audit(
        self,
        *,
        job: AutoAnnotationJob,
        actor_id: str,
        request_id: str,
        action: str,
        details: Mapping[str, object],
        occurred_at: datetime,
    ) -> None: ...


class AutoAnnotationSamplingRepository(Protocol):
    def get_for_task(self, task: AnnotationTask) -> AutoAnnotationSamplingReference | None: ...


class InMemoryAutoAnnotationSamplingRepository:
    def __init__(
        self, references: Mapping[str, AutoAnnotationSamplingReference] | None = None
    ) -> None:
        self.references = dict(references or {})

    def get_for_task(self, task: AnnotationTask) -> AutoAnnotationSamplingReference | None:
        return self.references.get(task.task_id)


class InMemoryAutoAnnotationJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, AutoAnnotationJob] = {}
        self.receipts: dict[tuple[str, str, str, str], tuple[str, str]] = {}
        self.audit_events: list[dict[str, object]] = []
        self.execution_events: list[DomainEventEnvelope] = []
        self._lock = RLock()

    def create(
        self,
        job: AutoAnnotationJob,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        max_concurrent_jobs: int,
        max_jobs_per_hour: int,
        daily_cost_limit_micros: int,
        execution_event: DomainEventEnvelope,
    ) -> tuple[AutoAnnotationJob, bool]:
        key = (job.project_id, job.region_code, job.task_id, idempotency_key)
        with self._lock:
            existing = self.receipts.get(key)
            if existing is not None:
                fingerprint, job_id = existing
                if fingerprint != request_fingerprint:
                    raise _idempotency_conflict()
                return self.jobs[job_id], False
            active, hourly, daily = self._quota_usage_locked(
                project_id=job.project_id,
                region_code=job.region_code,
                now=job.created_at,
            )
            _enforce_limits(
                active=active,
                hourly=hourly,
                reserved_daily_cost_micros=daily,
                estimated_cost_micros=job.estimated_cost_micros,
                max_concurrent_jobs=max_concurrent_jobs,
                max_jobs_per_hour=max_jobs_per_hour,
                daily_cost_limit_micros=daily_cost_limit_micros,
            )
            self.jobs[job.job_id] = job
            self.receipts[key] = (request_fingerprint, job.job_id)
            self.execution_events.append(execution_event)
            return job, True

    def get(
        self,
        *,
        project_id: str,
        region_code: str,
        job_id: str,
    ) -> AutoAnnotationJob | None:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or (job.project_id, job.region_code) != (project_id, region_code):
                return None
            return job

    def save(
        self,
        job: AutoAnnotationJob,
        *,
        expected_statuses: frozenset[str],
    ) -> bool:
        with self._lock:
            current = self.jobs.get(job.job_id)
            if current is None or current.status not in expected_statuses:
                return False
            self.jobs[job.job_id] = job
            return True

    def quota_usage(
        self,
        *,
        project_id: str,
        region_code: str,
        now: datetime,
    ) -> tuple[int, int, int]:
        with self._lock:
            return self._quota_usage_locked(
                project_id=project_id,
                region_code=region_code,
                now=now,
            )

    def _quota_usage_locked(
        self,
        *,
        project_id: str,
        region_code: str,
        now: datetime,
    ) -> tuple[int, int, int]:
        scoped = tuple(
            job
            for job in self.jobs.values()
            if (job.project_id, job.region_code) == (project_id, region_code)
        )
        active = sum(job.status in {"QUEUED", "RUNNING"} for job in scoped)
        hourly = sum(job.created_at >= now - timedelta(hours=1) for job in scoped)
        daily = sum(
            job.usage.cost_micros if job.usage is not None else job.estimated_cost_micros
            for job in scoped
            if job.created_at.date() == now.date()
        )
        return active, hourly, daily

    def requeue(
        self,
        job: AutoAnnotationJob,
        *,
        expected_statuses: frozenset[str],
        execution_event: DomainEventEnvelope,
    ) -> bool:
        with self._lock:
            current = self.jobs.get(job.job_id)
            if current is None or current.status not in expected_statuses:
                return False
            self.jobs[job.job_id] = job
            self.execution_events.append(execution_event)
            return True

    def append_audit(self, **event: Any) -> None:
        with self._lock:
            self.audit_events.append(dict(event))


class AutoAnnotationJobService:
    def __init__(
        self,
        annotation: AnnotationService,
        repository: AutoAnnotationJobRepository,
        providers: Sequence[AutoAnnotationProvider],
        *,
        max_concurrent_jobs_per_project: int = 4,
        max_jobs_per_hour: int = 60,
        daily_cost_limit_micros: int = 5_000_000,
        sampling_repository: AutoAnnotationSamplingRepository | None = None,
        require_sampling_manifest: bool = False,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._annotation = annotation
        self._repository = repository
        self._providers = {provider.name: provider for provider in providers}
        if len(self._providers) != len(providers):
            raise ValueError("automatic annotation provider names must be unique")
        self._max_concurrent = max_concurrent_jobs_per_project
        self._max_hourly = max_jobs_per_hour
        self._daily_cost = daily_cost_limit_micros
        self._sampling = sampling_repository
        self._require_sampling = require_sampling_manifest
        self._clock = clock

    def capability(self) -> AutoAnnotationCapability:
        providers = tuple(
            AutoAnnotationProviderDescriptor(provider=name, models=provider.models)
            for name, provider in sorted(self._providers.items())
        )
        return AutoAnnotationCapability(
            enabled=bool(providers),
            code=None if providers else "PROVIDER_UNAVAILABLE",
            providers=providers,
            max_concurrent_jobs_per_project=self._max_concurrent,
            max_jobs_per_hour=self._max_hourly,
            daily_cost_limit_micros=self._daily_cost,
        )

    def create(
        self,
        *,
        auth: AuthContext,
        task_id: str,
        source_revision: int,
        provider_name: str,
        model: str,
        input_selection: AutoAnnotationInputSelection,
        idempotency_key: str,
        request_id: str,
    ) -> AutoAnnotationJob:
        task = self._annotation.read_task(task_id, auth)
        self._authorize_write(auth, task)
        self._annotation.get_revision(task_id, source_revision, auth)
        provider = self._provider(provider_name, model)
        base_step_count = task.base_step_count
        if base_step_count is None:
            raise problem(
                status=409,
                code="AUTO_ANNOTATION_INPUT_UNAVAILABLE",
                title="Automatic annotation input is unavailable",
                detail="The annotation task has no immutable step-count snapshot.",
            )
        end_step = input_selection.end_step or base_step_count
        if end_step > base_step_count:
            raise problem(
                status=422,
                code="AUTO_ANNOTATION_INPUT_RANGE_INVALID",
                title="Automatic annotation input range is invalid",
                detail="The selected step range must fit the task's immutable Lance snapshot.",
            )
        normalized_selection = input_selection.model_copy(update={"end_step": end_step})
        sampling_reference = (
            None if self._sampling is None else self._sampling.get_for_task(task)
        )
        if self._require_sampling and sampling_reference is None:
            raise problem(
                status=409,
                code="AUTO_ANNOTATION_SAMPLING_UNAVAILABLE",
                title="Automatic annotation sampling is unavailable",
                detail=(
                    "This task has no verified ingest FrameSelectionManifest; "
                    "full-rate inference is disabled."
                ),
            )
        estimated = provider.estimate_cost_micros(
            model=model,
            selection=normalized_selection,
            base_step_count=base_step_count,
        )
        now = _aware_now(self._clock)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "task_id": task_id,
                    "source_revision": source_revision,
                    "provider": provider_name,
                    "model": model,
                    "selection": normalized_selection.model_dump(mode="json"),
                    "sampling_reference": (
                        None
                        if sampling_reference is None
                        else sampling_reference.model_dump(mode="json")
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        candidate = AutoAnnotationJob(
            job_id=str(uuid4()),
            project_id=task.project_id,
            region_code=task.region_code or "",
            task_id=task_id,
            source_revision=source_revision,
            provider=provider_name,
            model=model,
            input_selection=normalized_selection,
            sampling_reference=sampling_reference,
            status="QUEUED",
            progress_percent=0,
            estimated_cost_micros=estimated,
            created_by=auth.subject_id,
            created_at=now,
            updated_at=now,
        )
        execution_event = DomainEventEnvelope(
            event_type=AutoAnnotationOutboxHandler.EVENT_TYPE,
            aggregate_type="AUTO_ANNOTATION_JOB",
            aggregate_id=candidate.job_id,
            project_id=candidate.project_id,
            region_code=candidate.region_code,
            occurred_at=now,
            trace_id=request_id,
            payload={"task_id": candidate.task_id, "job_id": candidate.job_id},
        )
        job, was_created = self._repository.create(
            candidate,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            max_concurrent_jobs=self._max_concurrent,
            max_jobs_per_hour=self._max_hourly,
            daily_cost_limit_micros=self._daily_cost,
            execution_event=execution_event,
        )
        if was_created:
            self._audit(job, auth.subject_id, request_id, "annotation.auto_job.queued", {}, now)
        return job

    def get(self, *, auth: AuthContext, task_id: str, job_id: str) -> AutoAnnotationJob:
        task = self._annotation.read_task(task_id, auth)
        job = self._repository.get(
            project_id=task.project_id,
            region_code=task.region_code or "",
            job_id=job_id,
        )
        if job is None or job.task_id != task_id:
            raise _not_found()
        return job

    def run(
        self,
        *,
        auth: AuthContext,
        task_id: str,
        job_id: str,
        request_id: str,
    ) -> None:
        job = self.get(auth=auth, task_id=task_id, job_id=job_id)
        # Core outbox delivery is at-least-once: a worker may die after the
        # job reached RUNNING but before it marked the envelope dispatched.
        # Re-entering a RUNNING job is safe because the production provider
        # request carries its stable job id as Idempotency-Key.
        if job.status not in {"QUEUED", "RUNNING"}:
            return
        task = self._annotation.read_task(task_id, auth)
        if job.status == "QUEUED":
            running = job.model_copy(
                update={
                    "status": "RUNNING",
                    "progress_percent": 10,
                    "updated_at": _aware_now(self._clock),
                }
            )
            if not self._repository.save(running, expected_statuses=frozenset({"QUEUED"})):
                return
        else:
            running = job
        try:
            provider = self._provider(job.provider, job.model)
            base_step_count = task.base_step_count
            if base_step_count is None:
                raise RuntimeError(
                    "automatic annotation task lost its immutable step-count snapshot"
                )
            result = provider.annotate(
                AutoAnnotationProviderRequest(
                    job_id=job_id,
                    project_id=task.project_id,
                    region_code=task.region_code or "",
                    task_id=task.task_id,
                    dataset_id=task.dataset_id,
                    dataset_version=task.dataset_version,
                    rollout_id=task.rollout_id,
                    source_revision=job.source_revision,
                    base_lance_version=task.base_lance_version,
                    base_step_count=base_step_count,
                    tag_schema_id=task.tag_schema_id,
                    tag_schema_version=task.tag_schema_version,
                    model=job.model,
                    input_selection=job.input_selection,
                    sampling_reference=job.sampling_reference,
                )
            )
            current = self._repository.get(
                project_id=job.project_id,
                region_code=job.region_code,
                job_id=job_id,
            )
            if current is None or current.status == "CANCELLED":
                return
            completed = running.model_copy(
                update={
                    "status": "SUCCEEDED",
                    "progress_percent": 100,
                    "tags": result.tags,
                    "operations": result.operations,
                    "usage": result.usage,
                    "updated_at": _aware_now(self._clock),
                }
            )
            if self._repository.save(completed, expected_statuses=frozenset({"RUNNING"})):
                self._audit(
                    completed,
                    "system",
                    request_id,
                    "annotation.auto_job.completed",
                    {"cost_micros": result.usage.cost_micros},
                    completed.updated_at,
                )
        except Exception as exc:
            code = (
                "AUTO_ANNOTATION_PROVIDER_UNAVAILABLE"
                if isinstance(exc, AutoAnnotationProviderUnavailable)
                else "AUTO_ANNOTATION_FAILED"
            )
            failed = running.model_copy(
                update={
                    "status": "FAILED",
                    "error_code": code,
                    "error_message": (
                        str(exc)[:512]
                        if isinstance(exc, AutoAnnotationProviderUnavailable)
                        else "Automatic annotation failed."
                    ),
                    "updated_at": _aware_now(self._clock),
                }
            )
            self._repository.save(failed, expected_statuses=frozenset({"RUNNING"}))

    def cancel(
        self,
        *,
        auth: AuthContext,
        task_id: str,
        job_id: str,
        request_id: str,
    ) -> AutoAnnotationJob:
        job = self.get(auth=auth, task_id=task_id, job_id=job_id)
        self._authorize_write(auth, self._annotation.read_task(task_id, auth))
        if job.status not in {"QUEUED", "RUNNING"}:
            raise _state_conflict("AUTO_ANNOTATION_CANCEL_NOT_ALLOWED")
        now = _aware_now(self._clock)
        cancelled = job.model_copy(update={"status": "CANCELLED", "updated_at": now})
        if not self._repository.save(cancelled, expected_statuses=frozenset({"QUEUED", "RUNNING"})):
            raise _state_conflict("AUTO_ANNOTATION_STATE_CHANGED")
        self._audit(
            cancelled, auth.subject_id, request_id, "annotation.auto_job.cancelled", {}, now
        )
        return cancelled

    def retry(
        self,
        *,
        auth: AuthContext,
        task_id: str,
        job_id: str,
        request_id: str,
    ) -> AutoAnnotationJob:
        job = self.get(auth=auth, task_id=task_id, job_id=job_id)
        self._authorize_write(auth, self._annotation.read_task(task_id, auth))
        if job.status not in {"FAILED", "CANCELLED"}:
            raise _state_conflict("AUTO_ANNOTATION_RETRY_NOT_ALLOWED")
        now = _aware_now(self._clock)
        queued = job.model_copy(
            update={
                "status": "QUEUED",
                "progress_percent": 0,
                "error_code": None,
                "error_message": None,
                "updated_at": now,
            }
        )
        execution_event = DomainEventEnvelope(
            event_type=AutoAnnotationOutboxHandler.EVENT_TYPE,
            aggregate_type="AUTO_ANNOTATION_JOB",
            aggregate_id=queued.job_id,
            project_id=queued.project_id,
            region_code=queued.region_code,
            occurred_at=now,
            trace_id=request_id,
            payload={"task_id": queued.task_id, "job_id": queued.job_id},
        )
        if not self._repository.requeue(
            queued,
            expected_statuses=frozenset({"FAILED", "CANCELLED"}),
            execution_event=execution_event,
        ):
            raise _state_conflict("AUTO_ANNOTATION_STATE_CHANGED")
        self._audit(queued, auth.subject_id, request_id, "annotation.auto_job.retried", {}, now)
        return queued

    def apply(
        self,
        *,
        auth: AuthContext,
        task_id: str,
        job_id: str,
        expected_revision: int,
        if_match: str,
        tags: Sequence[AnnotationTag] | None,
        operations: Sequence[AnnotationOperation] | None,
        request_id: str,
    ) -> AnnotationRevision:
        job = self.get(auth=auth, task_id=task_id, job_id=job_id)
        task = self._annotation.read_task(task_id, auth)
        self._authorize_write(auth, task)
        if job.status != "SUCCEEDED" or job.tags is None or job.operations is None:
            raise _state_conflict("AUTO_ANNOTATION_APPLY_NOT_ALLOWED")
        if expected_revision != job.source_revision or task.current_revision != job.source_revision:
            raise problem(
                status=409,
                code="AUTO_ANNOTATION_SOURCE_REVISION_CHANGED",
                title="Automatic annotation source revision changed",
                detail="Create a new job from the current immutable annotation revision.",
            )
        revision = self._annotation.save_draft(
            task_id,
            auth,
            tuple(job.operations if operations is None else operations),
            tags=tuple(job.tags if tags is None else tags),
            expected_revision=expected_revision,
            if_match=if_match,
            client_mutation_id=f"auto-annotation:{job_id}:apply",
        )
        now = _aware_now(self._clock)
        applied = job.model_copy(
            update={"status": "APPLIED", "applied_revision": revision.revision, "updated_at": now}
        )
        if not self._repository.save(applied, expected_statuses=frozenset({"SUCCEEDED"})):
            raise _state_conflict("AUTO_ANNOTATION_STATE_CHANGED")
        self._audit(
            applied,
            auth.subject_id,
            request_id,
            "annotation.auto_job.applied",
            {"applied_revision": revision.revision},
            now,
        )
        return revision

    def _provider(self, name: str, model: str) -> AutoAnnotationProvider:
        provider = self._providers.get(name)
        if provider is None or model not in provider.models:
            raise problem(
                status=503,
                code="AUTO_ANNOTATION_PROVIDER_UNAVAILABLE",
                title="Automatic annotation provider unavailable",
                detail="The selected provider or model is not configured.",
            )
        return provider

    @staticmethod
    def _authorize_write(auth: AuthContext, task: AnnotationTask) -> None:
        auth.require_capability("annotation.write", task.project_id)
        select_request_scope(task.project_id, task.region_code)

    def _audit(
        self,
        job: AutoAnnotationJob,
        actor_id: str,
        request_id: str,
        action: str,
        details: Mapping[str, object],
        occurred_at: datetime,
    ) -> None:
        self._repository.append_audit(
            job=job,
            actor_id=actor_id,
            request_id=request_id,
            action=action,
            details=details,
            occurred_at=occurred_at,
        )


def _aware_now(clock: Clock) -> datetime:
    now = clock()
    if now.tzinfo is None:
        raise RuntimeError("automatic annotation clock must be timezone-aware")
    return now.astimezone(timezone.utc)


def _json_items(value: object) -> Sequence[object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if isinstance(decoded, (str, bytes)) or not isinstance(decoded, Sequence):
        raise RuntimeError("automatic annotation result JSON must be an array")
    return decoded


def _required_outbox_payload(event: DomainEventEnvelope, name: str) -> str:
    value = event.payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"automatic annotation outbox event is missing {name}")
    return value


def _idempotency_conflict() -> Exception:
    return problem(
        status=409,
        code="IDEMPOTENCY_KEY_REUSED",
        title="Idempotency key reused",
        detail="The Idempotency-Key was used for a different automatic annotation request.",
    )


def _not_found() -> Exception:
    return problem(
        status=404,
        code="AUTO_ANNOTATION_JOB_NOT_FOUND",
        title="Automatic annotation job not found",
        detail="The job is not visible for this annotation task and scope.",
    )


def _rate_limited(code: str, detail: str) -> Exception:
    return problem(
        status=429,
        code=code,
        title="Automatic annotation limit reached",
        detail=detail,
    )


def _enforce_limits(
    *,
    active: int,
    hourly: int,
    reserved_daily_cost_micros: int,
    estimated_cost_micros: int,
    max_concurrent_jobs: int,
    max_jobs_per_hour: int,
    daily_cost_limit_micros: int,
) -> None:
    if active >= max_concurrent_jobs:
        raise _rate_limited(
            "AUTO_ANNOTATION_CONCURRENCY_LIMIT",
            "Too many jobs are active.",
        )
    if hourly >= max_jobs_per_hour:
        raise _rate_limited(
            "AUTO_ANNOTATION_RATE_LIMIT",
            "The hourly job limit was reached.",
        )
    if reserved_daily_cost_micros + estimated_cost_micros > daily_cost_limit_micros:
        raise _rate_limited(
            "AUTO_ANNOTATION_BUDGET_EXCEEDED",
            "The daily cost limit was reached.",
        )


def _state_conflict(code: str) -> Exception:
    return problem(
        status=409,
        code=code,
        title="Automatic annotation job state changed",
        detail="Refresh the job before trying this action.",
    )


class PostgresAutoAnnotationSamplingRepository:
    """Tenant-scoped lookup for the immutable ingest sampling reference."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._factory = connection_factory

    def get_for_task(self, task: AnnotationTask) -> AutoAnnotationSamplingReference | None:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT source_sha256, object_key, content_sha256, size_bytes,
                       sampling_version, camera_set, source_frame_count,
                       selected_group_count
                FROM annotation.frame_selection_manifests
                WHERE task_id = %s AND project_id = %s AND region_code = %s
                """,
                (task.task_id, task.project_id, task.region_code),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            if isinstance(raw, Mapping):
                row = raw
            else:
                names = tuple(str(column[0]) for column in cursor.description)
                row = dict(zip(names, cast(Sequence[object], raw), strict=True))
            camera_set = row["camera_set"]
            if isinstance(camera_set, str):
                camera_set = json.loads(camera_set)
            return AutoAnnotationSamplingReference(
                source_sha256=str(row["source_sha256"]),
                object_key=str(row["object_key"]),
                content_sha256=str(row["content_sha256"]),
                size_bytes=int(str(row["size_bytes"])),
                sampling_version=str(row["sampling_version"]),
                camera_set=tuple(cast(Sequence[str], camera_set)),
                source_frame_count=int(str(row["source_frame_count"])),
                selected_group_count=int(str(row["selected_group_count"])),
            )
        finally:
            cursor.close()
            connection.close()


class PostgresAutoAnnotationJobRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._factory = connection_factory

    @staticmethod
    def _row(cursor: Any, raw: object) -> dict[str, object]:
        if isinstance(raw, Mapping):
            return {str(key): value for key, value in raw.items()}
        names = tuple(str(column[0]) for column in cursor.description)
        return dict(zip(names, cast(Sequence[object], raw), strict=True))

    @staticmethod
    def _job(row: Mapping[str, object]) -> AutoAnnotationJob:
        usage = None
        if row.get("actual_cost_micros") is not None:
            usage = AutoAnnotationUsage(
                input_units=int(str(row["input_units"])),
                output_units=int(str(row["output_units"])),
                cost_micros=int(str(row["actual_cost_micros"])),
            )
        return AutoAnnotationJob(
            job_id=str(row["job_id"]),
            project_id=str(row["project_id"]),
            region_code=str(row["region_code"]),
            task_id=str(row["task_id"]),
            source_revision=int(str(row["source_revision"])),
            provider=str(row["provider"]),
            model=str(row["model_name"]),
            input_selection=AutoAnnotationInputSelection.model_validate(row["input_selection"]),
            sampling_reference=(
                None
                if row.get("sampling_reference") is None
                else AutoAnnotationSamplingReference.model_validate(row["sampling_reference"])
            ),
            status=cast(Any, str(row["status"])),
            progress_percent=int(str(row["progress_percent"])),
            estimated_cost_micros=int(str(row["estimated_cost_micros"])),
            usage=usage,
            tags=(
                None
                if row.get("result_tags") is None
                else tuple(
                    AnnotationTag.model_validate(item) for item in _json_items(row["result_tags"])
                )
            ),
            operations=(
                None
                if row.get("result_operations") is None
                else tuple(
                    AnnotationOperation.model_validate(item)
                    for item in _json_items(row["result_operations"])
                )
            ),
            applied_revision=(
                None if row.get("applied_revision") is None else int(str(row["applied_revision"]))
            ),
            error_code=None if row.get("error_code") is None else str(row["error_code"]),
            error_message=(None if row.get("error_message") is None else str(row["error_message"])),
            created_by=str(row["created_by"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )

    def create(
        self,
        job: AutoAnnotationJob,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        max_concurrent_jobs: int,
        max_jobs_per_hour: int,
        daily_cost_limit_micros: int,
        execution_event: DomainEventEnvelope,
    ) -> tuple[AutoAnnotationJob, bool]:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            # Every creator for the same project/region serializes its
            # idempotency lookup, quota reservation and insert in one DB
            # transaction. This prevents parallel API processes from each
            # observing spare quota and collectively exceeding the limit.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                (job.project_id, job.region_code),
            )
            cursor.execute(
                """SELECT * FROM annotation.auto_annotation_jobs
                    WHERE project_id=%s AND region_code=%s AND task_id=%s
                      AND idempotency_key=%s""",
                (job.project_id, job.region_code, job.task_id, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is not None:
                row = self._row(cursor, existing)
                if str(row["request_fingerprint"]) != request_fingerprint:
                    raise _idempotency_conflict()
                connection.commit()
                return self._job(row), False

            active, hourly, daily = self._quota_usage(
                cursor,
                project_id=job.project_id,
                region_code=job.region_code,
                now=job.created_at,
            )
            _enforce_limits(
                active=active,
                hourly=hourly,
                reserved_daily_cost_micros=daily,
                estimated_cost_micros=job.estimated_cost_micros,
                max_concurrent_jobs=max_concurrent_jobs,
                max_jobs_per_hour=max_jobs_per_hour,
                daily_cost_limit_micros=daily_cost_limit_micros,
            )
            cursor.execute(
                """INSERT INTO annotation.auto_annotation_jobs (
                       job_id, project_id, region_code, task_id, source_revision,
                       provider, model_name, input_selection, sampling_reference, idempotency_key,
                       request_fingerprint, status, progress_percent,
                       estimated_cost_micros, created_by, created_at, updated_at
                   ) VALUES (
                       %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,
                       'QUEUED',0,%s,%s,%s,%s
                   )
                   RETURNING *""",
                (
                    job.job_id,
                    job.project_id,
                    job.region_code,
                    job.task_id,
                    job.source_revision,
                    job.provider,
                    job.model,
                    job.input_selection.model_dump_json(),
                    (
                        None
                        if job.sampling_reference is None
                        else job.sampling_reference.model_dump_json()
                    ),
                    idempotency_key,
                    request_fingerprint,
                    job.estimated_cost_micros,
                    job.created_by,
                    job.created_at,
                    job.updated_at,
                ),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise RuntimeError("automatic annotation insert returned no row")
            row = self._row(cursor, raw)
            self._insert_outbox(cursor, execution_event)
            connection.commit()
            return self._job(row), True
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get(
        self,
        *,
        project_id: str,
        region_code: str,
        job_id: str,
    ) -> AutoAnnotationJob | None:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT * FROM annotation.auto_annotation_jobs
                    WHERE job_id=%s AND project_id=%s AND region_code=%s""",
                (job_id, project_id, region_code),
            )
            raw = cursor.fetchone()
            return None if raw is None else self._job(self._row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def save(
        self,
        job: AutoAnnotationJob,
        *,
        expected_statuses: frozenset[str],
    ) -> bool:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE annotation.auto_annotation_jobs
                      SET status=%s, progress_percent=%s,
                          result_tags=%s::jsonb, result_operations=%s::jsonb,
                          actual_cost_micros=%s, input_units=%s, output_units=%s,
                          applied_revision=%s, error_code=%s, error_message=%s,
                          updated_at=%s
                    WHERE job_id=%s AND project_id=%s AND region_code=%s
                      AND status = ANY(%s)""",
                (
                    job.status,
                    job.progress_percent,
                    None
                    if job.tags is None
                    else json.dumps([item.model_dump(mode="json") for item in job.tags]),
                    None
                    if job.operations is None
                    else json.dumps([item.model_dump(mode="json") for item in job.operations]),
                    None if job.usage is None else job.usage.cost_micros,
                    None if job.usage is None else job.usage.input_units,
                    None if job.usage is None else job.usage.output_units,
                    job.applied_revision,
                    job.error_code,
                    job.error_message,
                    job.updated_at,
                    job.job_id,
                    job.project_id,
                    job.region_code,
                    list(expected_statuses),
                ),
            )
            changed = int(cursor.rowcount) == 1
            connection.commit()
            return changed
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def quota_usage(
        self,
        *,
        project_id: str,
        region_code: str,
        now: datetime,
    ) -> tuple[int, int, int]:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            return self._quota_usage(
                cursor,
                project_id=project_id,
                region_code=region_code,
                now=now,
            )
        finally:
            cursor.close()
            connection.close()

    def _quota_usage(
        self,
        cursor: Any,
        *,
        project_id: str,
        region_code: str,
        now: datetime,
    ) -> tuple[int, int, int]:
        cursor.execute(
            """SELECT
                   count(*) FILTER (WHERE status IN ('QUEUED','RUNNING')) AS active,
                   count(*) FILTER (WHERE created_at >= %s) AS hourly,
                   COALESCE(
                       sum(COALESCE(actual_cost_micros, estimated_cost_micros))
                           FILTER (WHERE created_at >= %s),
                       0
                   ) AS daily
                 FROM annotation.auto_annotation_jobs
                WHERE project_id=%s AND region_code=%s""",
            (
                now - timedelta(hours=1),
                now.replace(hour=0, minute=0, second=0, microsecond=0),
                project_id,
                region_code,
            ),
        )
        row = self._row(cursor, cursor.fetchone())
        return int(str(row["active"])), int(str(row["hourly"])), int(str(row["daily"]))

    @staticmethod
    def _insert_outbox(cursor: Any, event: DomainEventEnvelope) -> None:
        cursor.execute(
            """INSERT INTO core.outbox_events (
                   event_id, project_id, region_code, event_type, envelope, occurred_at
               ) VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
            (
                event.event_id,
                event.project_id,
                event.region_code,
                event.event_type,
                event.model_dump_json(exclude_none=True),
                event.occurred_at,
            ),
        )

    def requeue(
        self,
        job: AutoAnnotationJob,
        *,
        expected_statuses: frozenset[str],
        execution_event: DomainEventEnvelope,
    ) -> bool:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """UPDATE annotation.auto_annotation_jobs
                      SET status=%s, progress_percent=%s,
                          result_tags=%s::jsonb, result_operations=%s::jsonb,
                          actual_cost_micros=%s, input_units=%s, output_units=%s,
                          applied_revision=%s, error_code=%s, error_message=%s,
                          updated_at=%s
                    WHERE job_id=%s AND project_id=%s AND region_code=%s
                      AND status = ANY(%s)""",
                (
                    job.status,
                    job.progress_percent,
                    None,
                    None,
                    None,
                    None,
                    None,
                    job.applied_revision,
                    job.error_code,
                    job.error_message,
                    job.updated_at,
                    job.job_id,
                    job.project_id,
                    job.region_code,
                    list(expected_statuses),
                ),
            )
            changed = int(cursor.rowcount) == 1
            if changed:
                self._insert_outbox(cursor, execution_event)
            connection.commit()
            return changed
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def append_audit(
        self,
        *,
        job: AutoAnnotationJob,
        actor_id: str,
        request_id: str,
        action: str,
        details: Mapping[str, object],
        occurred_at: datetime,
    ) -> None:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """INSERT INTO core.audit_events (
                       audit_id, project_id, region_code, actor_id, action,
                       resource_type, resource_id, request_id, details, occurred_at
                   ) VALUES (%s,%s,%s,%s,%s,'AUTO_ANNOTATION_JOB',%s,%s,%s::jsonb,%s)""",
                (
                    str(uuid4()),
                    job.project_id,
                    job.region_code,
                    actor_id,
                    action,
                    job.job_id,
                    request_id,
                    json.dumps(dict(details), sort_keys=True),
                    occurred_at,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

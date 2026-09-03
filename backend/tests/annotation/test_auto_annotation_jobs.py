from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.annotation.auto_jobs import (
    AutoAnnotationInputSelection,
    AutoAnnotationJobService,
    AutoAnnotationOutboxHandler,
    AutoAnnotationProviderRequest,
    AutoAnnotationProviderResult,
    AutoAnnotationProviderUnavailable,
    AutoAnnotationUsage,
    DeterministicAutoAnnotationProvider,
    HttpAutoAnnotationProvider,
    InMemoryAutoAnnotationJobRepository,
    InMemoryAutoAnnotationSamplingRepository,
)
from hc_data_platform.annotation.models import (
    AnnotationOperation,
    AutoAnnotationSamplingReference,
    OperationKind,
)
from hc_data_platform.annotation.router import (
    get_annotation_auth,
    get_annotation_service,
    get_auto_annotation_job_service,
    router,
)
from hc_data_platform.annotation.service import InMemoryAnnotationService
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.auth import AuthContext

NOW = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)


def _auth(project_id: str = "project-a") -> AuthContext:
    return AuthContext(
        subject_id="annotator-a",
        project_ids=frozenset({project_id}),
        region_codes=frozenset({"cn-hz"}),
        capabilities=frozenset(
            {
                "annotation_task.read",
                "annotation_task.claim",
                "annotation_task.assign",
                "annotation.edit",
                "annotation.save",
                "annotation.submit",
            }
        ),
        scope_pairs=frozenset({(project_id, "cn-hz")}),
    )


def _fixture() -> tuple[
    AutoAnnotationJobService,
    InMemoryAnnotationService,
    InMemoryAutoAnnotationJobRepository,
    DeterministicAutoAnnotationProvider,
]:
    annotation = InMemoryAnnotationService(clock=lambda: NOW)
    annotation.create_task(
        task_id="task-a",
        project_id="project-a",
        region_code="cn-hz",
        dataset_id="dataset-a",
        dataset_version=3,
        rollout_id="rollout-a",
        base_step_count=100,
    )
    annotation.claim("task-a", _auth())
    result = AutoAnnotationProviderResult(
        operations=(
            AnnotationOperation(
                operation_id="auto-exclude-1",
                kind=OperationKind.EXCLUDE,
                start_step=10,
                end_step=20,
                reason="deterministic provider suggestion",
            ),
        ),
        usage=AutoAnnotationUsage(input_units=100, output_units=5, cost_micros=250),
    )
    provider = DeterministicAutoAnnotationProvider(result)
    repository = InMemoryAutoAnnotationJobRepository()
    service = AutoAnnotationJobService(
        annotation,
        repository,
        (provider,),
        clock=lambda: NOW,
    )
    return service, annotation, repository, provider


def test_job_runs_provider_and_human_explicitly_applies_suggestion() -> None:
    service, annotation, repository, provider = _fixture()
    auth = _auth()
    job = service.create(
        auth=auth,
        task_id="task-a",
        source_revision=0,
        provider_name="deterministic-fake",
        model="fake-v1",
        input_selection=AutoAnnotationInputSelection(start_step=0, end_step=100),
        idempotency_key="auto-once",
        request_id="request-create",
    )
    assert job.status == "QUEUED"

    service.run(
        auth=auth,
        task_id="task-a",
        job_id=job.job_id,
        request_id="request-run",
    )
    completed = service.get(auth=auth, task_id="task-a", job_id=job.job_id)
    assert completed.status == "SUCCEEDED"
    assert completed.usage is not None and completed.usage.cost_micros == 250
    assert len(provider.requests) == 1

    task = annotation.read_task("task-a", auth)
    revision = service.apply(
        auth=auth,
        task_id="task-a",
        job_id=job.job_id,
        expected_revision=0,
        if_match=task.etag,
        tags=None,
        operations=None,
        request_id="request-apply",
    )
    assert revision.revision == 1
    assert revision.operations[0].operation_id == "auto-exclude-1"
    applied = service.get(auth=auth, task_id="task-a", job_id=job.job_id)
    assert applied.status == "APPLIED"
    assert applied.applied_revision == 1
    assert repository.audit_events[-1]["action"] == "annotation.auto_job.applied"


def test_provider_receives_server_owned_ingest_sampling_reference() -> None:
    _, annotation, repository, provider = _fixture()
    sampling = AutoAnnotationSamplingReference(
        object_key="derived/frame-selections/project/source/adaptive-2fps-v1.json",
        content_sha256="a" * 64,
        size_bytes=2048,
        source_sha256="b" * 64,
        sampling_version="adaptive-2fps-v1",
        camera_set=("front", "left", "right", "wrist"),
        source_frame_count=7_200,
        selected_group_count=180,
    )
    service = AutoAnnotationJobService(
        annotation,
        repository,
        (provider,),
        sampling_repository=InMemoryAutoAnnotationSamplingRepository({"task-a": sampling}),
        require_sampling_manifest=True,
        clock=lambda: NOW,
    )
    job = service.create(
        auth=_auth(),
        task_id="task-a",
        source_revision=0,
        provider_name="deterministic-fake",
        model="fake-v1",
        input_selection=AutoAnnotationInputSelection(start_step=10, end_step=80),
        idempotency_key="sampled-once",
        request_id="sampled-once",
    )

    assert job.sampling_reference == sampling
    service.run(auth=_auth(), task_id="task-a", job_id=job.job_id, request_id="run")
    assert provider.requests[0].sampling_reference == sampling
    assert provider.requests[0].input_selection == AutoAnnotationInputSelection(
        start_step=10, end_step=80
    )


def test_production_policy_rejects_full_rate_fallback_without_sampling() -> None:
    _, annotation, repository, provider = _fixture()
    service = AutoAnnotationJobService(
        annotation,
        repository,
        (provider,),
        sampling_repository=InMemoryAutoAnnotationSamplingRepository(),
        require_sampling_manifest=True,
        clock=lambda: NOW,
    )

    with pytest.raises(ProblemException) as rejected:
        service.create(
            auth=_auth(),
            task_id="task-a",
            source_revision=0,
            provider_name="deterministic-fake",
            model="fake-v1",
            input_selection=AutoAnnotationInputSelection(),
            idempotency_key="no-full-rate-fallback",
            request_id="no-full-rate-fallback",
        )

    assert rejected.value.problem.code == "AUTO_ANNOTATION_SAMPLING_UNAVAILABLE"


def test_reclaimed_outbox_delivery_resumes_a_running_job() -> None:
    service, _, repository, provider = _fixture()
    auth = _auth()
    job = service.create(
        auth=auth,
        task_id="task-a",
        source_revision=0,
        provider_name="deterministic-fake",
        model="fake-v1",
        input_selection=AutoAnnotationInputSelection(),
        idempotency_key="resume-after-worker-crash",
        request_id="request-create",
    )
    crashed = job.model_copy(
        update={"status": "RUNNING", "progress_percent": 10, "updated_at": NOW}
    )
    assert repository.save(crashed, expected_statuses=frozenset({"QUEUED"}))

    # Simulate a core-outbox lease expiring after the original worker died.
    AutoAnnotationOutboxHandler(service)(repository.execution_events[0])

    resumed = service.get(auth=auth, task_id="task-a", job_id=job.job_id)
    assert resumed.status == "SUCCEEDED"
    assert len(provider.requests) == 1


def test_idempotency_quota_and_stale_source_revision_fail_closed() -> None:
    service, annotation, repository, _ = _fixture()
    auth = _auth()
    command = dict(
        auth=auth,
        task_id="task-a",
        source_revision=0,
        provider_name="deterministic-fake",
        model="fake-v1",
        input_selection=AutoAnnotationInputSelection(),
        idempotency_key="same-key",
        request_id="request-create",
    )
    first = service.create(**command)
    assert service.create(**command).job_id == first.job_id
    assert [event["action"] for event in repository.audit_events] == ["annotation.auto_job.queued"]
    with pytest.raises(ProblemException) as reused:
        service.create(
            **{
                **command,
                "input_selection": AutoAnnotationInputSelection(end_step=50),
            },
        )
    assert reused.value.problem.code == "IDEMPOTENCY_KEY_REUSED"

    service.run(auth=auth, task_id="task-a", job_id=first.job_id, request_id="run")
    task = annotation.read_task("task-a", auth)
    annotation.save_draft(
        "task-a",
        auth,
        (),
        expected_revision=0,
        if_match=task.etag,
        client_mutation_id="human-change",
    )
    with pytest.raises(ProblemException) as stale:
        service.apply(
            auth=auth,
            task_id="task-a",
            job_id=first.job_id,
            expected_revision=0,
            if_match=annotation.read_task("task-a", auth).etag,
            tags=None,
            operations=None,
            request_id="apply-stale",
        )
    assert stale.value.problem.code == "AUTO_ANNOTATION_SOURCE_REVISION_CHANGED"


def test_parallel_creates_atomically_reserve_project_quota() -> None:
    _, annotation, repository, provider = _fixture()
    service = AutoAnnotationJobService(
        annotation,
        repository,
        (provider,),
        max_concurrent_jobs_per_project=1,
        clock=lambda: NOW,
    )
    auth = _auth()

    def create(index: int) -> tuple[int, str]:
        try:
            job = service.create(
                auth=auth,
                task_id="task-a",
                source_revision=0,
                provider_name="deterministic-fake",
                model="fake-v1",
                input_selection=AutoAnnotationInputSelection(),
                idempotency_key=f"parallel-{index}",
                request_id=f"parallel-{index}",
            )
            return 202, job.job_id
        except ProblemException as exc:
            return exc.problem.status, exc.problem.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(create, range(2)))

    assert sorted(status for status, _ in outcomes) == [202, 429]
    assert any(value == "AUTO_ANNOTATION_CONCURRENCY_LIMIT" for _, value in outcomes)
    assert len(repository.jobs) == 1
    assert len(repository.execution_events) == 1


class _UnavailableProvider:
    name = "unavailable"
    models = ("vlm-v1",)

    def estimate_cost_micros(self, **_: object) -> int:
        return 10

    def annotate(self, request: AutoAnnotationProviderRequest) -> AutoAnnotationProviderResult:
        del request
        raise AutoAnnotationProviderUnavailable("provider maintenance")


def test_provider_failure_is_explicit_and_retryable_without_fake_result() -> None:
    _, annotation, repository, _ = _fixture()
    service = AutoAnnotationJobService(
        annotation,
        repository,
        (_UnavailableProvider(),),
        clock=lambda: NOW,
    )
    auth = _auth()
    job = service.create(
        auth=auth,
        task_id="task-a",
        source_revision=0,
        provider_name="unavailable",
        model="vlm-v1",
        input_selection=AutoAnnotationInputSelection(),
        idempotency_key="unavailable-once",
        request_id="create",
    )
    service.run(auth=auth, task_id="task-a", job_id=job.job_id, request_id="run")
    failed = service.get(auth=auth, task_id="task-a", job_id=job.job_id)
    assert failed.status == "FAILED"
    assert failed.error_code == "AUTO_ANNOTATION_PROVIDER_UNAVAILABLE"
    assert failed.tags is None and failed.operations is None
    assert (
        service.retry(
            auth=auth,
            task_id="task-a",
            job_id=job.job_id,
            request_id="retry",
        ).status
        == "QUEUED"
    )
    assert len(repository.execution_events) == 2
    assert [event["action"] for event in repository.audit_events] == [
        "annotation.auto_job.queued",
        "annotation.auto_job.retried",
    ]


class _ProviderGatewayHandler(BaseHTTPRequestHandler):
    received: list[tuple[dict[str, str], dict[str, object]]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        assert isinstance(payload, dict)
        self.received.append((dict(self.headers.items()), payload))
        if payload["model"] == "gateway-fail":
            body = b'{"code":"PROVIDER_MAINTENANCE"}'
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
        else:
            body = json.dumps(
                {
                    "tags": [],
                    "operations": [],
                    "usage": {
                        "input_units": 40,
                        "output_units": 0,
                        "cost_micros": 41,
                    },
                }
            ).encode()
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_production_http_provider_uses_authenticated_idempotent_wire_contract() -> None:
    _ProviderGatewayHandler.received.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1/annotate"
    provider = HttpAutoAnnotationProvider(
        name="gateway",
        endpoint=endpoint,
        models=("gateway-v1", "gateway-fail"),
        api_key="test-provider-token",
        timeout_seconds=2,
    )
    request = AutoAnnotationProviderRequest(
        job_id="job-http-1",
        project_id="project-a",
        region_code="cn-hz",
        task_id="task-a",
        dataset_id="dataset-a",
        dataset_version=3,
        rollout_id="rollout-a",
        source_revision=0,
        base_lance_version=1,
        base_step_count=100,
        tag_schema_id="schema-a",
        tag_schema_version=1,
        model="gateway-v1",
        input_selection=AutoAnnotationInputSelection(start_step=10, end_step=50),
    )
    try:
        result = provider.annotate(request)
        assert result.usage.cost_micros == 41
        headers, payload = _ProviderGatewayHandler.received[-1]
        assert headers["Idempotency-Key"] == "job-http-1"
        assert headers["Authorization"] == "Bearer test-provider-token"
        assert payload["source_revision"] == 0
        assert payload["input_selection"] == {
            "start_step": 10,
            "end_step": 50,
            "modalities": [],
        }

        with pytest.raises(AutoAnnotationProviderUnavailable):
            provider.annotate(request.model_copy(update={"model": "gateway-fail"}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_boundary_enqueues_durable_job_and_applies_reviewed_result() -> None:
    jobs, annotation, repository, _ = _fixture()
    auth = _auth()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_annotation_service] = lambda: annotation
    app.dependency_overrides[get_auto_annotation_job_service] = lambda: jobs
    app.dependency_overrides[get_annotation_auth] = lambda: auth

    @app.exception_handler(ProblemException)
    async def problem_handler(_: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.problem.status,
            content=exc.problem.model_dump(mode="json", exclude_none=True),
        )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/annotation-tasks/task-a/auto-annotation",
            headers={"Idempotency-Key": "browser-auto"},
            json={
                "revision": 0,
                "provider": "deterministic-fake",
                "model": "fake-v1",
                "input_selection": {"start_step": 0, "end_step": 100},
            },
        )
        assert created.status_code == 202
        assert created.headers["cache-control"] == "no-store"
        job_id = created.json()["job_id"]

        queued = client.get(f"/api/v1/annotation-tasks/task-a/auto-annotation-jobs/{job_id}")
        assert queued.status_code == 200
        assert queued.json()["status"] == "QUEUED"
        assert len(repository.execution_events) == 1
        AutoAnnotationOutboxHandler(jobs)(repository.execution_events[0])

        completed = client.get(f"/api/v1/annotation-tasks/task-a/auto-annotation-jobs/{job_id}")
        assert completed.status_code == 200
        assert completed.json()["status"] == "SUCCEEDED"
        task = annotation.read_task("task-a", auth)
        applied = client.post(
            f"/api/v1/annotation-tasks/task-a/auto-annotation-jobs/{job_id}:apply",
            headers={"If-Match": task.etag},
            json={"expected_revision": 0},
        )
        assert applied.status_code == 200
        assert applied.json()["revision"] == 1
        assert applied.headers["etag"] == annotation.read_task("task-a", auth).etag

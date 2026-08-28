from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

from hc_data_platform.workflow import router as workflow_router
from hc_data_platform.workflow.models import (
    JobStatus,
    QualityOutcome,
    parse_workflow_id,
    workflow_id,
)
from hc_data_platform.workflow.service import InMemoryWorkflowLauncher, TemporalWorkflowLauncher
from hc_data_platform.workflow.temporal_workflows import (
    ACTIVITY_RETRY_POLICY,
    COMMIT_ACTIVITY,
    LONG_ACTIVITY,
    STANDARD_ACTIVITY,
)
from hc_data_platform.workflow.worker import discover_temporal_registrations
from hc_data_platform.workflow.workflows import DatasetWriterWorkflow, IngestRolloutWorkflow


class Verifier:
    def verify(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {**raw, "verified": True}


class Quality:
    def __init__(self, outcome: QualityOutcome = QualityOutcome.PASS) -> None:
        self.outcome = outcome

    def evaluate(self, verified: dict[str, Any]) -> tuple[QualityOutcome, dict[str, bool]]:
        del verified
        return self.outcome, {"checked": True}


class Aligner:
    def align(self, verified: dict[str, Any]) -> dict[str, Any]:
        return {"fragment": verified["rollout_id"]}


class Committer:
    def __init__(self) -> None:
        self.calls = 0

    def commit(self, fragment: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"lance_version": self.calls, **fragment}


def test_ingest_workflow_pass_and_risk_are_distinct() -> None:
    commit = Committer()
    passed = IngestRolloutWorkflow(Verifier(), Quality(), Aligner(), commit).run(
        {"rollout_id": "r1"}
    )
    assert passed["training_eligible"] is True
    risky = IngestRolloutWorkflow(Verifier(), Quality(QualityOutcome.RISK), Aligner(), commit).run(
        {"rollout_id": "r2"}
    )
    assert risky["_job_status"] == JobStatus.QUALITY_RISK
    assert commit.calls == 1


def test_launcher_deduplicates_deterministic_workflow_id() -> None:
    launcher = InMemoryWorkflowLauncher()
    calls = 0

    def runner() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    identifier = workflow_id("ingest", "p1", "r1")
    first = launcher.start(
        workflow_id=identifier,
        job_type="ingest",
        project_id="p1",
        resource_id="r1",
        runner=runner,
    )
    second = launcher.start(
        workflow_id=identifier,
        job_type="ingest",
        project_id="p1",
        resource_id="r1",
        runner=runner,
    )
    assert first == second
    assert calls == 1


def test_production_runtime_defaults_job_api_to_temporal(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("HC_WORKFLOW_BACKEND", raising=False)
    monkeypatch.setenv("HC_RUNTIME_BACKEND", "production")
    workflow_router.get_settings.cache_clear()
    workflow_router.get_launcher.cache_clear()
    try:
        assert isinstance(workflow_router.get_launcher(), TemporalWorkflowLauncher)
    finally:
        workflow_router.get_launcher.cache_clear()
        workflow_router.get_settings.cache_clear()


def test_workflow_id_round_trips_reserved_project_and_resource_characters() -> None:
    identifier = workflow_id("preview", "project:cn/hz", "dataset:r1/front")

    assert identifier == "preview:v1:project%3Acn%2Fhz:dataset%3Ar1%2Ffront"
    assert parse_workflow_id(identifier) == (
        "preview",
        "v1",
        "project:cn/hz",
        "dataset:r1/front",
    )


def test_dataset_writer_serializes_and_deduplicates() -> None:
    committer = Committer()
    writer = DatasetWriterWorkflow(committer)

    def submit(_: int) -> dict[str, Any]:
        return writer.submit("r1:hash:v1", {"fragment": "r1"})

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(submit, range(100)))
    assert results == [results[0]] * 100
    assert committer.calls == 1


def test_in_memory_job_cancellation_wins_race_with_runner_completion() -> None:
    launcher = InMemoryWorkflowLauncher()
    started = Event()
    release = Event()

    def runner() -> dict[str, Any]:
        started.set()
        release.wait(timeout=5)
        return {"should_not_replace_cancelled_state": True}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            launcher.start,
            workflow_id=workflow_id("ingest", "p1", "cancel-me"),
            job_type="ingest",
            project_id="p1",
            resource_id="cancel-me",
            runner=runner,
        )
        assert started.wait(timeout=5)
        running = launcher.list(project_id="p1", status=JobStatus.RUNNING)
        assert running.total == 1
        cancelled = launcher.cancel(running.items[0].job_id)
        release.set()
        completed = future.result(timeout=5)

    assert cancelled.status is JobStatus.CANCELLED
    assert completed.status is JobStatus.CANCELLED
    assert completed.result is None


def test_worker_registers_every_workflow_and_activity_with_bounded_policies() -> None:
    workflows, activities = discover_temporal_registrations()

    assert {item.__name__ for item in workflows} == {
        "IngestRolloutWorkflow",
        "DatasetWriterWorkflow",
        "PublishDatasetWorkflow",
        "ExportWorkflow",
        "CatalogReconciliationWorkflow",
        "PublishReconciliationWorkflow",
        "AnnotationReviewPreparationWorkflow",
        "StorageLifecycleExecutionWorkflow",
    }
    assert {item.__name__ for item in activities} == {
        "verify_raw",
        "evaluate_quality",
        "align_fragment",
        "commit_fragment",
        "create_annotation_task",
        "publish_dataset",
        "preflight_export",
        "export_dataset",
        "verify_export_artifact",
        "reconcile_catalog",
        "reconcile_publication",
        "storage_apply_lifecycle_batch",
        "parse_manifest",
        "materialize_ingest_projection",
        "cleanup_ingest_projection",
        "persist_workflow_job",
    }
    media_workflows, media_activities = discover_temporal_registrations("media")
    assert {item.__name__ for item in media_workflows} == {"PreviewWorkflow"}
    assert {item.__name__ for item in media_activities} == {"create_preview"}
    assert ACTIVITY_RETRY_POLICY.maximum_attempts == 8
    assert ACTIVITY_RETRY_POLICY.backoff_coefficient == 2
    assert ACTIVITY_RETRY_POLICY.non_retryable_error_types is not None
    assert "VALIDATION_FAILED" in ACTIVITY_RETRY_POLICY.non_retryable_error_types
    for policy in (STANDARD_ACTIVITY, LONG_ACTIVITY, COMMIT_ACTIVITY):
        assert policy.start_to_close < policy.schedule_to_close
        assert policy.heartbeat < policy.start_to_close

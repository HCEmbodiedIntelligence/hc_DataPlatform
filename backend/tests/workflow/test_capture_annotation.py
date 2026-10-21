from dataclasses import replace

import pytest
from temporalio import workflow
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from hc_data_platform.annotation.automation import AutomaticAnnotationBlocked
from hc_data_platform.lance_catalog.service import InMemoryLanceCatalog
from hc_data_platform.quality.models import QualityStatus
from hc_data_platform.workflow.activities import ALL_ACTIVITIES, configure_activity_dependencies
from hc_data_platform.workflow.models import AutomaticAnnotationActivityInput
from hc_data_platform.workflow.temporal_workflows import (
    ALL_WORKFLOWS,
    IngestRolloutWorkflow,
    RetryEpisodeAnnotationWorkflow,
)
from tests.workflow.test_temporal_workflows import (
    StaticQuality,
    StaticVerifier,
    _dependencies,
    _ingest_input,
    _schema,
)


class AnnotationFailure:
    def __init__(self, missing):
        self.missing = missing

    def ensure_task(self, request):
        if self.missing:
            raise AutomaticAnnotationBlocked("no binding")
        raise ApplicationError(
            "injected annotation failure", type="ANNOTATION_FAILURE", non_retryable=True
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_committed_data_survives_annotation_and_annotation_only_retry(missing):
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(_schema())
    dependencies, writers = _dependencies(
        verifier=StaticVerifier(), quality=StaticQuality(QualityStatus.PASS), catalog=catalog
    )
    configure_activity_dependencies(
        replace(dependencies, annotation_tasks=AnnotationFailure(missing))
    )
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        Worker(
            env.client,
            task_queue="annotation-isolation",
            workflows=ALL_WORKFLOWS,
            activities=ALL_ACTIVITIES,
        ),
    ):
        handle = await env.client.start_workflow(
            IngestRolloutWorkflow.run,
            _ingest_input().model_copy(update={"media_task_queue": "annotation-isolation"}),
            id="annotation-isolation",
            task_queue="annotation-isolation",
        )
        job = await handle.result()
        assert job.status.value == "SUCCEEDED"
        assert job.result["data_readable"] is True
        assert job.result["training_eligible"] is False
        assert job.result["annotation_status"] == ("PENDING_CONFIGURATION" if missing else "FAILED")
        assert writers.calls == 1
        assert len(catalog.list_versions("d1", project_id="p1")) == 1
        configure_activity_dependencies(dependencies)
        retried = await env.client.execute_workflow(
            RetryEpisodeAnnotationWorkflow.run,
            AutomaticAnnotationActivityInput.model_validate(job.result["annotation_retry"]),
            id="annotation-only-retry",
            task_queue="annotation-isolation",
        )
        assert retried.result["annotation_status"] == "CREATED"
        assert writers.calls == 1
        assert len(catalog.list_versions("d1", project_id="p1")) == 1
        await Replayer(
            workflows=ALL_WORKFLOWS, data_converter=pydantic_data_converter
        ).replay_workflow(await handle.fetch_history())


@pytest.mark.asyncio
async def test_history_without_annotation_patch_replays(monkeypatch):
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(_schema())
    dependencies, _ = _dependencies(
        verifier=StaticVerifier(), quality=StaticQuality(QualityStatus.PASS), catalog=catalog
    )
    configure_activity_dependencies(dependencies)
    original = workflow.patched
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        with monkeypatch.context() as patch:
            patch.setattr(
                workflow,
                "patched",
                lambda name: (
                    False if name == "ingest-annotation-independent-v1" else original(name)
                ),
            )
            async with Worker(
                env.client,
                task_queue="legacy-annotation",
                workflows=ALL_WORKFLOWS,
                activities=ALL_ACTIVITIES,
            ):
                handle = await env.client.start_workflow(
                    IngestRolloutWorkflow.run,
                    _ingest_input().model_copy(update={"media_task_queue": "legacy-annotation"}),
                    id="legacy-annotation",
                    task_queue="legacy-annotation",
                )
                job = await handle.result()
                assert job.status.value == "SUCCEEDED"
                assert "annotation_status" not in job.result
                history = await handle.fetch_history()
        await Replayer(
            workflows=ALL_WORKFLOWS, data_converter=pydantic_data_converter
        ).replay_workflow(history)

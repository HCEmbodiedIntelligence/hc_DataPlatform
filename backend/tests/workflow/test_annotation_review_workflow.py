from __future__ import annotations

from datetime import datetime, timezone

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from hc_data_platform.annotation.models import (
    AnnotationRevision,
    AnnotationSubmission,
    AnnotationTag,
    ReviewCheckKind,
    TagNodeDefinition,
    TagSchemaDocument,
    TagSchemaStatus,
    TagSchemaVersion,
)
from hc_data_platform.annotation.validation import revision_content_hash, schema_content_hash
from hc_data_platform.workflow.models import (
    AnnotationReviewPreparationWorkflowInput,
    JobStatus,
    workflow_id,
)
from hc_data_platform.workflow.temporal_workflows import AnnotationReviewPreparationWorkflow


@pytest.mark.asyncio
async def test_annotation_review_preparation_executes_and_replays() -> None:
    timestamp = datetime(2026, 8, 17, tzinfo=timezone.utc)
    document = TagSchemaDocument(
        nodes=(TagNodeDefinition(tag_id="event", code="event", display_name="Event"),)
    )
    schema = TagSchemaVersion(
        schema_id="review-schema",
        project_id="project-a",
        name="Review schema",
        version=3,
        status=TagSchemaStatus.PUBLISHED,
        document=document,
        content_hash=schema_content_hash(document),
        created_by="publisher",
        created_at=timestamp,
        published_by="publisher",
        published_at=timestamp,
    )
    annotation = AnnotationTag(
        annotation_id="event-1",
        tag_id="event",
        path=("event",),
        start_step=4,
        end_step=9,
    )
    content_hash = revision_content_hash(
        base_lance_version=41,
        tag_schema_id=schema.schema_id,
        tag_schema_version=schema.version,
        tags=(annotation,),
        operations=(),
    )
    revision = AnnotationRevision(
        task_id="task-review",
        revision=1,
        parent_revision=0,
        author_id="alice",
        client_mutation_id="save-1",
        base_lance_version=41,
        tag_schema_id=schema.schema_id,
        tag_schema_version=schema.version,
        tags=(annotation,),
        operations=(),
        content_hash=content_hash,
        created_at=timestamp,
    )
    request = AnnotationReviewPreparationWorkflowInput(
        project_id="project-a",
        submitted_by="alice",
        base_step_count=100,
        tag_schema=schema,
        revision=revision,
        cumulative_operations=(),
    )

    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        async with Worker(
            environment.client,
            task_queue="annotation-review-tests",
            workflows=[AnnotationReviewPreparationWorkflow],
        ):
            handle = await environment.client.start_workflow(
                AnnotationReviewPreparationWorkflow.run,
                request,
                id=workflow_id("annotation-review-preparation", "project-a", "task-review/1"),
                task_queue="annotation-review-tests",
            )
            result = await handle.result()
            assert result.status is JobStatus.SUCCEEDED
            assert result.result is not None
            submission = AnnotationSubmission.model_validate(result.result["submission"])
            assert submission.revision_content_hash == content_hash
            assert submission.base_lance_version == 41
            assert (submission.tag_schema_id, submission.tag_schema_version) == (
                "review-schema",
                3,
            )
            assert {check.kind for check in submission.checks} == set(ReviewCheckKind)

            history = await handle.fetch_history()
            await Replayer(
                workflows=[AnnotationReviewPreparationWorkflow],
                data_converter=pydantic_data_converter,
            ).replay_workflow(history)
    finally:
        await environment.shutdown()

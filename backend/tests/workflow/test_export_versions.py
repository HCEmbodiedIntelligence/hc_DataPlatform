import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from hc_data_platform.publishing.memory import InMemoryArtifactSink
from hc_data_platform.publishing.ports import ExportSourcePort
from hc_data_platform.publishing.service import ExportCoordinator
from hc_data_platform.workflow.activities import (
    ALL_ACTIVITIES,
    ActivityDependencies,
    configure_activity_dependencies,
)
from hc_data_platform.workflow.models import (
    ExportActivityInput,
    ExportPreflightActivityInput,
    ExportWorkflowInput,
    JobStatus,
)
from hc_data_platform.workflow.temporal_workflows import ExportWorkflow
from tests.publishing.test_export_extension_api import MetadataExporter
from tests.publishing.test_export_job_api import _manifest


@pytest.mark.asyncio
async def test_history_captured_before_version_freezing_still_replays():
    # Executed against committed 76150bb5 before these model/workflow changes.
    history = WorkflowHistory.from_json(
        "export:v1:project-a:dataset-a%2Fv1%2Flance_snapshot%2Flegacy-attempt",
        Path(__file__).with_name("fixtures").joinpath("export-workflow-legacy-v1.json").read_text(),
    )
    await Replayer(
        workflows=[ExportWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)


@pytest.mark.parametrize(
    "model", [ExportWorkflowInput, ExportPreflightActivityInput, ExportActivityInput]
)
def test_unversioned_history_input_preserves_legacy_wire_shape(model):
    fields = {"manifest": _manifest().model_dump(mode="json"), "format": "lance_snapshot"}
    if model is not ExportPreflightActivityInput:
        fields["attempt_id"] = "legacy-attempt"
    if model is ExportWorkflowInput:
        fields["selection"] = None
    loaded = model.model_validate(fields)
    assert loaded.implementation_version is None
    assert json.loads(loaded.model_dump_json()) == fields


@pytest.mark.asyncio
@pytest.mark.parametrize("retained", [True, False])
async def test_frozen_export_version_executes_and_replays_after_default_upgrade(retained):
    original, upgraded = MetadataExporter("v1"), MetadataExporter("v2")
    source = Mock(spec=ExportSourcePort)
    source.read_steps.side_effect = AssertionError("This format requires only frozen metadata")
    coordinator = ExportCoordinator(
        source=source,
        sink=InMemoryArtifactSink(),
        exporters=[original, upgraded] if retained else [upgraded],
        defaults={"metadata_json": "v2"},
    )
    configure_activity_dependencies(ActivityDependencies(exporter=coordinator))
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        async with Worker(
            environment.client,
            task_queue="export-version-tests",
            workflows=[ExportWorkflow],
            activities=list(ALL_ACTIVITIES),
        ):
            handle = await environment.client.start_workflow(
                ExportWorkflow.run,
                ExportWorkflowInput(
                    manifest=_manifest(),
                    format="metadata_json",
                    attempt_id="frozen-attempt",
                    implementation_version="v1",
                ),
                id=f"frozen-export-{retained}",
                task_queue="export-version-tests",
            )
            result = await handle.result()
            assert result.result["export_implementation"] == {
                "key": "metadata_json",
                "version": "v1",
            }
            if retained:
                assert result.status is JobStatus.SUCCEEDED
                assert original.calls == 1
            else:
                assert result.status is JobStatus.TECHNICAL_FAILED
                assert result.error_code == "EXPORTER_UNAVAILABLE"
                assert original.calls == 0
            assert upgraded.calls == 0
            source.read_steps.assert_not_called()
            await Replayer(
                workflows=[ExportWorkflow], data_converter=pydantic_data_converter
            ).replay_workflow(await handle.fetch_history())
    finally:
        await environment.shutdown()
        configure_activity_dependencies(ActivityDependencies())

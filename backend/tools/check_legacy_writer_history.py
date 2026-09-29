"""Record an old writer's command history and replay it with the current writer.

Uses only the isolated Temporal namespace and an in-memory historical catalog.
No physical Lance files or production data are created. This validates workflow
compatibility, not complete ingestion or historical storage migration.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from hc_data_platform.lance_catalog.models import (
    AlignedFragmentManifestV1,
    DatasetSchemaSnapshot,
    StepRecord,
)
from hc_data_platform.lance_catalog.service import InMemoryLanceCatalog, compute_fragment_hash
from hc_data_platform.workflow.activities import (
    ActivityDependencies,
    commit_fragment,
    configure_activity_dependencies,
)
from hc_data_platform.workflow.models import (
    CatalogCommitActivityInput,
    CatalogFragmentPayloadV1,
    DatasetWriterWorkflowInput,
    JobStatus,
)
from hc_data_platform.workflow.names import COMMIT_FRAGMENT_ACTIVITY
from hc_data_platform.workflow.temporal_workflows import DatasetWriterWorkflow


async def main(args):
    assert os.environ["HC_TEMPORAL_NAMESPACE"] == "chayi-g1"
    source = Path(args.source)
    content = source.read_bytes()
    assert hashlib.sha256(content).hexdigest() == args.source_sha256
    # Import the exact prior workflow source, preserving relative imports. The
    # old worker is unsandboxed because this external evidence module has no
    # installed import location; replay uses the current production sandbox.
    name = "hc_data_platform.workflow.legacy_writer_evidence"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    legacy_writer = module.DatasetWriterWorkflow
    catalog = InMemoryLanceCatalog()
    schema = DatasetSchemaSnapshot.create(
        project_id="legacy-replay-project",
        dataset_id="legacy-replay-dataset",
        schema_snapshot_id="legacy-replay-schema",
        frequency_hz=30,
        fields={"x": "float64"},
    )
    catalog.register_schema(schema)
    steps = (
        StepRecord(
            rollout_id="legacy-replay-rollout",
            step_index=0,
            timestamp_ns=0,
            modalities={"x": 1.0},
            source_timestamps_ns={"x": (0,)},
            time_error_ns={"x": 0},
            valid={"x": True},
            repeated={"x": False},
        ),
    )
    manifest = AlignedFragmentManifestV1(
        project_id=schema.project_id,
        dataset_id=schema.dataset_id,
        schema_snapshot_id=schema.schema_snapshot_id,
        schema_fingerprint=schema.fingerprint,
        frequency_hz=30,
        rollout_id=steps[0].rollout_id,
        source_sha256="a" * 64,
        converter_version="legacy-replay-fixture/1",
        attempt_id="legacy-replay-attempt",
        fragment_uri="memory://legacy-compatibility-fixture",
        step_count=1,
        content_hash=compute_fragment_hash(steps),
    )
    request = DatasetWriterWorkflowInput(
        project_id=schema.project_id,
        dataset_id=schema.dataset_id,
        resource_id=schema.dataset_id + "/" + steps[0].rollout_id,
        commit=CatalogCommitActivityInput(
            fragment=CatalogFragmentPayloadV1(manifest=manifest, steps=steps)
        ),
    )
    configure_activity_dependencies(ActivityDependencies(catalog=catalog))
    client = await Client.connect(
        os.environ["HC_TEMPORAL_TARGET"],
        namespace="chayi-g1",
        data_converter=pydantic_data_converter,
    )
    queue = "chayi-g1-legacy-writer-" + uuid4().hex
    async with Worker(
        client,
        task_queue=queue,
        workflows=[legacy_writer],
        activities=[commit_fragment],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await client.start_workflow(
            legacy_writer.run,
            request,
            id=queue,
            task_queue=queue,
            execution_timeout=timedelta(seconds=45),
        )
        try:
            result = await asyncio.wait_for(handle.result(), timeout=45)
        except BaseException:
            await handle.cancel()
            raise
        assert result.status is JobStatus.SUCCEEDED, result
        history = await handle.fetch_history()
    await Replayer(
        workflows=[DatasetWriterWorkflow],
        data_converter=pydantic_data_converter,
    ).replay_workflow(history)
    scheduled = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert scheduled == [COMMIT_FRAGMENT_ACTIVITY]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    history_text = history.to_json()
    (output / "legacy-writer-history.json").write_text(history_text)
    report = {
        "prior_workflow_commit": args.source_commit,
        "prior_workflow_source_sha256": args.source_sha256,
        "history_sha256": hashlib.sha256(history_text.encode()).hexdigest(),
        "namespace": "chayi-g1",
        "workflow_id": queue,
        "scheduled_activities": scheduled,
        "new_writer_replay": "passed",
        "catalog": "in_memory_legacy_fixture_no_physical_lance_writes",
        "full_ingestion_acceptance": False,
    }
    (output / "legacy-writer-replay.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))

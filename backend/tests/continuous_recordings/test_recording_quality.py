"""Real recording checks run before any slice or Episode exists."""

from uuid import uuid4

import pytest

from hc_data_platform.continuous_recordings.models import SaveSliceDraftCommand
from hc_data_platform.continuous_recordings.processing import (
    PostgresContinuousEpisodeWorkflowInputResolver,
)
from hc_data_platform.continuous_recordings.quality import (
    EVENT_TYPE,
    RecordingQualityService,
)
from hc_data_platform.continuous_recordings.repository import PostgresContinuousRecordingRepository
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.events import DomainEventEnvelope


def check_recording(repository, processor, scope, recording_id, catalog=None, schemas=None, *, ingest_first=False):
    records = PostgresContinuousRecordingRepository(repository.connections)
    if not ingest_first:
        legacy_slicing_fixture(repository, scope, recording_id)
    current = records.get(scope, recording_id)
    assert current.status.value == "PENDING_QC"
    assert records.list_episode_processing(scope, recording_id) == ()
    with repository.transaction() as cursor:
        cursor.execute(
            """SELECT envelope FROM core.outbox_events WHERE organization_id=%s
            AND project_id=%s AND region_code=%s AND event_type=%s AND
            envelope->>'aggregate_id'=%s""",
            (scope.organization_id, scope.project_id, scope.region_code, EVENT_TYPE, recording_id),
        )
        events = cursor.fetchall()
    assert len(events) == 1
    event = DomainEventEnvelope.model_validate(events[0][0])
    runner = RecordingQualityService(
        repository.connections,
        PostgresContinuousEpisodeWorkflowInputResolver(
            repository.connections, catalog, package_schemas=schemas
        ),
        processor,
    )
    # An exhausted technical attempt is retryable without uploading or slicing.
    runner.failed(event.payload)
    assert records.get(scope, recording_id).status.value == "QC_ERROR"
    retry_id = str(uuid4())
    retried = records.retry_quality(scope, recording_id, retry_id)
    assert records.retry_quality(scope, recording_id, retry_id) == retried
    with pytest.raises(ValueError, match="stale"):
        runner.run(event.payload)
    event = event.model_copy(
        update={"payload": {**event.payload, "workflow_id": retried.quality_workflow_id}}
    )
    runner.failed(event.payload)
    retried = records.retry_quality(scope, recording_id, str(uuid4()))
    # A delayed replay of an older retry cannot supersede the current attempt.
    assert records.retry_quality(scope, recording_id, retry_id) == retried
    event = event.model_copy(
        update={"payload": {**event.payload, "workflow_id": retried.quality_workflow_id}}
    )
    with pytest.raises(ValueError):
        runner.run({**event.payload, "organization_id": "another-organization"})
    summary = runner.run(event.payload)
    assert summary["status"] == "PASS", summary
    assert runner.run(event.payload) == summary
    runner.failed(event.payload)
    with pytest.raises(ProblemException):
        records.retry_quality(scope, recording_id, str(uuid4()))
    assert records.get(scope, recording_id).status.value == ("SLICED" if ingest_first else "READY_FOR_SLICING")
    assert len(records.list_episode_processing(scope, recording_id)) == int(ingest_first)
    assert records.get(scope, recording_id).current_revision == int(ingest_first)
    return runner, event


@pytest.mark.parametrize("status", ["PENDING_QC", "QC_RUNNING", "QC_FAILED", "QC_ERROR"])
def test_quality_gate_blocks_all_slice_mutations(status):
    from tests.continuous_recordings.test_continuous_recordings import (
        _auth,
        _committed_services,
        _register,
    )

    # Legacy fixture gives a complete recording and service, then explicitly opts
    # into the new pre-slice state to test every authoring entry point.
    service, _, _, session_id = _committed_services()
    recording = _register(service, session_id).data
    repository = service._repository
    pending = recording.model_copy(update={"status": status})
    repository._recordings[(*pending.scope.model_dump().values(), pending.recording_id)] = pending
    args = dict(
        auth=_auth(),
        **pending.scope.model_dump(),
        recording_id=pending.recording_id,
        if_match=pending.etag,
        actor_id="test",
        request_id="test",
    )
    for action in (
        lambda: service.save_draft(**args, command=SaveSliceDraftCommand(slices=())),
        lambda: service.finalize(**args, expected_draft_revision=1),
    ):
        with pytest.raises(ProblemException) as error:
            action()
        assert error.value.problem.code == "RECORDING_QUALITY_NOT_PASSED"


async def check_recording_workflow(repository, runtime, scope, recording_id, monkeypatch):
    import asyncio
    import threading

    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.worker import Replayer, Worker

    from hc_data_platform.continuous_recordings.quality import RecordingQualityOutboxHandler
    from hc_data_platform.workflow import activities
    from hc_data_platform.workflow.service import TemporalWorkflowLauncher
    from hc_data_platform.workflow.temporal_workflows import ContinuousRecordingQualityWorkflow

    legacy_slicing_fixture(repository, scope, recording_id)
    with repository.transaction() as cursor:
        cursor.execute(
            "SELECT envelope FROM core.outbox_events WHERE event_type=%s "
            "AND organization_id=%s AND envelope->>'aggregate_id'=%s",
            (EVENT_TYPE, scope.organization_id, recording_id),
        )
        event = DomainEventEnvelope.model_validate(cursor.fetchone()[0])
    client = await Client.connect("temporal:7233", data_converter=pydantic_data_converter)
    queue = "recording-quality-test-" + uuid4().hex
    launcher = TemporalWorkflowLauncher("temporal:7233", client=client, task_queue=queue)
    handler = RecordingQualityOutboxHandler(launcher)
    processor = runtime.activities.continuous_episode_processing
    inspect = processor.inspect_quality
    attempts = []
    checking = threading.Event()
    allow_check = threading.Event()

    def interrupted(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("synthetic decoder process interruption")
        checking.set()
        assert allow_check.wait(30), "duplicate launch check timed out"
        return inspect(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(activities, "_dependencies", runtime.activities)
        patch.setattr(processor, "inspect_quality", interrupted)
        async with Worker(
            client,
            task_queue=queue,
            workflows=[ContinuousRecordingQualityWorkflow],
            activities=[activities.qc_continuous_recording, activities.fail_recording_quality],
        ):
            await handler(event)
            handle = client.get_workflow_handle(event.payload["workflow_id"])
            try:
                assert await asyncio.to_thread(checking.wait, 30)
                duplicate = await handler(event)
                assert duplicate.status.value == "RUNNING"
                assert duplicate.stage == "qc"
            finally:
                allow_check.set()
            result = await asyncio.wait_for(handle.result(), 90)
            assert result["result"]["status"] == "PASS"
            assert len(attempts) == 2
            await handler(event)  # Lost launch response is safe even after completion.
            history = await handle.fetch_history()
    await Replayer(
        workflows=[ContinuousRecordingQualityWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)
    records = PostgresContinuousRecordingRepository(repository.connections)
    assert records.get(scope, recording_id).status.value == "READY_FOR_SLICING"
    assert records.list_episode_processing(scope, recording_id) == ()


def legacy_slicing_fixture(repository, scope, recording_id):
    """Keep tests of historical manual drafts on their original workflow."""
    with repository.transaction() as cursor:
        cursor.execute("""UPDATE ingest.continuous_recordings
            SET recording_document=recording_document || '{"ingest_mode":"SLICE_FIRST"}'::jsonb
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND recording_id=%s
              AND current_revision=0""", (*scope.model_dump().values(), recording_id))

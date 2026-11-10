from datetime import datetime, timezone
from uuid import uuid4

import pytest

from hc_data_platform.continuous_recordings.models import (
    ContinuousRecording,
    RecordingQualitySummary,
    RecordingStatus,
)
from hc_data_platform.continuous_recordings.service import ContinuousRecordingService
from hc_data_platform.continuous_recordings.whole_recording import whole_recording_revision
from hc_data_platform.core.errors import ProblemException
from tests.continuous_recordings.test_continuous_recordings import (
    _committed_services,
    _register,
)


def ready_recording():
    service, _, _, session = _committed_services()
    old = _register(service, session).data
    return ContinuousRecording.model_validate(
        {
            **old.model_dump(),
            "schema_version": "continuous-recording/v2",
            "upload_session_id": None,
            "recording_upload_id": uuid4(),
            "ingest_mode": "WHOLE_RECORDING",
            "quality_summary": RecordingQualitySummary(
                report_id="passed",
                status="PASS",
                checked_at=datetime.now(timezone.utc),
            ),
        }
    )


def test_full_recording_dispatches_without_a_human_draft():
    from hc_data_platform.continuous_recordings.repository import (
        InMemoryContinuousRecordingRepository,
    )

    source = ready_recording()
    repository = InMemoryContinuousRecordingRepository()
    repository.create(source, actor_id="test", request_id="register")
    updated, revision = whole_recording_revision(source)
    repository.save_revision(
        recording=updated,
        revision=revision,
        expected_version=0,
        actor_id=revision.created_by,
        request_id="quality",
    )
    rows = repository.list_episode_processing(source.scope, source.recording_id)
    assert len(rows) == 1
    assert rows[0].episode_id == "episode_full_recording"
    assert rows[0].start_offset_ns == 0
    assert rows[0].end_offset_ns == source.duration_ns
    assert rows[0].status.value == "PENDING_QC"  # Dispatch is not successful ingestion.
    assert revision.authoring_mode.value == "SYSTEM"
    assert revision.slices[0].source_recording_upload_id == source.recording_upload_id
    assert revision.slices[0].started_at == source.capture_started_at
    assert revision.slices[0].ended_at == source.capture_ended_at
    assert whole_recording_revision(repository.get(source.scope, source.recording_id)) is None
    assert len(repository.outbox_events) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"status": RecordingStatus.PENDING_QC},
        {"status": RecordingStatus.QC_RUNNING},
        {"status": RecordingStatus.QC_FAILED},
        {"status": RecordingStatus.QC_ERROR},
        {"quality_summary": None},
        {"current_revision": 1},
        {"ingest_mode": "SLICE_FIRST"},
    ],
)
def test_unchecked_failed_and_historical_drafts_are_not_dispatched(changes):
    assert whole_recording_revision(ready_recording().model_copy(update=changes)) is None


def test_rejected_report_cannot_be_auto_ingested():
    source = ready_recording()
    source = source.model_copy(
        update={"quality_summary": source.quality_summary.model_copy(update={"status": "REJECT"})}
    )
    assert whole_recording_revision(source) is None


def test_new_recordings_do_not_race_human_finalization():
    with pytest.raises(ProblemException) as error:
        ContinuousRecordingService._require_quality(ready_recording())
    assert error.value.problem.code == "RECORDING_INGEST_FIRST"


def test_old_documents_keep_their_original_flow():
    document = ready_recording().model_dump()
    document.pop("ingest_mode")
    restored = ContinuousRecording.model_validate(document)
    assert restored.ingest_mode == "SLICE_FIRST"
    ContinuousRecordingService._require_quality(restored)


@pytest.mark.integration
@pytest.mark.parametrize("verdict", ["PASS", "REJECT"])
def test_quality_dispatch_transaction_and_recovery(verdict, monkeypatch):
    import os
    from types import SimpleNamespace

    from hc_data_platform.continuous_recordings.quality import RecordingQualityService
    from hc_data_platform.continuous_recordings.repository import (
        PostgresContinuousRecordingRepository,
    )
    from hc_data_platform.core.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )
    from hc_data_platform.core.dbapi import psycopg_connection_factory

    dsn = os.environ.get("HC_INGEST_FIRST_TEST_DSN")
    if not dsn:
        pytest.skip("explicit isolated HC_INGEST_FIRST_TEST_DSN required")
    factory = psycopg_connection_factory(dsn)
    source = ready_recording().model_copy(
        update={
            "recording_id": "whole-" + uuid4().hex,
            "status": RecordingStatus.PENDING_QC,
            "quality_summary": None,
        }
    )
    scope = source.scope
    token = bind_request_context(
        RequestContext(**scope.model_dump(), subject_id="ingest-first-test", service_identity=True)
    )
    try:
        with factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO registry.organization_projects VALUES (%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (scope.organization_id, scope.project_id, "Synthetic ingest-first test"),
            )
            cursor.execute(
                """INSERT INTO ingest.recording_uploads
                (organization_id,project_id,region_code,upload_id,recording_id,manifest_sha256,
                 status,upload_document,created_by,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,'COMMITTED','{}','test',now(),now())""",
                (
                    *scope.model_dump().values(),
                    source.recording_upload_id,
                    source.recording_id,
                    source.manifest_fingerprint,
                ),
            )
        repository = PostgresContinuousRecordingRepository(factory)
        source = repository.create(source, actor_id="test", request_id="test-register")
        calls = []

        def inspect(*args, **kwargs):
            calls.append(1)
            return {
                "status": verdict,
                "finding_codes": [],
                "warning_codes": [],
                "quality_report": {"findings": []},
            }

        runner = RecordingQualityService(
            factory,
            SimpleNamespace(resolve_recording=lambda **kwargs: source),
            SimpleNamespace(inspect_quality=inspect),
        )
        request = {
            **scope.model_dump(),
            "recording_id": source.recording_id,
            "source_sha256": source.source_sha256,
            "workflow_id": source.quality_workflow_id,
        }
        if verdict == "PASS":
            original = PostgresContinuousRecordingRepository.write_revision

            def interrupted(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("injected before dispatch transaction commits")

            with monkeypatch.context() as patch:
                patch.setattr(PostgresContinuousRecordingRepository, "write_revision", interrupted)
                with pytest.raises(RuntimeError, match="injected"):
                    runner.run(request)
            # The report is durable; every partial window and dispatch was rolled back.
            assert repository.get(scope, source.recording_id).current_revision == 0
            assert repository.list_episode_processing(scope, source.recording_id) == ()
        result = runner.run(request)
        assert runner.run(request) == result
        assert calls == [1]
        rows = repository.list_episode_processing(scope, source.recording_id)
        assert len(rows) == (1 if verdict == "PASS" else 0)
        current = repository.get(scope, source.recording_id)
        assert current.status.value == ("SLICED" if verdict == "PASS" else "QC_FAILED")
        shown = repository.present((current,))[0]
        assert shown.whole_episode_status == ("PENDING_QC" if verdict == "PASS" else None)
        with factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT count(*) FROM core.outbox_events
                WHERE event_type='continuous-recording.episode.workflow.requested.v1'
                AND envelope->'payload'->>'recording_id'=%s""",
                (source.recording_id,),
            )
            assert cursor.fetchone()[0] == (1 if verdict == "PASS" else 0)
    finally:
        reset_request_context(token)

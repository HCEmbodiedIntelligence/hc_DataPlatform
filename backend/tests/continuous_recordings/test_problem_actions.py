import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.continuous_recordings.asset_models import (
    EpisodeProcessingStatus as Status,
)
from hc_data_platform.continuous_recordings.asset_models import (
    RetryEpisodeCommand,
)
from hc_data_platform.continuous_recordings.models import RecordingScope, SaveSliceDraftCommand
from hc_data_platform.continuous_recordings.repository import PostgresContinuousRecordingRepository
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.quality.postgres import PostgresQualityRepository
from hc_data_platform.workflow.continuous_episode_dispatch import ContinuousEpisodeOutboxHandler
from tests.continuous_recordings.test_continuous_recordings import (
    _auth,
    _committed_services,
    _register,
)
from tests.dashboard.test_dashboard_postgres import (
    NOW,
    isolated_dsn,  # noqa: F401
    organization_for,
    request_scope,
    seed_rollout,
)
from tests.quality.test_router import _report


def finalized(service, auth, recording):
    args = dict(
        auth=auth,
        organization_id=recording.scope.organization_id,
        project_id=recording.scope.project_id,
        region_code=recording.scope.region_code,
        recording_id=recording.recording_id,
        actor_id="operator-a",
        request_id="test",
    )
    service.save_draft(
        **args,
        if_match='"v1"',
        command=SaveSliceDraftCommand.model_validate(
            {
                "slices": [
                    {
                        "episode_id": f"episode_{i:02d}",
                        "start_offset_ns": str(i * 1_000_000_000),
                        "end_offset_ns": str((i + 1) * 1_000_000_000),
                    }
                    for i in range(2)
                ]
            }
        ),
    )
    service.finalize(**args, expected_draft_revision=1, if_match='"v2"')
    repo = service._repository
    episode = repo.get_episode_processing(recording.scope, recording.recording_id, "episode_00")
    failed = episode.model_copy(
        update={
            "status": Status.QC_FAILED,
            "qc_report_id": "qc-test",
            "failure_code": "QC_REJECTED",
            "failure_stage": "qc",
        }
    )
    repo.save_episode_processing(failed, expected_status=Status.PENDING_QC)
    return args, failed


@pytest.mark.parametrize("discard", [False, True])
def test_episode_decision_is_scoped_idempotent_and_preserves_sibling(discard):
    service, _, _, session_id = _committed_services()
    recording = _register(service, session_id).data
    args, failed = finalized(service, _auth(), recording)
    repo = service._repository
    command = RetryEpisodeCommand(request_id=uuid4(), expected_workflow_id=failed.workflow_id)
    before = len(repo.outbox_events)
    result = service.retry_episode(
        **args, episode_id="episode_00", command=command, discard=discard
    )
    assert result.status == (Status.DISCARDED if discard else Status.PENDING_QC)
    assert result.qc_report_id == failed.qc_report_id
    assert len(repo.outbox_events) == before + (0 if discard else 1)
    assert (
        service.retry_episode(**args, episode_id="episode_00", command=command, discard=discard)
        == result
    )
    assert (
        repo.get_episode_processing(recording.scope, recording.recording_id, "episode_01").status
        == Status.PENDING_QC
    )
    assert (
        repo.get(recording.scope, recording.recording_id).source_sha256 == recording.source_sha256
    )
    with pytest.raises(ProblemException, match="EPISODE_RETRY_REQUEST_REUSED"):
        service.retry_episode(**args, episode_id="episode_00", command=command, discard=not discard)
    with pytest.raises(ProblemException):
        service.retry_episode(
            **args,
            episode_id="episode_00",
            command=command.model_copy(update={"request_id": uuid4()}),
            discard=discard,
        )
    denied = {**args, "auth": replace(_auth(), capabilities=frozenset({"upload.read"}))}
    with pytest.raises(ProblemException):
        service.retry_episode(**denied, episode_id="episode_00", command=command, discard=discard)
    with pytest.raises(ProblemException):
        repo.save_episode_processing(failed, expected_status=Status.PENDING_QC)


def test_retry_outbox_uses_new_scoped_workflow_identity():
    service, _, _, session_id = _committed_services()
    recording = _register(service, session_id).data
    args, failed = finalized(service, _auth(), recording)
    command = RetryEpisodeCommand(request_id=uuid4(), expected_workflow_id=failed.workflow_id)
    result = service.retry_episode(**args, episode_id="episode_00", command=command)
    event = service._repository.outbox_events[str(result.event_id)]
    seen = []

    class Resolver:
        def terminal_result(self, **kwargs):
            seen.append(kwargs)
            return {"status": "QC_FAILED"}

    handler = ContinuousEpisodeOutboxHandler(None, Resolver())
    assert asyncio.run(handler(event)) == {"status": "QC_FAILED"}
    assert seen[0]["expected_workflow_id"] == result.workflow_id
    assert result.workflow_id != failed.workflow_id
    forged = event.model_copy(
        update={"payload": {**event.payload, "retry_request_id": str(uuid4())}}
    )
    with pytest.raises(ValueError, match="locator disagrees"):
        asyncio.run(handler(forged))


@pytest.mark.integration
@pytest.mark.parametrize("discard", [False, True])
def test_postgres_episode_decision_audits_and_atomically_enqueues(isolated_dsn, discard):  # noqa: F811
    service, _, _, session_id = _committed_services()
    original = _register(service, session_id).data
    project, region = "project-a", "cn-east"
    scope = RecordingScope(
        organization_id=organization_for(project), project_id=project, region_code=region
    )
    with request_scope(project, region):
        with psycopg_connection_factory(isolated_dsn)() as connection:
            # The dashboard fixture contains placeholder QC JSON. Its summaries
            # are unrelated to the recording reports exercised by this test.
            connection.execute(
                "DELETE FROM quality_rollout_summaries WHERE project_id=%s AND region_code=%s",
                (project, region),
            )
            _, upload_id = seed_rollout(
                connection,
                project_id=project,
                region_code=region,
                suffix=f"problem-{uuid4().hex[:8]}",
                at=NOW,
            )
        repo = PostgresContinuousRecordingRepository(psycopg_connection_factory(isolated_dsn))
        recording = original.model_copy(
            update={
                "scope": scope,
                "upload_session_id": upload_id,
                "recording_id": f"recording-{uuid4().hex}",
            }
        )
        repo.create(recording, actor_id="operator-a", request_id="create")
        service._repository = repo
        auth = replace(
            _auth(),
            organization_ids=frozenset({scope.organization_id}),
            region_codes=frozenset({region}),
            scope_pairs=frozenset({(project, region)}),
            organization_scope_triples=frozenset({(scope.organization_id, project, region)}),
        )
        args, failed = finalized(service, auth, recording)
        report_id = f"qc-{recording.recording_id}"
        failed = failed.model_copy(update={"qc_report_id": report_id})
        repo.save_episode_processing(failed, expected_status=Status.QC_FAILED)
        with psycopg_connection_factory(isolated_dsn)() as connection:
            connection.execute(
                """INSERT INTO ingest.recording_episode_qc_reports
                (organization_id,project_id,region_code,recording_id,episode_id,
                 report_id,report_sha256,status,report_document,created_at)
                VALUES (%s,%s,%s,%s,'episode_00',%s,%s,'REJECT',%s::jsonb,%s)""",
                (
                    scope.organization_id,
                    project,
                    region,
                    recording.recording_id,
                    report_id,
                    "a" * 64,
                    json.dumps(
                        {
                            "status": "REJECT",
                            "episode_id": "episode_00",
                            "finding_codes": ["OPENARM_CAPTURE_VECTOR_INVALID"],
                            "quality_report": _report().model_dump(mode="json"),
                        }
                    ),
                    NOW,
                ),
            )
        quality = PostgresQualityRepository(psycopg_connection_factory(isolated_dsn))

        def visible():
            return [
                p
                for p in quality.list_problem_reports(project_id=project, region_code=region)
                if p.recording_id == recording.recording_id
            ]

        assert len(visible()) == 1
        command = RetryEpisodeCommand(request_id=uuid4(), expected_workflow_id=failed.workflow_id)
        result = service.retry_episode(
            **args, episode_id="episode_00", command=command, discard=discard
        )
        assert result.status == (Status.DISCARDED if discard else Status.PENDING_QC)
        assert (
            service.retry_episode(**args, episode_id="episode_00", command=command, discard=discard)
            == result
        )
        with psycopg.connect(isolated_dsn) as connection:
            audits = connection.execute(
                "SELECT action FROM core.audit_events WHERE resource_id=%s",
                (f"{recording.recording_id}:episode_00",),
            ).fetchall()
            assert audits == [
                (
                    "continuous_recording.episode.discarded"
                    if discard
                    else "continuous_recording.episode.retried",
                )
            ]
            events = connection.execute(
                "SELECT envelope FROM core.outbox_events "
                "WHERE envelope->'payload'->>'retry_request_id'=%s",
                (str(command.request_id),),
            ).fetchall()
            assert len(events) == (0 if discard else 1)
        sibling = repo.get_episode_processing(scope, recording.recording_id, "episode_01")
        assert sibling.status == Status.PENDING_QC
        assert len(visible()) == (0 if discard else 1)


@pytest.mark.integration
@pytest.mark.parametrize(
    "discard,claimed,error",
    [
        (False, False, True),
        (True, False, True),
        (False, True, True),
        (True, True, True),
        (False, False, False),
    ],
)
def test_blocked_episode_actions_require_an_unclaimed_failed_dispatch(
    isolated_dsn,  # noqa: F811
    discard,
    claimed,
    error,
):
    service, _, _, session_id = _committed_services()
    original = _register(service, session_id).data
    project, region = "project-a", "cn-east"
    scope = RecordingScope(
        organization_id=organization_for(project), project_id=project, region_code=region
    )
    connect = psycopg_connection_factory(isolated_dsn)
    with request_scope(project, region):
        with connect() as db:
            _, upload_id = seed_rollout(
                db,
                project_id=project,
                region_code=region,
                suffix=f"blocked-{uuid4().hex[:8]}",
                at=NOW,
            )
        repo = PostgresContinuousRecordingRepository(connect)
        recording = original.model_copy(
            update={
                "scope": scope,
                "upload_session_id": upload_id,
                "recording_id": f"recording-{uuid4().hex}",
            }
        )
        repo.create(recording, actor_id="operator", request_id="create")
        service._repository = repo
        auth = replace(
            _auth(),
            organization_ids=frozenset({scope.organization_id}),
            region_codes=frozenset({region}),
            scope_pairs=frozenset({(project, region)}),
            organization_scope_triples=frozenset({(scope.organization_id, project, region)}),
        )
        args, failed = finalized(service, auth, recording)
        pending = failed.model_copy(
            update={
                "status": Status.PENDING_QC,
                "failure_code": None,
                "failure_stage": None,
                "qc_report_id": None,
            }
        )
        repo.save_episode_processing(pending, expected_status=Status.QC_FAILED)
        with connect() as db:
            db.execute(
                """UPDATE core.outbox_events SET last_error_code=%s, claim_token=%s,
                claimed_by=%s, claimed_until=%s WHERE event_id=%s""",
                (
                    "RECORDING_DATASET_SCHEMA_MISMATCH" if error else None,
                    str(uuid4()) if claimed else None,
                    "test-worker" if claimed else None,
                    NOW if claimed else None,
                    pending.event_id,
                ),
            )
        command = RetryEpisodeCommand(request_id=uuid4(), expected_workflow_id=pending.workflow_id)
        if claimed or not error:
            with pytest.raises(ProblemException, match="EPISODE_RETRY_UNAVAILABLE"):
                service.retry_episode(
                    **args, episode_id="episode_00", command=command, discard=discard
                )
            assert (
                repo.get_episode_processing(scope, recording.recording_id, "episode_00").workflow_id
                == pending.workflow_id
            )
            return
        result = service.retry_episode(
            **args, episode_id="episode_00", command=command, discard=discard
        )
        assert result.status == (Status.DISCARDED if discard else Status.PENDING_QC)
        assert (
            service.retry_episode(**args, episode_id="episode_00", command=command, discard=discard)
            == result
        )
        with connect() as db:
            published = db.execute(
                "SELECT published_at FROM core.outbox_events WHERE event_id=%s", (pending.event_id,)
            ).fetchone()[0]
            assert (published is not None) == (not discard)
            count = db.execute(
                "SELECT count(*) FROM core.outbox_events "
                "WHERE envelope->'payload'->>'retry_request_id'=%s",
                (str(command.request_id),),
            ).fetchone()[0]
            assert count == (0 if discard else 1)
        assert (
            repo.get_episode_processing(scope, recording.recording_id, "episode_01").status
            == Status.PENDING_QC
        )

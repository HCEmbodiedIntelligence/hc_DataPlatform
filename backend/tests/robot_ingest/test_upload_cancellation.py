"""Operator cancellation, terminal races, scope boundaries and storage retry."""

from dataclasses import replace
from datetime import timedelta

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_ingest.models import UploadState
from tests.robot_ingest.test_robot_ingest_service import (
    ORG,
    PROJECT_A,
    PROJECT_B,
    REGION,
    Harness,
    _admin,
    _manifest,
)


def operator():
    auth = _admin()
    return replace(
        auth,
        organization_scoped_capabilities=auth.organization_scoped_capabilities
        | {
            (ORG, PROJECT_A, "upload.manage"),
            (ORG, PROJECT_B, "upload.manage"),
        },
    )


def cancel(h, upload, **overrides):
    args = dict(
        auth=operator(),
        organization_id=ORG,
        project_id=PROJECT_A,
        region_code=REGION,
        upload_id=upload.upload_id,
    )
    args.update(overrides)
    return h.service.cancel_upload_as_operator(**args)


@pytest.mark.parametrize("state", ["UPLOADING", "PAUSED", "READY_TO_COMMIT", "FAILED"])
def test_operator_cancels_expired_upload_without_robot_credentials(state):
    h = Harness()
    upload = h.service.create_upload(token=h.token, manifest=_manifest()).data
    h.repository.save_upload(upload.model_copy(update={"state": UploadState(state)}))
    h.now = upload.expires_at + timedelta(days=2)
    h.service.revoke_credential(
        auth=_admin(),
        organization_id=ORG,
        project_id=PROJECT_A,
        ingest_identity_id=h.identity_id,
        credential_id=h.credential_id,
    )
    result = cancel(h, upload)
    assert result.data.state is UploadState.CANCELLED
    assert result.data.cancelled_by == operator().subject_id
    assert result.data.cancelled_at == h.now
    assert upload.assets[0].multipart_upload_id not in h.storage._uploads
    assert cancel(h, upload).resumed


def test_permissions_scope_and_committed_data_are_protected():
    h = Harness()
    upload = h.service.create_upload(token=h.token, manifest=_manifest()).data
    for changes, status in [
        ({"auth": _admin()}, 403),
        ({"project_id": PROJECT_B}, 404),
        ({"organization_id": "foreign"}, 403),
        ({"region_code": "foreign"}, 403),
    ]:
        with pytest.raises(ProblemException) as error:
            cancel(h, upload, **changes)
        assert error.value.problem.status == status
    committed = upload.model_copy(
        update={"state": UploadState.COMMITTED, "raw_source_id": "raw-protected"}
    )
    h.repository.save_upload(committed)
    with pytest.raises(ProblemException) as error:
        cancel(h, upload)
    assert error.value.problem.status == 409
    assert upload.assets[0].multipart_upload_id in h.storage._uploads
    assert h.repository.get_upload(ORG, upload.upload_id, h.identity_id) == committed


def test_cancelled_upload_cannot_be_revived_by_stale_complete_or_commit():
    h = Harness()
    upload = h.service.create_upload(token=h.token, manifest=_manifest()).data
    cancel(h, upload)
    for state in [
        UploadState.UPLOADING,
        UploadState.PAUSED,
        UploadState.READY_TO_COMMIT,
        UploadState.COMMITTED,
    ]:
        with pytest.raises(ProblemException):
            h.repository.save_upload(upload.model_copy(update={"state": state}))
    with pytest.raises(ProblemException):
        h.repository.commit_raw_source(
            upload=upload,
            raw_source_id="raw-stale",
            manifest_key="key",
            storage_prefix="prefix",
            content_hash="a" * 64,
            adapter_name="mcap",
            processing_status="PENDING",
            now=h.now,
        )
    assert (
        h.repository.get_upload(ORG, upload.upload_id, h.identity_id).state is UploadState.CANCELLED
    )


def test_storage_failure_rolls_back_and_missing_multipart_can_be_retried(monkeypatch):
    h = Harness()
    upload = h.service.create_upload(token=h.token, manifest=_manifest()).data

    def unavailable(*args):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(h.storage, "abort_multipart", unavailable)
    with pytest.raises(ProblemException) as error:
        cancel(h, upload)
    assert error.value.problem.status == 503
    assert (
        h.repository.get_upload(ORG, upload.upload_id, h.identity_id).state is UploadState.UPLOADING
    )

    class MissingUpload(Exception):
        response = {"Error": {"Code": "NoSuchUpload"}}

    def missing(*args):
        raise MissingUpload()

    monkeypatch.setattr(h.storage, "abort_multipart", missing)
    assert cancel(h, upload).data.state is UploadState.CANCELLED


def test_operator_route_uses_user_scope_without_robot_bearer(monkeypatch):
    from tests.robot_ingest import test_robot_ingest_api as api

    h = Harness()
    upload = h.service.create_upload(token=h.token, manifest=_manifest()).data
    monkeypatch.setattr(api, "_auth", operator)
    with api._app(h.service) as client:
        result = client.post(
            f"/api/v1/projects/{PROJECT_A}/robot-ingest/uploads/{upload.upload_id}:cancel",
            headers={"X-Organization-Id": ORG, "X-Region-Code": REGION},
        )
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["data"]["state"] == "CANCELLED"


@pytest.mark.integration
def test_postgres_cancel_serializes_commit_and_updates_capture_and_rail():
    import os
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    from threading import Event
    from uuid import uuid4

    from hc_data_platform.collection_tasks.captures import read_captures
    from hc_data_platform.collection_tasks.postgres import PostgresCollectionTaskRepository
    from hc_data_platform.collection_tasks.service import CollectionTaskService
    from hc_data_platform.core.context import (
        RequestContext,
        bind_request_context,
        reset_request_context,
    )
    from hc_data_platform.core.dbapi import psycopg_connection_factory
    from hc_data_platform.dashboard.recording_projection import recording_package
    from hc_data_platform.dashboard.service import _stage_counts
    from hc_data_platform.ingest.ports import InMemoryObjectStorage
    from hc_data_platform.robot_ingest.models import (
        CreateRobotIngestIdentity,
        IssueCredentialCommand,
    )
    from hc_data_platform.robot_ingest.repository import PostgresRobotIngestRepository
    from hc_data_platform.robot_ingest.service import RobotIngestService
    from tests.robot_ingest import test_robot_ingest_postgres as pg

    dsn = os.environ.get("ROBOT_INGEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires explicitly isolated ROBOT_INGEST_POSTGRES_DSN")
    pg._setup_database(dsn)
    connections = psycopg_connection_factory(dsn)
    repo = PostgresRobotIngestRepository.from_dsn(dsn, connections)
    storage = InMemoryObjectStorage()
    service = RobotIngestService(
        repo, storage, credential_hmac_key="test-cancel-postgres-hmac-key", clock=lambda: pg.NOW
    )
    auth = pg._admin()
    auth = replace(
        auth,
        organization_scoped_capabilities=auth.organization_scoped_capabilities
        | {(pg.ORG, pg.PROJECT_A, "upload.manage")},
    )
    token = bind_request_context(
        RequestContext(organization_id=pg.ORG, project_id=pg.PROJECT_A, region_code=pg.REGION_A)
    )
    try:
        ident = service.create_identity(
            auth=pg._admin(),
            organization_id=pg.ORG,
            project_id=pg.PROJECT_A,
            command=CreateRobotIngestIdentity(robot_id=pg.ROBOT, allowed_formats=("MCAP",)),
        ).data
        credential = service.issue_credential(
            auth=pg._admin(),
            organization_id=pg.ORG,
            project_id=pg.PROJECT_A,
            ingest_identity_id=ident.ingest_identity_id,
            command=IssueCredentialCommand(),
        ).credential
        upload = service.create_upload(
            token=credential.token, manifest=pg._manifest(pg.TASK_A, str(uuid4()), b"cancel-test")
        ).data
        entered, release, commit_started = Event(), Event(), Event()
        abort = storage.abort_multipart

        def slow_abort(key, multipart):
            entered.set()
            assert release.wait(5)
            abort(key, multipart)

        storage.abort_multipart = slow_abort

        def cancel_current():
            return service.cancel_upload_as_operator(
                auth=auth,
                organization_id=pg.ORG,
                project_id=pg.PROJECT_A,
                region_code=pg.REGION_A,
                upload_id=upload.upload_id,
            )

        def stale_commit():
            commit_started.set()
            return repo.commit_raw_source(
                upload=upload,
                raw_source_id="raw-cancel-race",
                manifest_key="unused",
                storage_prefix="unused",
                content_hash="a" * 64,
                adapter_name="mcap",
                processing_status="PENDING",
                now=pg.NOW,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            cancel_future = pool.submit(copy_context().run, cancel_current)
            assert entered.wait(5)
            commit_future = pool.submit(copy_context().run, stale_commit)
            assert commit_started.wait(5)
            release.set()
            assert cancel_future.result(timeout=10).data.state is UploadState.CANCELLED
            with pytest.raises(ProblemException):
                commit_future.result(timeout=10)
        with pytest.raises(ProblemException):
            repo.save_upload(upload.model_copy(update={"state": UploadState.READY_TO_COMMIT}))
        with connections() as db, db.cursor() as cursor:
            items = read_captures(cursor, pg.ORG, pg.PROJECT_A, pg.REGION_A, pg.TASK_A)
        capture = next(x for x in items if x.upload_id == upload.upload_id)
        assert capture.stage == "CANCELLED" and capture.source_path == "capture.mcap"
        stages = _stage_counts((recording_package(capture),))
        stage = next(s for s in stages if s.stage.value == "PACKAGE_UPLOAD")
        assert stage.cancelled == 1 and stage.running == stage.blocked == stage.failed == 0
        tasks = CollectionTaskService(repository=PostgresCollectionTaskRepository(connections))
        unfinished = tasks.captures(pg.ORG, pg.PROJECT_A, pg.REGION_A, unfinished_uploads_only=True)
        assert all(x.upload_id != upload.upload_id for x in unfinished.items)
        assert service.cancel_upload_as_operator(
            auth=auth,
            organization_id=pg.ORG,
            project_id=pg.PROJECT_A,
            region_code=pg.REGION_A,
            upload_id=upload.upload_id,
        ).resumed
    finally:
        reset_request_context(token)

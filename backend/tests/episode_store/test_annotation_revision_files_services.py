"""Real PG/outbox/MinIO checks; generated RGB-D is a fault fixture, not P0 acceptance."""

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import psycopg
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.annotation.models import (
    AnnotationObjectRelation,
    AnnotationOperation,
    AnnotationTag,
    ReviewDecision,
    TagObjectRef,
)
from hc_data_platform.annotation.revision_files import (
    AnnotationRevisionFileOutboxHandler,
    AnnotationRevisionFileService,
    PostgresRevisionFiles,
    SnapshotBusy,
    revision_document,
)
from hc_data_platform.annotation.router import (
    get_annotation_auth,
    get_revision_file_service,
    router,
)
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security import AuthContext
from hc_data_platform.security.outbox import OutboxDispatcher, PostgresOutboxDeliveryRepository
from tests.episode_store.test_annotation_bindings_services import (
    actor,
    publish_schema,
)
from tests.episode_store.test_commit_services import scoped


@pytest.fixture
def files(target):
    packages, source, annotation, automatic, request = target
    ident = source.identity
    auth = AuthContext(
        subject_id="editor",
        service_identity=True,
        organization_ids=frozenset({ident.organization_id}),
        project_ids=frozenset({ident.project_id}),
        region_codes=frozenset({ident.region_code}),
        scope_pairs=frozenset({(ident.project_id, ident.region_code)}),
        organization_scope_triples=frozenset({ident.scope}),
        capabilities=frozenset({"annotation_task.read", "annotation.save", "export.download"}),
    )
    repository = PostgresRevisionFiles(packages.repository.connections)
    service = AnnotationRevisionFileService(annotation, repository, packages.committer.storage)
    with scoped(source):
        publish_schema(source, annotation)
        task = automatic.ensure_task(request)
        claimed = annotation.claim(task.task_id, actor(ident))
        tags = (
            AnnotationTag(
                annotation_id="parent",
                tag_id="p",
                path=("p",),
                label="父",
                start_step=0,
                end_step=2,
            ),
            AnnotationTag(
                annotation_id="child",
                tag_id="c",
                path=("p", "c"),
                parent_annotation_id="parent",
                label="交叉子区间",
                start_step=1,
                end_step=4,
                attributes={"difficulty": 2, "confirmed": True},
                subject=TagObjectRef(object_id="arm", object_type="robot"),
                relations=(
                    AnnotationObjectRelation(
                        relation_type="holds",
                        target=TagObjectRef(object_id="cup", object_type="object"),
                    ),
                ),
            ),
        )
        annotation.save_draft(
            task.task_id,
            actor(ident),
            (
                AnnotationOperation(
                    operation_id="exclude",
                    kind="EXCLUDE",
                    start_step=2,
                    end_step=3,
                    reason="fixture exclusion",
                ),
            ),
            tags=tags,
            expected_revision=0,
            if_match=claimed.etag,
            client_mutation_id="save",
        )
    return source, task, service, auth


def event_for(service, task, revision, generation=1):
    with service.repository.transaction() as cursor:
        cursor.execute(
            """SELECT envelope FROM core.outbox_events WHERE event_type = %s
                AND envelope->'payload'->>'task_id' = %s
                AND envelope->'payload'->>'revision' = %s
                AND envelope->'payload'->>'generation' = %s""",
            (
                AnnotationRevisionFileOutboxHandler.EVENT_TYPE,
                task.task_id,
                str(revision),
                str(generation),
            ),
        )
        return DomainEventEnvelope.model_validate(cursor.fetchone()[0])


def drain(source, service):
    handler = AnnotationRevisionFileOutboxHandler(service)
    dispatcher = OutboxDispatcher(
        PostgresOutboxDeliveryRepository(service.repository.connections),
        {handler.EVENT_TYPE: handler},
        worker_id="snapshot-test",
    )
    for _ in range(20):
        if not asyncio.run(
            dispatcher.dispatch_one(
                organization_id=source.identity.organization_id,
                project_id=source.identity.project_id,
                region_code=source.identity.region_code,
            )
        ):
            return
    raise AssertionError("outbox did not drain")


@pytest.mark.integration
def test_transactional_generation_full_json_and_approval_download(files, monkeypatch):
    source, task, service, auth = files
    annotation = service.annotation
    with scoped(source):
        assert service.status(task.task_id, 1, auth).status == "PENDING"
        history = annotation.get_history(task.task_id, actor(source.identity))
        # Rollback after revision insertion must also remove its job and notification.
        audit = annotation._repository._insert_transition_audit
        monkeypatch.setattr(
            annotation._repository,
            "_insert_transition_audit",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("rollback probe")),
        )
        with pytest.raises(RuntimeError, match="rollback probe"):
            annotation.save_draft(
                task.task_id,
                actor(source.identity),
                (),
                tags=(),
                expected_revision=1,
                if_match=history.task.etag,
                client_mutation_id="rolled-back",
            )
        monkeypatch.setattr(annotation._repository, "_insert_transition_audit", audit)
        assert service.repository.get(task.task_id, 2) is None
        assert annotation.get_history(task.task_id, actor(source.identity)) == history
        schema = annotation.get_bound_tag_schema(task.task_id, auth)
        assert revision_document(history, 1, schema, source.identity.organization_id)
        drain(source, service)
        ready = service.status(task.task_id, 1, auth)
        assert ready.status == "READY" and ready.attempts == 1
        with pytest.raises(ProblemException, match="ANNOTATION_APPROVED_REVISION_REQUIRED"):
            service.download(task.task_id, auth)
        with pytest.raises(ProblemException, match="ANNOTATION_UNREVIEWED_DOWNLOAD_REQUIRED"):
            service.download(task.task_id, auth, revision=1)
        draft = service.download(task.task_id, auth, revision=1, allow_unreviewed=True)
        document = json.loads(draft.content)
        assert not draft.approved and draft.file == ready
        assert document["revision"] == history.revisions[1].model_dump(mode="json")
        assert document["effective_exclusions"] == [
            {"start_step": 2, "end_step": 3, "modality_scope": "ALL_MODALITIES"}
        ]
        assert document["cumulative_operations"] == [
            op.model_dump(mode="json") for op in history.revisions[1].operations
        ]
        assert document["data_binding"]["data_ref"] == task.data_ref.model_dump(mode="json")
        assert "base_lance_version" not in document["data_binding"]
        assert document["tag_schema"]["document"] == schema.document.model_dump(mode="json")
        assert ready.content_sha256 == hashlib.sha256(draft.content).hexdigest()
        submission = annotation.submit_for_review(
            task.task_id,
            actor(source.identity),
            expected_revision=1,
            if_match=history.task.etag,
            idempotency_key="approve-submit",
        )
        annotation.review(
            task.task_id,
            actor(source.identity, "reviewer"),
            ReviewDecision.APPROVE,
            revision=1,
            submission_id=submission.submission_id,
            if_match=annotation.get_task(task.task_id).etag,
        )
        approved = service.download(task.task_id, auth)
        assert approved.approved and approved.content == draft.content
        assert approved.submission_id == submission.submission_id and approved.review_id
        # Rebuild the same saved revision after its task gained a review: identical bytes.
        service.request(task.task_id, 1, 1, auth)
        drain(source, service)
        assert service.download(task.task_id, auth).content == draft.content
        assert annotation.get_task(task.task_id).current_revision == 1


@pytest.mark.integration
def test_failed_generation_concurrent_retry_and_stale_events(files, monkeypatch):
    source, task, service, auth = files
    handler = AnnotationRevisionFileOutboxHandler(service)
    put = service.storage.put_json
    with scoped(source):
        event = event_for(service, task, 1)
        monkeypatch.setattr(
            service.storage,
            "put_json",
            lambda *a, **k: (_ for _ in ()).throw(OSError("secret backend error must not leak")),
        )
        handler(event)
        failed = service.status(task.task_id, 1, auth)
        assert (
            failed.status == "FAILED" and failed.error_code == "ANNOTATION_FILE_GENERATION_FAILED"
        )
        monkeypatch.setattr(service.storage, "put_json", put)

    def retry(_):
        with scoped(source):
            try:
                return service.request(task.task_id, 1, failed.generation, auth).generation
            except ProblemException as exc:
                assert exc.problem.code == "ANNOTATION_FILE_GENERATION_CONFLICT"
                return "conflict"

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(retry, range(4)))
    assert outcomes.count(2) == 1 and outcomes.count("conflict") == 3
    with scoped(source):
        handler(event)  # Old event cannot consume the new generation.
        assert service.status(task.task_id, 1, auth).status == "PENDING"
        drain(source, service)
        assert service.status(task.task_id, 1, auth).status == "READY"
        assert service.annotation.get_task(task.task_id).current_revision == 1


@pytest.mark.integration
def test_crash_after_object_put_reuses_bytes_and_fences_old_worker(files, monkeypatch):
    source, task, service, auth = files
    handler = AnnotationRevisionFileOutboxHandler(service)

    class WorkerCrash(BaseException):
        pass

    put, written = service.storage.put_json, []

    def crash(key, value, **kwargs):
        result = put(key, value, **kwargs)
        written.append(result)
        raise WorkerCrash()

    with scoped(source):
        event = event_for(service, task, 1)
        monkeypatch.setattr(service.storage, "put_json", crash)
        with pytest.raises(WorkerCrash):
            handler(event)
        old = service.repository.get(task.task_id, 1)
        assert old["status"] == "GENERATING" and len(written) == 1
        with pytest.raises(SnapshotBusy):
            handler(event)
        with service.repository.transaction() as cursor:
            cursor.execute(
                "UPDATE annotation.revision_files "
                "SET lease_expires_at = now() - interval '1 second' "
                "WHERE task_id=%s AND revision=1",
                (task.task_id,),
            )
        monkeypatch.setattr(
            service.storage, "put_json", lambda *a, **k: pytest.fail("must reuse completed object")
        )
        handler(event)
        ready = service.status(task.task_id, 1, auth)
        assert ready.status == "READY" and ready.attempts == 2
        assert not service.repository.finish(old, error_code="STALE_WORKER")
        handler(event)
        assert service.status(task.task_id, 1, auth) == ready


@pytest.mark.integration
def test_scope_permissions_http_and_integrity_repair(files):
    source, task, service, auth = files
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_revision_file_service] = lambda: service
    current = {"auth": auth}
    app.dependency_overrides[get_annotation_auth] = lambda: current["auth"]

    @app.exception_handler(ProblemException)
    async def error(_: Request, exc: ProblemException):
        return JSONResponse(
            status_code=exc.problem.status, content=exc.problem.model_dump(mode="json")
        )

    with scoped(source), TestClient(app) as client:
        path = f"/api/v1/annotation-tasks/{task.task_id}"
        before = service.status(task.task_id, 1, auth)
        assert client.get(path + "/revisions/1/file").json()["status"] == "PENDING"
        assert (
            client.get(path + "/annotation-file?revision=1&allow_unreviewed=true").status_code
            == 409
        )
        assert service.status(task.task_id, 1, auth) == before  # GET does not schedule.
        drain(source, service)
        response = client.get(path + "/annotation-file?revision=1&allow_unreviewed=true")
        assert response.status_code == 200
        assert response.headers["x-annotation-review-status"] == "unreviewed"
        assert response.headers["cache-control"] == "no-store"
        assert hashlib.sha256(response.content).hexdigest() == response.headers["x-content-sha256"]
        current["auth"] = replace(
            auth,
            capabilities=frozenset(),
            organization_scoped_capabilities=frozenset(
                (source.identity.organization_id, task.project_id, cap) for cap in auth.capabilities
            ),
        )
        assert client.get(path + "/revisions/1/file").status_code == 200
        assert (
            client.get(path + "/annotation-file?revision=1&allow_unreviewed=true").status_code
            == 200
        )
        current["auth"] = replace(auth, capabilities=frozenset({"annotation_task.read"}))
        assert (
            client.get(path + "/annotation-file?revision=1&allow_unreviewed=true").status_code
            == 403
        )
        assert (
            client.post(path + "/revisions/1/file", json={"expected_generation": 1}).status_code
            == 403
        )
        current["auth"] = replace(
            auth,
            organization_ids=frozenset({"foreign"}),
            organization_scope_triples=frozenset({("foreign", task.project_id, task.region_code)}),
        )
        assert client.get(path + "/revisions/1/file").status_code == 403
        current["auth"] = auth
        row = service.repository.get(task.task_id, 1)
        # This is a uniquely scoped fixture object, never a business recording.
        service.storage._client.put_object(
            Bucket=service.storage._bucket, Key=row["object_key"], Body=b"corrupt"
        )
        before = service.status(task.task_id, 1, auth)
        bad = client.get(path + "/annotation-file?revision=1&allow_unreviewed=true")
        assert bad.status_code == 409 and bad.json()["code"] == "ANNOTATION_FILE_INTEGRITY_FAILED"
        assert service.status(task.task_id, 1, auth) == before
        assert (
            client.post(path + "/revisions/1/file", json={"expected_generation": 1}).status_code
            == 200
        )
        drain(source, service)
        repaired = client.get(path + "/annotation-file?revision=1&allow_unreviewed=true")
        assert repaired.content == response.content
        assert service.repository.get(task.task_id, 1)["object_key"] != row["object_key"]


@pytest.mark.integration
def test_duplicate_workers_and_database_immutability(files):
    source, task, service, auth = files
    handler = AnnotationRevisionFileOutboxHandler(service)
    with scoped(source):
        event = event_for(service, task, 1)

    def consume(_):
        with scoped(source):
            try:
                handler(event)
            except SnapshotBusy:
                return "busy"
            return "consumed"

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(consume, range(4)))
    assert "consumed" in outcomes
    with scoped(source):
        assert service.status(task.task_id, 1, auth).status == "READY"
        assert service.status(task.task_id, 1, auth).attempts == 1
        for command in (
            "DELETE FROM annotation.revision_files WHERE task_id=%s AND revision=1",
            "UPDATE annotation.revision_files SET content_sha256=repeat('e',64) "
            "WHERE task_id=%s AND revision=1",
            "UPDATE annotation.revision_files SET generation=generation+2,status='PENDING' "
            "WHERE task_id=%s AND revision=1",
            "UPDATE annotation.revision_files SET organization_id='foreign' "
            "WHERE task_id=%s AND revision=1",
        ):
            with pytest.raises(psycopg.Error), service.repository.transaction() as cursor:
                cursor.execute(command, (task.task_id,))
        assert service.status(task.task_id, 1, auth).status == "READY"

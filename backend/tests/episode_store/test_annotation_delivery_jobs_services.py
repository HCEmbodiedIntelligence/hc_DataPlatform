"""Real isolated PG/outbox/MinIO, with synthetic RGB-D fault inputs (not full P0)."""

import asyncio
import hashlib
import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.annotation.delivery import (
    AnnotationDeliveryOutboxHandler,
    AnnotationDeliveryService,
)
from hc_data_platform.annotation.delivery_models import RequestAnnotationDelivery
from hc_data_platform.annotation.delivery_repository import (
    DeliveryLeaseLost,
    PostgresAnnotationDeliveries,
)
from hc_data_platform.annotation.models import ReviewDecision
from hc_data_platform.annotation.revision_files import (
    AnnotationRevisionFileOutboxHandler,
    SnapshotBusy,
)
from hc_data_platform.annotation.router import (
    get_annotation_auth,
    get_annotation_delivery_service,
    router,
)
from hc_data_platform.annotation.subtasks import SubtaskTrack
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.outbox import OutboxDispatcher, PostgresOutboxDeliveryRepository
from tests.episode_store.test_annotation_bindings_services import actor
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_depth_window import forbid_quantization


@pytest.fixture
def delivery(files, target):  # noqa: F811
    source, task, snapshots, auth = files
    service = AnnotationDeliveryService(
        snapshots, target[0], PostgresAnnotationDeliveries(target[0].repository.connections)
    )
    with scoped(source):
        state = snapshots.status(task.task_id, 1, auth)
        snapshots.generate(task.task_id, 1, state.generation, state.revision_content_hash, auth)
        assert snapshots.status(task.task_id, 1, auth).status == "READY"
    auth = replace(auth, capabilities=auth.capabilities | {"export.create"})
    return source, task, service, auth


def selection(**changes):
    return RequestAnnotationDelivery(
        revision=1,
        allow_unreviewed=True,
        track=SubtaskTrack(track_id="task-segments", label="任务分段", annotation_ids=("parent",)),
        **changes,
    )


def event_for(service, job):
    with service.repository.transaction() as cursor:
        cursor.execute(
            """SELECT envelope FROM core.outbox_events WHERE event_type = %s
            AND envelope->'payload'->>'job_id' = %s
            AND envelope->'payload'->>'generation' = %s""",
            (AnnotationDeliveryOutboxHandler.EVENT_TYPE, job.job_id, str(job.generation)),
        )
        return DomainEventEnvelope.model_validate(cursor.fetchone()[0])


def dispatch(source, service):
    delivery = AnnotationDeliveryOutboxHandler(service)
    files_handler = AnnotationRevisionFileOutboxHandler(service.files)
    dispatcher = OutboxDispatcher(
        PostgresOutboxDeliveryRepository(service.repository.connections),
        {delivery.EVENT_TYPE: delivery, files_handler.EVENT_TYPE: files_handler},
        worker_id="delivery-test",
    )
    for _ in range(30):
        if not asyncio.run(
            dispatcher.dispatch_one(
                organization_id=source.identity.organization_id,
                project_id=source.identity.project_id,
                region_code=source.identity.region_code,
            )
        ):
            return
    pytest.fail("scoped outbox did not drain")


@pytest.mark.integration
def test_approved_delivery_freezes_revision_and_reuses_verified_archive(
    delivery, monkeypatch, tmp_path
):
    source, task, service, auth = delivery
    annotation = service.files.annotation
    with scoped(source):
        history = annotation.get_history(task.task_id, auth)
        submission = annotation.submit_for_review(
            task.task_id,
            actor(source.identity),
            expected_revision=1,
            if_match=history.task.etag,
            idempotency_key="approve",
        )
        annotation.review(
            task.task_id,
            actor(source.identity, "reviewer"),
            ReviewDecision.APPROVE,
            revision=1,
            submission_id=submission.submission_id,
            if_match=annotation.get_task(task.task_id).etag,
        )
        request = RequestAnnotationDelivery(track=selection().track)
        job = service.request(task.task_id, request, "frozen-default", auth)
        assert job.status == "PENDING" and job.plan.review.status == "APPROVED"
        assert job.plan.binding.revision == 1
        # New saved draft must not replace the frozen approval or download's revision.
        annotation.save_draft(
            task.task_id,
            actor(source.identity),
            (),
            tags=(),
            expected_revision=1,
            if_match=annotation.get_task(task.task_id).etag,
            client_mutation_id="new-draft",
        )
        forbid_quantization(monkeypatch)
        dispatch(source, service)
        ready = service.status(task.task_id, job.job_id, auth)
        assert ready.status == "READY", ready
        assert ready.plan == job.plan
        row = service.repository.get_job(task.task_id, job.job_id)
        archive_bytes = b"".join(service.storage.read_chunks(row["artifact_key"]))
        assert hashlib.sha256(archive_bytes).hexdigest() == ready.artifact_sha256
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            doc = json.loads(archive.read("episode/hc/annotation.json"))
            review = json.loads(archive.read("episode/hc/annotation-review.json"))
            assert doc["revision"]["revision"] == 1 and len(doc["revision"]["tags"]) == 2
            assert review["approval"]["review_id"] == job.plan.review.review_id
            package = service.packages.files.open(
                task.data_ref.identity, task.data_ref.data_version_id
            )
            for entry in package.manifest.files:
                if entry.path.startswith(("videos/", "hc/depth/")):
                    assert (
                        hashlib.sha256(archive.read("episode/" + entry.path)).hexdigest()
                        == entry.sha256
                    )
            evidence = (
                Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", tmp_path))
                / "annotation-delivery-jobs"
                / job.job_id
            )
            evidence.mkdir(parents=True)
            archive.extractall(evidence)  # Trusted writer-produced inventory, not user upload.
        (evidence / "job.json").write_text(ready.model_dump_json(indent=2))
        (evidence / "evidence.json").write_text(
            json.dumps(
                {
                    "synthetic_input": True,
                    "real_postgres_minio_outbox": True,
                    "browser_verified": False,
                    "new_draft_revision": 2,
                    "delivered_revision": 1,
                    "media_requantized": False,
                }
            )
        )
        monkeypatch.setattr(
            "hc_data_platform.annotation.delivery.write_annotated_delivery",
            lambda *a, **k: pytest.fail("cache hit must not rebuild"),
        )
        assert service.request(task.task_id, request, "frozen-default", auth) == ready
        grant = service.download(task.task_id, job.job_id, auth)
        assert grant.artifact_sha256 == ready.artifact_sha256 and grant.expires_seconds == 300
        assert len(service.list(task.task_id, auth)) == 1
        with pytest.raises(ProblemException, match="IDEMPOTENCY_CONFLICT"):
            service.request(task.task_id, selection(), "frozen-default", auth)
        # A correct filename/length is insufficient: verification must hash the stored bytes.
        read = service.storage.read_chunks

        def corrupt(key, *args, **kwargs):
            for i, chunk in enumerate(read(key, *args, **kwargs)):
                yield (
                    bytes([chunk[0] ^ 1]) + chunk[1:]
                    if key == row["artifact_key"] and i == 0
                    else chunk
                )

        monkeypatch.setattr(service.storage, "read_chunks", corrupt)
        with pytest.raises(ProblemException, match="INTEGRITY_FAILED"):
            service.download(task.task_id, job.job_id, auth)
        with pytest.raises(ProblemException, match="INTEGRITY_FAILED"):
            service.request(task.task_id, request, "frozen-default", auth)


@pytest.mark.integration
def test_concurrent_commands_cancel_retry_and_stale_worker(delivery, monkeypatch):
    source, task, service, auth = delivery

    def request(_):
        with scoped(source):
            return service.request(task.task_id, selection(), "same-command", auth)

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(request, range(4)))
    assert len({j.job_id for j in jobs}) == 1
    job = jobs[0]
    with scoped(source):
        old = service.repository.claim_delivery(task.task_id, job.job_id, 1)
        with pytest.raises(SnapshotBusy):
            service.repository.claim_delivery(task.task_id, job.job_id, 1)
        cancelled = service.command(task.task_id, job.job_id, 1, auth, retry=False)
        assert cancelled.status == "CANCELLED"
        with pytest.raises(DeliveryLeaseLost):
            service.repository.finish_delivery(old)

    def retry(_):
        with scoped(source):
            try:
                return service.command(task.task_id, job.job_id, 1, auth, retry=True).generation
            except ProblemException as exc:
                assert exc.problem.code == "ANNOTATION_DELIVERY_GENERATION_CONFLICT"
                return "conflict"

    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(retry, range(4)))
    assert result.count(2) == 1 and result.count("conflict") == 3
    with scoped(source):
        assert service.repository.claim_delivery(task.task_id, job.job_id, 1) is None
        forbid_quantization(monkeypatch)
        dispatch(source, service)
        assert service.status(task.task_id, job.job_id, auth).status == "READY"
        assert service.files.annotation.get_task(task.task_id).current_revision == 1


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["upload_failure", "crash_after_upload", "lease_expired"])
def test_upload_interruption_retry_and_recovery(delivery, monkeypatch, failure):
    source, task, service, auth = delivery

    class WorkerCrash(BaseException):
        pass

    upload = service._upload

    def interrupt(*args):
        if failure in {"crash_after_upload", "lease_expired"}:
            upload(*args)
            if failure == "crash_after_upload":
                raise WorkerCrash()
            with service.repository.transaction() as cursor:
                cursor.execute(
                    "UPDATE annotation.delivery_jobs "
                    "SET lease_expires_at = now() - interval '1 second' "
                    "WHERE job_id = %s",
                    (args[1]["job_id"],),
                )
            return
        raise OSError("private adapter error must never leave the worker")

    with scoped(source):
        job = service.request(task.task_id, selection(), failure, auth)
        handler = AnnotationDeliveryOutboxHandler(service)
        event = event_for(service, job)
        monkeypatch.setattr(service, "_upload", interrupt)
        if failure in {"crash_after_upload", "lease_expired"}:
            with pytest.raises(WorkerCrash if failure == "crash_after_upload" else SnapshotBusy):
                asyncio.run(handler(event))
            old = service.repository.get_job(task.task_id, job.job_id)
            assert old["status"] == "RUNNING" and old["artifact_sha256"]
            with service.repository.transaction() as cursor:
                cursor.execute(
                    "UPDATE annotation.delivery_jobs "
                    "SET lease_expires_at = now() - interval '1 second' "
                    "WHERE job_id = %s",
                    (job.job_id,),
                )
            monkeypatch.setattr(
                "hc_data_platform.annotation.delivery.write_annotated_delivery",
                lambda *a, **k: pytest.fail("completed archive must be reused after worker crash"),
            )
        else:
            asyncio.run(handler(event))
            failed = service.status(task.task_id, job.job_id, auth)
            assert (
                failed.status == "FAILED"
                and failed.error_code == "ANNOTATION_DELIVERY_PACKAGING_FAILED"
            )
            job = service.command(task.task_id, job.job_id, 1, auth, retry=True)
            event = event_for(service, job)
        monkeypatch.setattr(service, "_upload", upload)
        forbid_quantization(monkeypatch)
        asyncio.run(handler(event))
        ready = service.status(task.task_id, job.job_id, auth)
        assert ready.status == "READY" and ready.attempts == 2
        asyncio.run(handler(event))
        assert service.status(task.task_id, job.job_id, auth) == ready


@pytest.mark.integration
def test_http_selection_tenant_and_event_authorization(delivery):
    source, task, service, auth = delivery
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_annotation_delivery_service] = lambda: service
    current = {"auth": auth}
    app.dependency_overrides[get_annotation_auth] = lambda: current["auth"]

    @app.exception_handler(ProblemException)
    async def error(_: Request, exc: ProblemException):
        return JSONResponse(
            status_code=exc.problem.status, content=exc.problem.model_dump(mode="json")
        )

    with scoped(source), TestClient(app) as client:
        path = f"/api/v1/annotation-tasks/{task.task_id}/deliveries"
        assert client.get(path).json() == []
        response = client.post(
            path,
            json={"track": selection().track.model_dump(mode="json")},
            headers={"Idempotency-Key": "no-approval"},
        )
        assert response.status_code == 409
        body = selection().model_dump(mode="json")
        body["track"]["annotation_ids"] = ["parent", "child"]
        response = client.post(path, json=body, headers={"Idempotency-Key": "overlap"})
        assert response.status_code == 422
        assert set(response.json()["details"]["annotation_ids"]) == {"parent", "child"}
        body = selection().model_dump(mode="json")
        current["auth"] = replace(auth, capabilities=auth.capabilities - {"export.create"})
        assert (
            client.post(path, json=body, headers={"Idempotency-Key": "denied"}).status_code == 403
        )
        current["auth"] = auth
        response = client.post(path, json=body, headers={"Idempotency-Key": "ok"})
        assert response.status_code == 200, response.text
        job = service.status(task.task_id, response.json()["job_id"], auth)
        assert response.headers["cache-control"] == "no-store"
        job_path = path + "/" + job.job_id
        assert client.post(job_path + "/download").status_code == 409
        current["auth"] = replace(
            auth,
            organization_ids=frozenset({"other"}),
            organization_scope_triples=frozenset(
                {("other", source.identity.project_id, source.identity.region_code)}
            ),
        )
        assert client.get(job_path).status_code == 403
        assert client.post(job_path + "/download").status_code == 403
        current["auth"] = auth
        assert (
            client.get(path.replace(task.task_id, "unknown-task") + "/" + job.job_id).status_code
            == 404
        )
        handler, event = AnnotationDeliveryOutboxHandler(service), event_for(service, job)
        forged = event.model_copy(update={"payload": {**event.payload, "generation": 999}})
        with pytest.raises(ValueError, match="persisted"):
            asyncio.run(handler(forged))
        assert service.status(task.task_id, job.job_id, auth).status == "PENDING"
        evidence_root = os.getenv("HC_G1_WRITER_EVIDENCE_ROOT")
        if evidence_root:
            (Path(evidence_root) / "annotation-delivery-pending.json").write_text(
                job.model_dump_json(indent=2)
            )

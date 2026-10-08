"""Collection jobs use real scoped SQL/MinIO and the shared persisted outbox."""

import hashlib
import io
import json
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
import pytest

from hc_data_platform.annotation.delivery import AnnotationDeliveryService
from hc_data_platform.annotation.delivery_repository import PostgresAnnotationDeliveries
from hc_data_platform.annotation.revision_files import SnapshotBusy
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.collection_delivery_models import RequestCollectionDelivery
from hc_data_platform.episode_store.postgres import PackageConflict
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_collection_export_services import pair  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_complete_delivery_services import run_job
from tests.episode_store.test_depth_window import forbid_quantization
from tests.episode_store.test_detail_services import auth_for
from tests.episode_store.test_http_services import register_and_grant


@pytest.fixture
def queued(pair, target):  # noqa: F811
    source, first, second = pair
    auth = auth_for(first.receipt.data)
    auth = replace(auth, capabilities=auth.capabilities | {"export.create"})
    body = RequestCollectionDelivery.model_validate(
        {
            "modalities": "rgbd",
            "members": [
                {"data": first.receipt.data},
                {"data": second.receipt.data},
            ],
        }
    )
    return target[0].deliveries, source, body, auth


@pytest.mark.integration
@pytest.mark.parametrize("modalities", ["rgb", "rgbd"])
def test_collection_job_freezes_outputs_reuses_archive_and_preserves_existing_jobs(
    queued, tmp_path, monkeypatch, modalities
):
    service, source, body, auth = queued
    body = body.model_copy(update={"modalities": modalities})
    with scoped(source):
        forbid_quantization(monkeypatch)
        job = service.request_collection(body, "fixed-selection", auth)
        assert job.status == "PENDING" and job.total_files is None and job.total_bytes is None
        assert job.plan.modalities == modalities
        assert len(service.collection.sources(job.plan)) == 2
        run_job(service, job)
        ready = service.status(job.job_id, auth)
        assert ready.status == "READY", ready
        assert ready.processed_files == ready.total_files > 0
        assert ready.processed_bytes == ready.total_bytes > 0
        assert ready.uploaded_bytes == ready.size_bytes
        row = service.repository.get_job(job.job_id)
        archive = b"".join(service.storage.read_chunks(row["artifact_key"]))
        assert hashlib.sha256(archive).hexdigest() == ready.artifact_sha256
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            manifest = json.loads(z.read("collection/hc/collection_manifest.json"))
            assert json.loads(z.read("delivery.json"))["is_reassembled_collection"] is True
            for entry in manifest["files"]:
                assert (
                    hashlib.sha256(z.read("collection/" + entry["path"])).hexdigest()
                    == entry["sha256"]
                )
            assert all(
                bool(m["excluded_depth_features"]) == (modalities == "rgb")
                for m in manifest["members"]
            )
        with monkeypatch.context() as guard:
            guard.setattr(
                service.collection,
                "build",
                lambda *a, **k: pytest.fail("cached collection rebuilt"),
            )
            assert service.request_collection(body, "fixed-selection", auth) == ready
            reordered = body.model_copy(update={"members": tuple(reversed(body.members))})
            assert service.request_collection(reordered, "same-content-another-key", auth) == ready
            run_job(service, job)
        assert service.list(auth, kind="collection") == (ready,)
        assert service.list(auth, kind="independent") == ()
        assert service.download(job.job_id, auth).artifact_sha256 == ready.artifact_sha256
        with pytest.raises(ProblemException, match="IDEMPOTENCY_CONFLICT"):
            service.request_collection(
                body.model_copy(update={"modalities": "rgb" if modalities == "rgbd" else "rgbd"}),
                "fixed-selection",
                auth,
            )
        with (
            pytest.raises(psycopg.errors.CheckViolation),
            service.repository.transaction() as cursor,
        ):
            cursor.execute(
                "UPDATE episode_store.delivery_jobs SET total_bytes=total_bytes+1 WHERE job_id=%s",
                (job.job_id,),
            )
        evidence = (
            Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            / "collection-deliveries"
            / job.job_id
        )
        evidence.mkdir(parents=True)
        (evidence / "delivery.zip").write_bytes(archive)
        (evidence / "job.json").write_text(ready.model_dump_json(indent=2))


@pytest.mark.integration
def test_normal_http_collection_worker_download_and_revocation(queued, tmp_path):
    service, source, body, _ = queued
    ident = source.identity
    with (
        scoped(source),
        httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as client,
    ):
        principal, headers = register_and_grant(
            client,
            service.repository,
            ident,
            capabilities=["dataset_version.read", "export.create", "export.download"],
        )
        base = f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}/episode-deliveries"
        try:
            candidates = client.get(
                base + "/candidates", headers=headers, params={"dataset_id": ident.dataset_id}
            )
            assert candidates.status_code == 200, candidates.text
            assert candidates.json()["items"][0] == body.members[0].data.model_dump(mode="json")
            response = client.post(
                base + "/collections",
                headers={**headers, "Idempotency-Key": "ordinary-collection"},
                json=body.model_dump(mode="json"),
            )
            assert response.status_code == 200, response.text
            job = response.json()
            deadline = time.monotonic() + 45
            while job["status"] in {"PENDING", "RUNNING"} and time.monotonic() < deadline:
                time.sleep(0.1)
                response = client.get(base + "/" + job["job_id"], headers=headers)
                assert response.status_code == 200, response.text
                job = response.json()
            assert job["status"] == "READY", job
            endpoint = base + "/" + job["job_id"] + "/download"
            response = client.post(endpoint, headers=headers)
            assert response.status_code == 200, response.text
            grant = response.json()
            url = urlsplit(grant["url"])
            artifact = client.get(
                urlunsplit((url.scheme, "object-store-browser:9000", url.path, url.query, "")),
                headers={"Host": url.netloc},
            )
            assert artifact.status_code == 200
            assert hashlib.sha256(artifact.content).hexdigest() == grant["artifact_sha256"]
            with service.repository.transaction() as cursor:
                cursor.execute(
                    "UPDATE access_control.capability_grants SET active=false "
                    "WHERE principal_id=%s AND capability_key='export.download'",
                    (principal,),
                )
            assert client.post(endpoint, headers=headers).status_code == 403
            evidence = (
                Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
                / "collection-delivery-http"
            )
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "delivery.zip").write_bytes(artifact.content)
            (evidence / "result.json").write_text(
                json.dumps(
                    {
                        "job": job,
                        "ordinary_session": True,
                        "persistent_worker": True,
                        "archive_hash_verified": True,
                        "revoked_cached_download_denied": True,
                        "synthetic_input": True,
                        "human_acceptance": False,
                    },
                    indent=2,
                )
            )
        finally:
            assert client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204


@pytest.mark.integration
def test_collection_request_concurrency_and_retry_keep_frozen_plan(queued, monkeypatch):
    service, source, body, auth = queued

    def request(_):
        with scoped(source):
            return service.request_collection(body, "simultaneous", auth)

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(request, range(4)))
    assert len({j.job_id for j in jobs}) == 1
    job = jobs[0]
    original_upload = service._upload
    with scoped(source):
        monkeypatch.setattr(
            service, "_upload", lambda *a, **k: (_ for _ in ()).throw(OSError("private failure"))
        )
        run_job(service, job)
        failed = service.status(job.job_id, auth)
        assert failed.status == "FAILED" and failed.total_files > 0
        monkeypatch.setattr(service, "_upload", original_upload)
        retry = service.command(job.job_id, 1, auth, retry=True)
        assert retry.plan == failed.plan and retry.generation == 2
        run_job(service, retry)
        ready = service.status(job.job_id, auth)
        assert ready.status == "READY" and ready.artifact_sha256 == failed.artifact_sha256
        assert ready.total_bytes == failed.total_bytes
        with pytest.raises(ProblemException):
            service.download(
                job.job_id, replace(auth, capabilities=auth.capabilities - {"export.download"})
            )
        changed = body.model_copy(
            update={
                "members": (
                    body.members[0].model_copy(
                        update={
                            "data": body.members[0].data.model_copy(
                                update={"content_hash": "f" * 64}
                            )
                        }
                    ),
                    body.members[1],
                )
            }
        )
        with pytest.raises(PackageConflict):
            service.request_collection(changed, "bad-reference", auth)


@pytest.mark.integration
def test_collection_binds_annotation_and_rechecks_permissions_on_cached_archive(
    queued,
    target,
    files,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    service, source, body, auth = queued
    _, task, revision_files, annotation_auth = files
    service.annotations = AnnotationDeliveryService(
        revision_files, target[0], PostgresAnnotationDeliveries(target[0].repository.connections)
    ).selections
    with scoped(source):
        for _ in range(100):
            state = revision_files.status(task.task_id, 1, annotation_auth)
            if state.status == "READY":
                break
            try:
                revision_files.generate(
                    task.task_id, 1, state.generation, state.revision_content_hash, annotation_auth
                )
            except SnapshotBusy:
                time.sleep(0.1)
        raw = body.model_dump(mode="json")
        raw["members"][0]["annotation"] = {
            "task_id": task.task_id,
            "revision": 1,
            "allow_unreviewed": True,
            "track": {"track_id": "explicit", "label": "任务分段", "annotation_ids": ["parent"]},
        }
        request = RequestCollectionDelivery.model_validate(raw)
        job = service.request_collection(request, "annotated", auth)
        binding = next(m.annotation for m in job.plan.members if m.annotation is not None)
        assert binding.binding.data == request.members[0].data
        assert binding.review.status == "UNREVIEWED"
        run_job(service, job)
        ready = service.status(job.job_id, auth)
        assert ready.status == "READY", ready
        revoked = replace(auth, capabilities=auth.capabilities - {"annotation_task.read"})
        assert service.list(revoked, kind="collection") == ()
        for action in (
            lambda: service.request_collection(request, "annotated", revoked),
            lambda: service.status(job.job_id, revoked),
            lambda: service.download(job.job_id, revoked),
        ):
            with pytest.raises(ProblemException) as error:
                action()
            assert error.value.problem.status == 403
        # Retrying an ambiguous response must use the already frozen revision/approval.
        monkeypatch.setattr(
            service.collection,
            "freeze",
            lambda *a, **k: pytest.fail("idempotent collection re-froze annotations"),
        )
        assert service.request_collection(request, "annotated", auth) == ready
        row = service.repository.get_job(job.job_id)
        evidence = (
            Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            / "collection-deliveries"
            / job.job_id
        )
        evidence.mkdir(parents=True)
        (evidence / "delivery.zip").write_bytes(
            b"".join(service.storage.read_chunks(row["artifact_key"]))
        )
        (evidence / "job.json").write_text(ready.model_dump_json(indent=2))

"""Actual isolated services/outbox; RGB-D inputs are software fixtures, not sensor acceptance."""

import asyncio
import hashlib
import io
import json
import os
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest

from hc_data_platform.core.artifact_tasks import DeliveryLeaseLost
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.episode_store.delivery import EpisodeDeliveryOutboxHandler
from hc_data_platform.episode_store.delivery_models import RequestEpisodeDelivery
from hc_data_platform.episode_store.delivery_repository import EpisodeDeliveryBusy
from hc_data_platform.episode_store.postgres import PackageConflict
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_depth_window import forbid_quantization
from tests.episode_store.test_detail_services import auth_for
from tests.episode_store.test_http_services import register_and_grant


@pytest.fixture
def complete(target):
    packages, source, _, _, request = target
    auth = auth_for(request.data_ref)
    return (
        packages.deliveries,
        source,
        request.data_ref,
        replace(auth, capabilities=auth.capabilities | {"export.create"}),
    )


def event_for(service, job):
    with service.repository.transaction() as cursor:
        cursor.execute(
            "SELECT envelope FROM core.outbox_events WHERE event_type=%s "
            "AND envelope->'payload'->>'job_id'=%s AND envelope->'payload'->>'generation'=%s",
            (EpisodeDeliveryOutboxHandler.EVENT_TYPE, job.job_id, str(job.generation)),
        )
        rows = cursor.fetchall()
        assert len(rows) == 1
        return DomainEventEnvelope.model_validate(rows[0][0])


def run_job(service, job):
    asyncio.run(EpisodeDeliveryOutboxHandler(service)(event_for(service, job)))


def clone_package(service, source, data, directory):
    package = service.packages.files.open(source.identity, data.data_version_id)
    other_source = source.model_copy(
        update={
            "identity": source.identity.model_copy(
                update={"episode_id": "another-episode", "rollout_id": "another-rollout"}
            )
        }
    )
    for entry in package.manifest.files:
        dest = directory / entry.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        with package.file(entry.path) as path:
            shutil.copyfile(path, dest)
    origin = directory / "hc/source.json"
    doc = json.loads(origin.read_text())
    doc["identity"] = other_source.identity.model_dump(mode="json")
    origin.write_text(json.dumps(doc))
    return service.packages.committer.commit(directory, other_source).data


@pytest.mark.integration
def test_complete_and_batch_preserve_every_file_and_reuse_without_conversion(
    complete, tmp_path, monkeypatch
):
    service, source, data, auth = complete
    with scoped(source):
        second = clone_package(service, source, data, tmp_path / "second")
        forbid_quantization(monkeypatch)
        for refs, mode in [
            ((data,), "independent_episode_zip/1"),
            ((data, second), "independent_episode_batch_zip/1"),
        ]:
            body = RequestEpisodeDelivery(data=refs, archive_format=mode)
            job = service.request(body, mode, auth)
            assert job.status == "PENDING"
            run_job(service, job)
            ready = service.status(job.job_id, auth)
            assert ready.status == "READY", ready
            assert (
                ready.processed_files == ready.total_files
                and ready.processed_bytes == ready.total_bytes
            )
            assert ready.uploaded_bytes == ready.size_bytes
            row = service.repository.get_job(job.job_id)
            content = b"".join(service.storage.read_chunks(row["artifact_key"]))
            assert hashlib.sha256(content).hexdigest() == ready.artifact_sha256
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                inventory = json.loads(archive.read("delivery.json"))
                assert inventory["plan"]["archive_format"] == mode
                assert not inventory["is_reassembled_collection"]
                for member in ready.plan.members:
                    package = service.packages.files.open(
                        member.data.identity, member.data.data_version_id
                    )
                    for entry in package.manifest.files:
                        blob = archive.read(f"{member.root}/{entry.path}")
                        assert (
                            len(blob) == entry.size
                            and hashlib.sha256(blob).hexdigest() == entry.sha256
                        )
                    assert json.loads(archive.read(f"{member.root}/hc/commit.json"))[
                        "data"
                    ] == member.data.model_dump(mode="json")
            with monkeypatch.context() as guard:
                guard.setattr(
                    "hc_data_platform.episode_store.delivery.archive_packages",
                    lambda *a, **k: pytest.fail("cached delivery rebuilt"),
                )
                assert (
                    service.request(
                        body.model_copy(update={"data": tuple(reversed(refs))}),
                        "same-selection-new-key-" + mode,
                        auth,
                    )
                    == ready
                )
                run_job(service, job)
                assert service.download(job.job_id, auth).artifact_sha256 == ready.artifact_sha256
            evidence = (
                Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", tmp_path))
                / "complete-deliveries"
                / job.job_id
            )
            evidence.mkdir(parents=True)
            (evidence / "delivery.zip").write_bytes(content)
            (evidence / "job.json").write_text(ready.model_dump_json(indent=2))
        with pytest.raises(ProblemException, match="IDEMPOTENCY_CONFLICT"):
            service.request(
                RequestEpisodeDelivery(data=(second,)), "independent_episode_zip/1", auth
            )


@pytest.mark.integration
def test_concurrent_commands_cancel_retry_and_expired_worker_fence(complete):
    service, source, data, auth = complete
    body = RequestEpisodeDelivery(data=(data,))

    def create(_):
        with scoped(source):
            return service.request(body, "same-command", auth)

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(create, range(4)))
    assert len({j.job_id for j in jobs}) == 1
    job = jobs[0]
    with scoped(source):
        stale = service.repository.claim(job.job_id, 1)
        with pytest.raises(EpisodeDeliveryBusy):
            service.repository.claim(job.job_id, 1)
        assert service.command(job.job_id, 1, auth, retry=False).status == "CANCELLED"
        with pytest.raises(DeliveryLeaseLost):
            service.repository.finish(stale)

    def retry(_):
        with scoped(source):
            try:
                return service.command(job.job_id, 1, auth, retry=True).generation
            except ProblemException as exc:
                assert exc.problem.code == "EPISODE_DELIVERY_GENERATION_CONFLICT"
                return "conflict"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(retry, range(4)))
    assert results.count(2) == 1 and results.count("conflict") == 3
    with scoped(source):
        assert service.repository.claim(job.job_id, 1) is None
        current = service.status(job.job_id, auth)
        run_job(service, current)
        assert service.status(job.job_id, auth).status == "READY"


@pytest.mark.integration
@pytest.mark.parametrize("fault", ["upload", "after_upload", "expired_lease"])
def test_upload_interruption_recovers_exact_archive(complete, monkeypatch, fault):
    service, source, data, auth = complete

    class Crash(BaseException):
        pass

    original = service._upload

    def failed_upload(path, row, checkpoint):
        if fault == "upload":
            raise OSError("private storage error must not leak")
        original(path, row, checkpoint)
        if fault == "after_upload":
            raise Crash()
        with service.repository.transaction() as cursor:
            cursor.execute(
                "UPDATE episode_store.delivery_jobs "
                "SET lease_expires_at=now()-interval '1 second' WHERE job_id=%s",
                (row["job_id"],),
            )

    with scoped(source):
        job = service.request(RequestEpisodeDelivery(data=(data,)), fault, auth)
        monkeypatch.setattr(service, "_upload", failed_upload)
        if fault == "upload":
            run_job(service, job)
            failed = service.status(job.job_id, auth)
            assert (
                failed.status == "FAILED"
                and failed.error_code == "EPISODE_DELIVERY_PACKAGING_FAILED"
            )
            job = service.command(job.job_id, 1, auth, retry=True)
        else:
            with pytest.raises(Crash if fault == "after_upload" else EpisodeDeliveryBusy):
                run_job(service, job)
            assert service.status(job.job_id, auth).status == "RUNNING"
            with service.repository.transaction() as cursor:
                cursor.execute(
                    "UPDATE episode_store.delivery_jobs "
                    "SET lease_expires_at=now()-interval '1 second' WHERE job_id=%s",
                    (job.job_id,),
                )
            monkeypatch.setattr(
                "hc_data_platform.episode_store.delivery.archive_packages",
                lambda *a, **k: pytest.fail("uploaded archive must be reused"),
            )
        monkeypatch.setattr(service, "_upload", original)
        run_job(service, job)
        ready = service.status(job.job_id, auth)
        assert ready.status == "READY" and ready.attempts == 2


@pytest.mark.integration
def test_plan_event_scope_and_ready_integrity_fail_closed(complete, monkeypatch):
    service, source, data, auth = complete
    with scoped(source):
        with pytest.raises(PackageConflict):
            service.request(
                RequestEpisodeDelivery(data=(data.model_copy(update={"content_hash": "f" * 64}),)),
                "forged",
                auth,
            )
        job = service.request(RequestEpisodeDelivery(data=(data,)), "valid", auth)
        event = event_for(service, job)
        with pytest.raises(ValueError):
            asyncio.run(
                EpisodeDeliveryOutboxHandler(service)(
                    event.model_copy(update={"payload": {"job_id": job.job_id, "generation": 2}})
                )
            )
        run_job(service, job)
        denied = replace(auth, capabilities=auth.capabilities - {"export.download"})
        with pytest.raises(ProblemException):
            service.download(job.job_id, denied)
        row = service.repository.get_job(job.job_id)
        read = service.storage.read_chunks

        def changed(key):
            for index, block in enumerate(read(key)):
                yield (
                    bytes([block[0] ^ 1]) + block[1:]
                    if key == row["artifact_key"] and index == 0
                    else block
                )

        monkeypatch.setattr(service.storage, "read_chunks", changed)
        with pytest.raises(PackageConflict):
            service.download(job.job_id, auth)
    foreign = source.model_copy(
        update={"identity": source.identity.model_copy(update={"organization_id": "foreign"})}
    )
    with scoped(foreign), pytest.raises(ProblemException):
        service.status(job.job_id, auth)


@pytest.mark.integration
def test_normal_http_worker_download_and_revocation(complete, tmp_path):
    service, source, data, _ = complete
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
            assert client.get(base).status_code == 401
            assert (
                client.get(base, headers={**headers, "X-Organization-Id": "foreign"}).status_code
                == 403
            )
            response = client.post(
                base,
                headers={**headers, "Idempotency-Key": "normal-http"},
                json=RequestEpisodeDelivery(data=(data,)).model_dump(mode="json"),
            )
            assert response.status_code == 200, response.text
            job = response.json()
            deadline = time.monotonic() + 30
            while job["status"] in {"PENDING", "RUNNING"} and time.monotonic() < deadline:
                time.sleep(0.1)
                response = client.get(base + "/" + job["job_id"], headers=headers)
                assert response.status_code == 200, response.text
                job = response.json()
            assert job["status"] == "READY", job
            path = base + "/" + job["job_id"] + "/download"
            grant_response = client.post(path, headers=headers)
            assert grant_response.status_code == 200, grant_response.text
            grant = grant_response.json()
            url = urlsplit(grant["url"])
            artifact = client.get(
                urlunsplit((url.scheme, "object-store-browser:9000", url.path, url.query, "")),
                headers={"Host": url.netloc},
            )
            assert artifact.status_code == 200
            assert hashlib.sha256(artifact.content).hexdigest() == grant["artifact_sha256"]
            evidence = (
                Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", tmp_path)) / "complete-delivery-http"
            )
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "delivery.zip").write_bytes(artifact.content)
            with service.repository.transaction() as cursor:
                cursor.execute(
                    "UPDATE access_control.capability_grants SET active=false "
                    "WHERE principal_id=%s AND capability_key='export.download'",
                    (principal,),
                )
            assert client.post(path, headers=headers).status_code == 403
            (evidence / "result.json").write_text(
                json.dumps(
                    {
                        "job": job,
                        "ordinary_session": True,
                        "persistent_worker": True,
                        "archive_hash_verified": True,
                        "revoked_cached_download_denied": True,
                        "real_recording": False,
                        "human_acceptance": False,
                    },
                    indent=2,
                )
            )
        finally:
            assert client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204

"""Real isolated login/gateway/API/daemon/download; synthetic data, no browser."""

import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest

from tests.episode_store.test_annotation_delivery_jobs_services import (  # noqa: F401
    delivery,
    selection,
)
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant


@pytest.mark.integration
def test_live_annotation_archive_grant_and_revocation(delivery):  # noqa: F811
    source, task, service, _ = delivery
    identity = source.identity
    with (
        scoped(source),
        httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as client,
    ):
        principal, headers = register_and_grant(
            client,
            service.repository,
            identity,
            capabilities=["annotation_task.read", "export.create", "export.download"],
        )
        path = f"/api/v1/annotation-tasks/{task.task_id}/deliveries"
        try:
            assert client.get(path).status_code == 401
            body = selection().model_dump(mode="json")
            created = client.post(
                path, json=body, headers={**headers, "Idempotency-Key": "real-http"}
            )
            assert created.status_code == 200, created.text
            job = created.json()
            job_path = path + "/" + job["job_id"]
            for _ in range(45):
                response = client.get(job_path, headers=headers)
                assert response.status_code == 200, response.text
                job = response.json()
                if job["status"] not in {"PENDING", "RUNNING"}:
                    break
                time.sleep(1)
            assert job["status"] == "READY", job
            repeat = client.post(
                path, json=body, headers={**headers, "Idempotency-Key": "real-http"}
            )
            assert repeat.status_code == 200 and repeat.json() == job
            assert client.post(job_path + "/download").status_code == 401
            response = client.post(job_path + "/download", headers=headers)
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            grant = response.json()
            assert grant["artifact_sha256"] == job["artifact_sha256"]
            url = urlsplit(grant["url"])
            internal = urlunsplit(
                (url.scheme, "object-store-browser:9000", url.path, url.query, "")
            )
            download = client.get(internal, headers={"Host": url.netloc})
            assert download.status_code == 200
            assert hashlib.sha256(download.content).hexdigest() == grant["artifact_sha256"]
            assert len(download.content) == grant["size_bytes"]
            ranged = client.get(internal, headers={"Host": url.netloc, "Range": "bytes=0-31"})
            assert ranged.status_code == 206 and ranged.content == download.content[:32]
            with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
                annotation = json.loads(archive.read("episode/hc/annotation.json"))
                assert annotation["revision"]["revision"] == 1
                manifest = json.loads(archive.read("episode/hc/delivery_manifest.json"))
                for entry in manifest["files"]:
                    member = archive.read("episode/" + entry["path"])
                    assert len(member) == entry["size"]
                    assert hashlib.sha256(member).hexdigest() == entry["sha256"]
                assert any(e["path"].endswith("validity/flags.parquet") for e in manifest["files"])
            assert (
                client.get(
                    job_path, headers={**headers, "X-Organization-Id": "foreign"}
                ).status_code
                == 403
            )
            with service.repository.transaction() as cursor:
                cursor.execute(
                    """UPDATE access_control.capability_grants SET active=false
                    WHERE principal_id=%s AND capability_key='export.download'""",
                    (principal,),
                )
            assert client.post(job_path + "/download", headers=headers).status_code == 403
            assert client.get(job_path, headers=headers).status_code == 403
            root = (
                Path(os.environ["HC_G1_WRITER_EVIDENCE_ROOT"])
                / "annotation-delivery-http"
                / job["job_id"]
            )
            root.mkdir(parents=True)
            (root / "evidence.json").write_text(
                json.dumps(
                    {
                        "synthetic_input": True,
                        "real_gateway_login_api_worker_minio": True,
                        "browser_verified": False,
                        "job": job,
                        "archive_hash_verified": True,
                        "all_members_verified": True,
                        "http_range": True,
                        "new_grant_denied_after_capability_revocation": True,
                    },
                    indent=2,
                )
            )
        finally:
            assert client.post("/api/v1/auth/session:logout", headers=headers).status_code == 204

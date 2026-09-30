"""Ordinary session -> isolated gateway/API -> persistent worker -> complete RGB-D."""

import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path

import httpx
import pytest

from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant
from tests.episode_store.test_tag_derivation_services import selected  # noqa: F401
from tests.episode_store.test_tag_jobs_services import tag_jobs  # noqa: F401
from tests.episode_store.test_tag_projection_services import projected  # noqa: F401

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "prepared_bundle",
        [{"identity": {"dataset_id": "dataset_tag_http", "episode_id": "episode_tag_parent"}}],
        indirect=True,
    ),
]


def test_ordinary_tag_command_persistent_worker_detail_download_and_revocation(tag_jobs, tmp_path):  # noqa: F811
    source, service, task_id, _ = tag_jobs
    identity = source.identity
    body = {"revision": 1, "annotation_id": "child", "allow_unreviewed": True}
    with (
        scoped(source),
        httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as client,
    ):
        principal, headers = register_and_grant(
            client,
            service.repository,
            identity,
            capabilities=[
                "upload.manage",
                "dataset_version.read",
                "annotation_task.read",
                "export.download",
            ],
        )
        base = (
            f"/api/v1/projects/{identity.project_id}/regions/{identity.region_code}"
            f"/annotation-tasks/{task_id}/tag-episodes"
        )
        assert client.get(base).status_code == 401
        response = client.post(
            base,
            headers={**headers, "Idempotency-Key": "explicit-unreviewed-required"},
            json={**body, "allow_unreviewed": False},
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "ANNOTATION_UNREVIEWED_DOWNLOAD_REQUIRED"
        response = client.post(
            base, headers={**headers, "Idempotency-Key": "ordinary-tag-command"}, json=body
        )
        assert response.status_code == 200, response.text
        job = response.json()
        assert response.headers["Cache-Control"] == "no-store"
        deadline = time.monotonic() + 50
        while job["status"] in {"PENDING", "RUNNING"} and time.monotonic() < deadline:
            time.sleep(0.1)
            response = client.get(base + "/" + job["job_id"], headers=headers)
            assert response.status_code == 200, response.text
            job = response.json()
        assert job["status"] == "READY", job
        data = job["committed_data"]
        data_base = (
            f"/api/v1/projects/{identity.project_id}/regions/{identity.region_code}"
            f"/datasets/{identity.dataset_id}/episodes/{data['identity']['episode_id']}"
            f"/data-versions/{data['data_version_id']}"
        )
        assert job["selection"]["start_timestamp_ns"] == "33333333"
        assert job["selection"]["next_parent_timestamp_ns"] is None
        response = client.get(data_base + "/overview", headers=headers)
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["quality"]["status"] == "UNKNOWN"
        assert detail["data_review_status"] == "NOT_REVIEWED"
        assert detail["source"]["document"]["derivation"]["child_approvals_inherited"] is False
        assert detail["annotation"]["task_id"] == job["annotation_task_id"]
        assert job["annotation_task_id"] != task_id
        png = client.get(
            data_base + "/depth/observation.images.head_depth/0",
            params={"representation": "png"},
            headers=headers,
        )
        assert png.status_code == 200 and png.content.startswith(b"\x89PNG"), png.text[:120]
        pixel = client.get(
            data_base + "/depth/observation.images.head_depth/0/pixel",
            params={"x": 1, "y": 1, "unit": "m"},
            headers=headers,
        )
        assert pixel.status_code == 200, pixel.text
        assert pixel.json()["valid"] and pixel.json()["value"] > 0
        download = client.get(data_base + "/download", headers=headers)
        assert download.status_code == 200, download.text[:120]
        evidence = (
            Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            / "tag-jobs-http"
            / job["job_id"]
        )
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "episode.zip").write_bytes(download.content)
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            manifest = json.loads(archive.read("episode/hc/manifest.json"))
            for entry in manifest["files"]:
                payload = archive.read("episode/" + entry["path"])
                assert (
                    len(payload) == entry["size"]
                    and hashlib.sha256(payload).hexdigest() == entry["sha256"]
                )
            for member in archive.infolist():
                relative = Path(member.filename)
                assert not relative.is_absolute() and ".." not in relative.parts
                destination = evidence / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
        assert (
            client.post(
                base, headers={**headers, "Idempotency-Key": "ordinary-tag-command"}, json=body
            ).json()
            == job
        )
        assert client.get(base, headers=headers).json() == [job]
        wrong = {**headers, "X-Organization-Id": "other-tenant"}
        assert client.get(base + "/" + job["job_id"], headers=wrong).status_code in {403, 404}
        with service.repository.transaction() as cursor:
            cursor.execute(
                "UPDATE access_control.capability_grants SET active=false "
                "WHERE principal_id=%s AND capability_key='upload.manage'",
                (principal,),
            )
        assert (
            client.post(
                base, headers={**headers, "Idempotency-Key": "ordinary-tag-command"}, json=body
            ).status_code
            == 403
        )
        assert client.get(base + "/" + job["job_id"], headers=headers).status_code == 200
        with service.repository.transaction() as cursor:
            cursor.execute(
                "UPDATE access_control.capability_grants SET active=false "
                "WHERE principal_id=%s AND capability_key='annotation_task.read'",
                (principal,),
            )
        assert client.get(base + "/" + job["job_id"], headers=headers).status_code == 403
        assert client.get(base, headers=headers).status_code == 403
        (evidence / "result.json").write_text(
            json.dumps(
                {
                    "job": job,
                    "ordinary_session": True,
                    "persistent_worker": True,
                    "complete_download_sha256": hashlib.sha256(download.content).hexdigest(),
                    "all_package_files_verified": True,
                    "official_realsense_png": True,
                    "physical_depth_m": pixel.json(),
                    "independent_child_annotation_task": True,
                    "quality": "UNKNOWN",
                    "inherited_approvals": False,
                    "revoked_cached_command_and_read_denied": True,
                    "synthetic_input": True,
                    "browser_verified": False,
                    "human_acceptance": False,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

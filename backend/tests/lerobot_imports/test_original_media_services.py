"""Actual ordinary upload and raw media contract, including untouched depth bytes."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_http_services import register_and_grant
from tests.episode_store.test_writer import identity
from tests.lerobot_imports.native_package_fixture import make_native_rgbd

pytest_plugins = ["tests.episode_store.test_commit_services"]


@pytest.mark.integration
def test_raw_depth_is_download_only_and_original_bytes_remain_available(services, tmp_path):
    repository, _ = services
    ident = identity().model_copy(update={"organization_id": "raw-media-" + uuid4().hex})
    root = make_native_rgbd(tmp_path / "source")
    files = {str(p.relative_to(root)): p for p in root.rglob("*") if p.is_file()}
    manifest = dict(
        dataset_id=ident.dataset_id,
        processing_mode="STORE_ONLY",
        info=json.loads((root / "meta/info.json").read_bytes()),
        files=[
            dict(path=name, size=path.stat().st_size, part_count=1) for name, path in files.items()
        ],
    )
    with (
        scoped(SimpleNamespace(identity=ident)),
        httpx.Client(base_url="http://gateway:8080", trust_env=False, timeout=90) as client,
    ):
        principal, headers = register_and_grant(
            client,
            repository,
            ident,
            capabilities=["upload.read", "upload.manage", "dataset.create"],
        )
        base = f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}/lerobot-imports"
        eligibility = client.post(
            base + ":capabilities", headers=headers, json={"info": manifest["info"]}
        )
        assert eligibility.status_code == 200, eligibility.text
        assert eligibility.json()["processing_supported"] is True
        assert eligibility.json()["profile_id"] == "native-generic-v3"
        assert eligibility.headers["Cache-Control"] == "no-store"
        assert (
            client.post(base + ":capabilities", json={"info": manifest["info"]}).status_code == 401
        )
        assert (
            client.post(
                base + ":capabilities",
                headers={**headers, "X-Organization-Id": "foreign"},
                json={"info": manifest["info"]},
            ).status_code
            == 403
        )
        created = client.post(
            f"/api/v1/projects/{ident.project_id}/datasets",
            headers={**headers, "Idempotency-Key": uuid4().hex},
            json={"name": "Raw media fixture", "description": "synthetic"},
        )
        assert created.status_code == 201, created.text
        dataset = created.json()["data"]["dataset_id"]
        manifest["dataset_id"] = dataset
        started = client.post(base, headers=headers, json=manifest)
        assert started.status_code == 201, started.text
        grant = started.json()
        path = base + "/" + grant["import_id"]
        for asset in grant["assets"]:
            payload = files[asset["path"]].read_bytes()
            uploaded = client.put(
                path + "/assets:upload-part",
                headers=headers,
                content=payload,
                params=dict(
                    dataset_id=dataset,
                    path=asset["path"],
                    multipart_upload_id=asset["multipart_upload_id"],
                    part_number=1,
                ),
            )
            assert uploaded.status_code == 200, uploaded.text
            complete = client.post(
                path + "/assets:complete",
                headers=headers,
                json=dict(
                    dataset_id=dataset,
                    path=asset["path"],
                    multipart_upload_id=asset["multipart_upload_id"],
                    size=len(payload),
                    part_count=1,
                ),
            )
            assert complete.status_code == 200, complete.text
        committed = client.post(path + ":commit", headers=headers, json={"manifest": manifest})
        assert committed.status_code == 200, committed.text
        assert committed.json()["status"] == "RAW_COMMITTED"
        episode = client.get(path + "/episodes/0", headers=headers)
        assert episode.status_code == 200, episode.text
        videos = {v["media_kind"]: v for v in episode.json()["videos"]}
        assert videos.keys() == {"RGB", "DEPTH"}
        assert videos["RGB"]["preview_kind"] == "ORIGINAL_VIDEO"
        assert videos["DEPTH"]["preview_kind"] == "DOWNLOAD_ONLY"
        assert videos["DEPTH"]["preview_reason"] == "RAW_DEPTH_REQUIRES_COLORIZATION"
        for video in videos.values():
            result = client.get(
                path + "/assets:read",
                headers=headers,
                params={"path": video["path"], "download": True},
            )
            assert result.status_code == 200, result.text
            body = client.get(result.json()["url"])
            assert body.status_code == 200
            assert body.content == files[video["path"]].read_bytes()
            assert (
                hashlib.sha256(body.content).digest()
                == hashlib.sha256(files[video["path"]].read_bytes()).digest()
            )
        assert client.get(path + "/episodes/0").status_code == 401
        assert (
            client.get(
                path + "/episodes/0", headers={**headers, "X-Organization-Id": "other"}
            ).status_code
            == 403
        )
        with repository.transaction() as cursor:
            cursor.execute(
                "UPDATE access_control.capability_grants SET active=false WHERE principal_id=%s",
                (principal,),
            )
        assert client.get(path + "/episodes/0", headers=headers).status_code == 403
        assert (
            client.post(
                base + ":capabilities", headers=headers, json={"info": manifest["info"]}
            ).status_code
            == 403
        )

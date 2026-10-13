from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lerobot_imports.source_directory import (
    TEXT_PREVIEW_BYTES,
    directory,
    text_preview,
)
from tests.lerobot_imports import test_lerobot_web_upload as web
from tests.lerobot_imports.test_original_storage import store_source


@pytest.fixture
def source():
    return store_source(
        {
            "capture.mcap": b"capture",
            "meta/info.json": b'{"robot":"openarmx"}',
            "meta/nested/readme.txt": "采集说明".encode(),
            "meta2/other.json": b"{}",
        },
        source_format="MCAP",
    )


def test_nested_folders_sizes_stable_pagination_and_local_paths(source):
    _storage, browser, raw = source
    root = directory(browser, raw, limit=1, local_root="/local/files")
    assert root.total == 3 and root.file_count == 4
    assert root.entries[0].name == "meta" and root.entries[0].file_count == 2
    assert root.local_path == f"/local/files/{raw.storage_prefix}"
    assert directory(browser, raw, offset=1, limit=1).entries[0].name == "meta2"
    nested = directory(browser, raw, path="meta", local_root="/local/files")
    assert [f.name for f in nested.entries] == ["nested", "info.json"]
    file = nested.entries[1]
    assert file.text_preview and file.sha256
    assert file.local_path == f"/local/files/{raw.storage_prefix}/meta/info.json"
    assert nested.file_count == 2
    assert root.total_bytes == nested.total_bytes + len(b"capture{}")


@pytest.mark.parametrize("path", ["..", "meta/../meta2", "/meta", "meta//nested", "meta\\nested"])
def test_rejects_traversal(source, path):
    _, browser, raw = source
    for call in (
        lambda: directory(browser, raw, path=path),
        lambda: text_preview(browser, raw, path),
    ):
        with pytest.raises(ProblemException) as exc:
            call()
        assert exc.value.problem.status == 422


def test_membership_binary_and_empty_directory(source):
    _, browser, raw = source
    for call, status in [
        (lambda: directory(browser, raw, path="missing"), 404),
        (lambda: text_preview(browser, raw, "missing.json"), 404),
        (lambda: text_preview(browser, raw, "capture.mcap"), 415),
    ]:
        with pytest.raises(ProblemException) as exc:
            call()
        assert exc.value.problem.status == status
    assert text_preview(browser, raw, "meta/nested/readme.txt").text == "采集说明"


def test_robot_rollout_manifest_can_be_browsed_without_native_manifest(source):
    from hc_data_platform.ingest.models import raw_object_key
    from tests.ingest.test_ingest import manifest_for

    storage, browser, raw = source
    manifest = manifest_for(b"robot-capture")
    key = raw_object_key(manifest)
    storage.objects[key] = b"robot-capture"
    storage.put_json("robot-manifest.json", manifest.model_dump(mode="json"), if_none_match=True)
    raw = raw.model_copy(
        update={
            "manifest_key": "robot-manifest.json",
            "project_id": manifest.project_id,
            "storage_prefix": key.rsplit("/", 1)[0],
            "content_hash": manifest.sha256,
        }
    )
    file = directory(browser, raw).entries[0]
    assert file.name == "recording.mcap" and file.storage_key == key
    assert file.size == len(b"robot-capture")
    assert key in browser.grant(raw, file.path, download=True).url
    with pytest.raises(ValueError):
        directory(browser, raw.model_copy(update={"content_hash": "a" * 64}))


def test_robot_upload_manifest_uses_verified_original_paths_and_scope(source):
    import hashlib

    from hc_data_platform.lerobot_imports.source_browser import OriginalSourceBrowser
    from tests.robot_ingest.test_robot_ingest_service import (
        Harness,
        _complete_first_asset,
        _manifest,
    )

    harness = Harness()
    upload_id = _complete_first_asset(harness, _manifest())
    upload = harness.service.commit_upload(token=harness.token, upload_id=upload_id).data
    manifest_key = next(
        key for key in harness.storage.objects if key.endswith("/robot-ingest-manifest.json")
    )
    digest = hashlib.sha256(
        "\n".join(
            f"{a.asset_id}\0{a.expected_sha256}\0{a.expected_size_bytes}" for a in upload.assets
        ).encode()
    ).hexdigest()
    _, browser, raw = source
    raw = raw.model_copy(
        update={
            "raw_source_id": upload.raw_source_id,
            "upload_id": upload.upload_id,
            "organization_id": upload.target.organization_id,
            "project_id": upload.target.project_id,
            "region_code": upload.target.region_code,
            "dataset_id": upload.target.dataset_id,
            "collection_task_id": upload.target.collection_task_id,
            "robot_id": upload.authenticated_robot_id,
            "manifest_key": manifest_key,
            "storage_prefix": manifest_key.rsplit("/", 1)[0],
            "content_hash": digest,
            "file_count": len(upload.assets),
            "total_bytes": upload.total_bytes,
        }
    )
    browser = OriginalSourceBrowser(harness.storage, browser.repository)
    file = directory(browser, raw, path="capture").entries[0]
    assert file.path == "capture/raw.bin"
    assert file.storage_key == upload.assets[0].object_key
    assert file.storage_key in browser.grant(raw, file.path).url
    with pytest.raises(ValueError, match="IDENTITY"):
        directory(browser, raw.model_copy(update={"organization_id": "foreign"}))


def test_bounded_utf8_preview_and_aliased_object_location(source, monkeypatch):
    storage, browser, raw = source
    body = b"x" * (TEXT_PREVIEW_BYTES - 1) + "数据".encode()
    storage.objects["raw/real.json"] = body
    monkeypatch.setattr(
        browser,
        "manifest",
        lambda _: {
            "files": [
                {
                    "path": "logical/log.json",
                    "size": len(body),
                    "sha256": "a" * 64,
                    "object_key": "raw/real.json",
                }
            ]
        },
    )
    root = directory(browser, raw, path="logical", local_root="/local/files")
    assert root.local_path is None
    assert root.entries[0].local_path == "/local/files/raw/real.json"
    original = storage.read_range
    reads = []

    def bounded(key, start, end):
        reads.append((start, end))
        return original(key, start, end)

    monkeypatch.setattr(storage, "read_range", bounded)
    preview = text_preview(browser, raw, "logical/log.json")
    assert preview.truncated and preview.text == "x" * (TEXT_PREVIEW_BYTES - 1)
    assert reads == [(0, TEXT_PREVIEW_BYTES)]
    storage.objects["raw/real.json"] = b"\xff\x00"
    with pytest.raises(ProblemException) as exc:
        text_preview(browser, raw, "logical/log.json")
    assert exc.value.problem.status == 415


def test_directory_and_preview_http_scope_permission_and_no_store(source, monkeypatch):
    storage, browser, raw = source
    service = SimpleNamespace(storage=storage, raw_sources=browser.repository)
    base = f"/api/v1/projects/project-a/regions/cn-hz/lerobot-imports/{raw.raw_source_id}"
    with TestClient(web._app(service)) as client:
        for suffix, params in [
            ("directory", {"path": "meta"}),
            ("text-preview", {"path": "meta/info.json"}),
        ]:
            url = f"{base}/{suffix}"
            response = client.get(url, params=params, headers={"X-Organization-Id": "org-a"})
            assert response.status_code == 200, response.text
            assert response.headers["Cache-Control"] == "no-store"
            assert (
                client.get(url, params=params, headers={"X-Organization-Id": "foreign"}).status_code
                == 403
            )
            assert (
                client.get(
                    url.replace("project-a", "other"),
                    params=params,
                    headers={"X-Organization-Id": "org-a"},
                ).status_code
                == 403
            )
        auth = web._auth()
        monkeypatch.setattr(web, "_auth", lambda: replace(auth, capabilities=frozenset()))
        assert (
            client.get(base + "/directory", headers={"X-Organization-Id": "org-a"}).status_code
            == 403
        )
        assert (
            client.get(
                base + "/text-preview",
                params={"path": "meta/info.json"},
                headers={"X-Organization-Id": "org-a"},
            ).status_code
            == 403
        )

from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from zipfile import BadZipFile, ZipFile

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lerobot_imports.source_archive import (
    OriginalArchiveRequest,
    archive_files,
    stream_archive,
)
from tests.lerobot_imports import test_lerobot_web_upload as web
from tests.lerobot_imports.test_original_storage import SCOPE, store_source


@pytest.fixture
def sources():
    first = {"capture.mcap": b"original-one", "meta/采集说明.json": b'{"camera":1}'}
    second = {"capture.mcap": b"original-second", "meta/采集说明.json": b'{"camera":2}'}
    storage, browser, raw = store_source(first, source_format="MCAP")
    other_storage, _, other = store_source(
        second, raw_sources=browser.repository, source_format="MCAP"
    )
    storage.objects.update(other_storage.objects)
    return storage, browser, [raw, other], [first, second]


def test_bulk_archive_preserves_all_original_bytes_and_nested_paths(sources):
    storage, browser, raws, bodies = sources
    before = dict(storage.objects)
    # Duplicate selections do not duplicate files; no processed Episodes are required.
    files = archive_files(
        browser,
        **SCOPE,
        command=OriginalArchiveRequest(import_ids=[r.raw_source_id for r in raws] * 2),
    )
    with ZipFile(BytesIO(b"".join(stream_archive(browser, files)))) as archive:
        expected = {
            f"{raw.raw_source_id}/{path}": body
            for raw, batch in zip(raws, bodies, strict=True)
            for path, body in batch.items()
        }
        assert set(archive.namelist()) == set(expected)
        assert {name: archive.read(name) for name in archive.namelist()} == expected
        assert archive.testzip() is None
    assert storage.objects == before
    assert all(r.processing_status.value == "NOT_REQUESTED" for r in raws)


def test_stream_is_lazy_bounded_and_closes_abandoned_source(sources, monkeypatch):
    storage, browser, raws, _ = sources
    files = archive_files(
        browser, **SCOPE, command=OriginalArchiveRequest(import_ids=[raws[0].raw_source_id])
    )
    reads, closed = [], []

    def chunks(key, chunk_size):
        assert chunk_size == 1024**2
        reads.append(key)
        try:
            yield storage.objects[key]
        finally:
            closed.append(key)

    monkeypatch.setattr(storage, "read_chunks", chunks)
    stream = stream_archive(browser, files)
    assert reads == []
    next(stream)  # ZIP header precedes the first file's content.
    assert reads == []
    next(stream)
    assert reads == [files[0].key]
    stream.close()
    assert closed == reads


@pytest.mark.parametrize("damage", [b"short", b"x" * 12, b"longer-than-committed-size"])
def test_changed_source_never_finishes_a_valid_archive(sources, damage):
    storage, browser, raws, _ = sources
    files = archive_files(
        browser, **SCOPE, command=OriginalArchiveRequest(import_ids=[raws[0].raw_source_id])
    )
    storage.objects[files[0].key] = damage
    partial = bytearray()
    with pytest.raises(ValueError, match="original file"):
        for block in stream_archive(browser, files):
            partial.extend(block)
    with pytest.raises(BadZipFile):
        ZipFile(BytesIO(partial))


def test_all_selected_sources_are_authorized_before_streaming(sources):
    _, browser, raws, _ = sources
    with pytest.raises(ProblemException) as exc:
        archive_files(
            browser,
            **SCOPE,
            command=OriginalArchiveRequest(import_ids=[raws[0].raw_source_id, "foreign-source"]),
        )
    assert exc.value.problem.status == 404


@pytest.mark.parametrize(
    "path", ["../outside", "/absolute", "meta/../outside", "meta\\file", "meta/file\x00hidden"]
)
def test_rejects_unsafe_archive_members(sources, monkeypatch, path):
    _, browser, raws, _ = sources
    monkeypatch.setattr(
        browser, "manifest", lambda _: {"files": [{"path": path, "size": 1, "sha256": "a" * 64}]}
    )
    with pytest.raises(ProblemException) as exc:
        archive_files(
            browser, **SCOPE, command=OriginalArchiveRequest(import_ids=[raws[0].raw_source_id])
        )
    assert exc.value.problem.status == 409


def test_archive_uses_manifest_object_alias(sources, monkeypatch):
    storage, browser, raws, _ = sources
    raw = raws[0]
    descriptor = browser.manifest(raw)["files"][0]
    storage.objects["raw/aliased-source"] = storage.objects[
        f"{raw.storage_prefix}/{descriptor['path']}"
    ]
    monkeypatch.setattr(
        browser,
        "manifest",
        lambda _: {"files": [{**descriptor, "object_key": "raw/aliased-source"}]},
    )
    files = archive_files(
        browser, **SCOPE, command=OriginalArchiveRequest(import_ids=[raw.raw_source_id])
    )
    with ZipFile(BytesIO(b"".join(stream_archive(browser, files)))) as archive:
        assert archive.read(files[0].path) == storage.objects["raw/aliased-source"]


def test_archive_http_permissions_scope_and_selection_validation(sources, monkeypatch):
    storage, browser, raws, _ = sources
    service = SimpleNamespace(storage=storage, raw_sources=browser.repository)
    auth = web._auth()
    monkeypatch.setattr(
        web,
        "_auth",
        lambda: replace(auth, capabilities=frozenset({"upload.read", "export.download"})),
    )
    path = "/api/v1/projects/project-a/regions/cn-hz/lerobot-imports/archive"
    headers = {"X-Organization-Id": "org-a"}
    body = {"import_ids": [raw.raw_source_id for raw in raws]}
    with TestClient(web._app(service)) as client:
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        assert "attachment" in response.headers["content-disposition"]
        assert len(ZipFile(BytesIO(response.content)).namelist()) == 4
        assert client.post(path, json={"import_ids": []}, headers=headers).status_code == 422
        assert (
            client.post(path, json={"import_ids": ["missing"]}, headers=headers).status_code == 404
        )
        assert (
            client.post(path, json=body, headers={"X-Organization-Id": "foreign"}).status_code
            == 403
        )
        assert (
            client.post(
                path.replace("project-a", "foreign"), json=body, headers=headers
            ).status_code
            == 403
        )
        for permission in ("upload.read", "export.download"):
            monkeypatch.setattr(
                web, "_auth", lambda p=permission: replace(auth, capabilities=frozenset({p}))
            )
            assert client.post(path, json=body, headers=headers).status_code == 403

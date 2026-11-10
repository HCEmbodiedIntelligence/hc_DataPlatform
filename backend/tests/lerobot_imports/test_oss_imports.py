import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import pytest

from hc_data_platform.collection_tasks.deletion_plan import registered_locators
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.lerobot_imports.oss_imports import (
    CommitOssImport,
    commit_source,
    inspect_source,
)
from hc_data_platform.lerobot_imports.service import LeRobotWebUploadService
from hc_data_platform.lerobot_imports.source_archive import (
    OriginalArchiveRequest,
    archive_files,
    stream_archive,
)
from hc_data_platform.lerobot_imports.source_browser import OriginalSourceBrowser
from hc_data_platform.object_storage.providers import OssObjectStorage
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.storage.oss_prefix import PrefixedOssBucket
from hc_data_platform.storage.oss_reference import reference_uri

SCOPE = dict(organization_id="org", project_id="project", region_code="cn")
AUTH = AuthContext(
    subject_id="user",
    organization_ids=frozenset({"org"}),
    project_ids=frozenset({"project"}),
    region_codes=frozenset({"cn"}),
    capabilities=frozenset({"upload.manage", "upload.read"}),
    organization_scope_triples=frozenset({("org", "project", "cn")}),
)


class Missing(Exception):
    status = 404


class Exists(Exception):
    status = 409


class Bucket:
    bucket_name = "bucket"

    def __init__(self, bodies):
        self.objects = dict(bodies)
        self.writes = []
        self.reads = []
        self.parts = {}

    def get_object_meta(self, key):
        if key not in self.objects:
            raise Missing()
        body = self.objects[key]
        return SimpleNamespace(
            headers={"content-length": str(len(body))}, etag=hashlib.md5(body).hexdigest()
        )

    def get_object(self, key, byte_range=None):
        self.reads.append(key)
        body = self.objects[key]
        return BytesIO(body if byte_range is None else body[byte_range[0] : byte_range[1] + 1])

    def put_object(self, key, body, headers=None):
        if headers and headers.get("x-oss-forbid-overwrite") == "true" and key in self.objects:
            raise Exists()
        self.writes.append(key)
        self.objects[key] = body

    def list_objects_v2(self, prefix, continuation_token="", **kwargs):
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        offset = int(continuation_token or 0)
        selected = keys[offset : offset + 2]
        return SimpleNamespace(
            object_list=[
                SimpleNamespace(key=k, size=len(self.objects[k]), etag=self.get_object_meta(k).etag)
                for k in selected
            ],
            is_truncated=len(keys) > offset + 2,
            next_continuation_token=str(offset + 2),
            prefix_list=[],
        )

    def init_multipart_upload(self, key, **kwargs):
        self.parts[key] = {}
        return SimpleNamespace(upload_id=key)

    def upload_part(self, key, upload_id, number, body, **kwargs):
        value = body.read()
        self.parts[key][number] = value
        return SimpleNamespace(etag=hashlib.md5(value).hexdigest())

    def list_parts(self, key, upload_id, **kwargs):
        return SimpleNamespace(
            is_truncated=False,
            parts=[
                SimpleNamespace(part_number=n, size=len(b), etag=hashlib.md5(b).hexdigest())
                for n, b in sorted(self.parts[key].items())
            ],
        )

    def complete_multipart_upload(self, key, upload_id, parts, **kwargs):
        self.put_object(key, b"".join(self.parts[key][p.part_number] for p in parts), **kwargs)
        self.parts.pop(key)

    def abort_multipart_upload(self, key, upload_id):
        self.parts.pop(key, None)

    def sign_url(self, method, key, expires, **kwargs):
        return f"https://bucket.example/{key}"


def setup(bodies):
    bucket = Bucket({"root/input/" + p: b for p, b in bodies.items()})
    storage = OssObjectStorage(PrefixedOssBucket(bucket, "root"))
    return bucket, storage, LeRobotWebUploadService(storage)


def commit(service, preview, **fields):
    fields.setdefault("collection_task_id", "task")
    fields.setdefault("robot_id", "robot")
    return commit_source(
        service,
        auth=AUTH,
        **SCOPE,
        command=CommitOssImport(
            source_uri=preview["source_uri"],
            inventory_sha256=preview["inventory_sha256"],
            dataset_id="dataset",
            **fields,
        ),
    )


@pytest.mark.parametrize(
    "bodies,format",
    [
        ({"one.mcap": b"mcap", "notes.json": b"{}"}, "MCAP"),
        ({"one.bag": b"bag"}, "ROSBAG"),
        ({"metadata.yaml": b"meta", "one.db3": b"bag"}, "ROSBAG"),
    ],
)
def test_import_references_originals_and_export_reads_them(bodies, format):
    bucket, storage, service = setup(bodies)
    preview = inspect_source(storage, "oss://bucket/root/input/")
    assert preview["source_format"] == format
    assert bucket.reads == []
    accepted = commit(service, preview)
    assert commit(service, preview) == accepted
    assert len(bucket.writes) == 2
    assert all("/source/" not in key for key in bucket.writes)
    browser = OriginalSourceBrowser(storage, service.raw_sources)
    raw = browser.source(**SCOPE, import_id=accepted.import_id)
    assert raw.storage_prefix == "oss://bucket/root/input"
    manifest = browser.manifest(raw)
    assert manifest["storage_mode"] == "oss_reference"
    for item in manifest["files"]:
        assert item["object_key"] == reference_uri("bucket", "root/input/" + item["path"])
        assert item["sha256"] == hashlib.sha256(bodies[item["path"]]).hexdigest()
    import zipfile

    files = archive_files(
        browser, **SCOPE, command=OriginalArchiveRequest(import_ids=(accepted.import_id,))
    )
    with zipfile.ZipFile(BytesIO(b"".join(stream_archive(browser, files)))) as archive:
        assert {n.split("/", 1)[1]: archive.read(n) for n in archive.namelist()} == bodies
    keys, prefixes = registered_locators("ingest.raw_sources", raw.model_dump(), "bucket")
    assert keys == {raw.manifest_key} and prefixes == set()


def test_pagination_single_file_and_special_names():
    bodies = {"中文 # % ? .mcap": b"1", "nested/two.mcap": b"2", "three.mcap": b"3"}
    bucket, storage, service = setup(bodies)
    preview = inspect_source(storage, "oss://bucket/root/input/")
    assert len(preview["files"]) == 3
    commit(service, preview)
    single = inspect_source(storage, reference_uri("bucket", "root/input/中文 # % ? .mcap"))
    assert single["files"][0]["path"] == "中文 # % ? .mcap"
    commit(service, single)


@pytest.mark.parametrize(
    "uri",
    [
        "oss://other/root/input/",
        "https://example.org/input/",
        "oss://bucket/outside/file.mcap",
        "oss://bucket/root/../private",
        "oss://bucket/root/input/?token=secret",
        "oss://bucket/root/input/%2e%2e/file",
    ],
)
def test_denies_unconfigured_sources(uri):
    _, storage, _ = setup({"one.mcap": b"x"})
    with pytest.raises(ProblemException):
        inspect_source(storage, uri)


@pytest.mark.parametrize("method", ["put_object", "delete_object", "init_multipart_upload"])
def test_reference_is_read_only(method):
    bucket, storage, _ = setup({"one.mcap": b"x"})
    with pytest.raises(ValueError):
        getattr(storage._bucket, method)("oss://bucket/root/input/one.mcap")
    assert bucket.objects == {"root/input/one.mcap": b"x"}


def test_changed_inventory_and_missing_permission_do_not_register():
    bucket, storage, service = setup({"one.mcap": b"x"})
    preview = inspect_source(storage, "oss://bucket/root/input/")
    bucket.objects["root/input/one.mcap"] = b"y"
    with pytest.raises(ProblemException) as exc:
        commit(service, preview)
    assert exc.value.problem.code == "OSS_IMPORT_SOURCE_CHANGED"
    assert bucket.writes == []
    denied = (
        AUTH.model_copy(update={"capabilities": frozenset()})
        if hasattr(AUTH, "model_copy")
        else None
    )
    from dataclasses import replace

    denied = denied or replace(AUTH, capabilities=frozenset())
    with pytest.raises(ProblemException):
        commit_source(
            service,
            auth=denied,
            **SCOPE,
            command=CommitOssImport(
                source_uri=preview["source_uri"],
                inventory_sha256=preview["inventory_sha256"],
                dataset_id="dataset",
                collection_task_id="task",
                robot_id="robot",
            ),
        )
    assert bucket.writes == []


def test_mutation_during_read_is_rejected():
    bucket, storage, service = setup({"one.mcap": b"x"})
    preview = inspect_source(storage, "oss://bucket/root/input/")
    original = bucket.get_object

    def mutate(key, **kwargs):
        body = original(key, **kwargs)
        if key == "root/input/one.mcap":
            bucket.objects[key] = b"y"
        return body

    bucket.get_object = mutate
    with pytest.raises(ProblemException):
        commit(service, preview)
    assert not any(k.endswith("/manifest.json") for k in bucket.writes)


def test_mcap_processing_uses_normal_job_and_snapshot():
    _, storage, service = setup({"one.mcap": b"mcap"})
    preview = inspect_source(storage, "oss://bucket/root/input/")
    accepted = commit(
        service, preview, processing_mode="PROCESS", collection_task_id="task", robot_id="robot"
    )
    raw = service.raw_sources.get_source(**SCOPE, raw_source_id=accepted.import_id)
    job = service.raw_sources.get_job(**SCOPE, raw_source_id=accepted.import_id)
    assert raw.processing_status.value == "PENDING"
    assert job.job_type.value == "CONTINUOUS_RECORDING_DISCOVERY"
    assert "processing_config_snapshot" in json.loads(
        b"".join(storage.read_chunks(raw.manifest_key))
    )


@pytest.mark.parametrize("packed", [False, True])
def test_lerobot_directory_uses_existing_format_rules(packed):
    info = dict(
        codebase_version="v3.0",
        robot_type="another_robot",
        fps=25,
        total_episodes=1,
        data_path="data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        video_path="videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        features={},
    )
    bodies = {
        "meta/info.json": json.dumps(info).encode(),
        "meta/episodes/chunk-000/file-000.parquet": b"episodes",
        "data/chunk-000/file-000.parquet": b"data",
    }
    bucket, storage, service = setup(
        {"capture.zip": zip_bytes({"capture/" + p: b for p, b in bodies.items()})}
        if packed
        else bodies
    )
    preview = inspect_source(storage, "oss://bucket/root/input/")
    assert preview["source_format"] == "LEROBOT_V3"
    assert preview["info"] == info
    accepted = commit(service, preview)
    assert accepted.episode_count == 1
    assert len(bucket.writes) == (5 if packed else 2)


def test_rebuild_and_export_selected_mcap_from_oss_reference(tmp_path):
    from hc_data_platform.episode_store.selected_cache import (
        materialize_selected,
        selected_reference,
    )
    from tests.episode_store.test_native_retention import fixture

    src, memory = fixture(tmp_path)
    original = memory.objects["raw/original.mcap"]
    bucket, storage, service = setup({"original.mcap": original})
    preview = inspect_source(storage, "oss://bucket/root/input/")
    accepted = commit(
        service, preview, processing_mode="PROCESS", collection_task_id="task", robot_id="robot"
    )
    raw = service.raw_sources.get_source(**SCOPE, raw_source_id=accepted.import_id)
    manifest = OriginalSourceBrowser(storage, service.raw_sources).manifest(raw)
    src["provenance"]["source_object"] = manifest["files"][0]
    target = tmp_path / "oss-rebuilt.mcap"
    before = dict(bucket.objects)
    materialize_selected(storage, selected_reference(src["provenance"]), src["provenance"], target)
    assert target.read_bytes() == src["path"].read_bytes()
    assert bucket.objects == before


def test_http_import_scope_validation_and_retry():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from hc_data_platform.lerobot_imports.router import get_service, router
    from hc_data_platform.security.http import require_auth_context

    app = FastAPI()
    bucket, storage, service = setup({"one.mcap": b"mcap"})
    app.include_router(router)
    app.dependency_overrides[get_service] = lambda: service
    app.dependency_overrides[require_auth_context] = lambda: AUTH

    @app.exception_handler(ProblemException)
    async def error(request, exc):
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    client = TestClient(app)
    base = "/api/v1/projects/project/regions/cn/lerobot-imports/oss:"
    headers = {"X-Organization-Id": "org"}
    denied = client.post(
        base.replace("/project/", "/other/") + "inspect",
        headers=headers,
        json={"source_uri": "oss://bucket/root/input/"},
    )
    assert denied.status_code == 403 and bucket.reads == []
    inspected = client.post(
        base + "inspect", headers=headers, json={"source_uri": "oss://bucket/root/input/"}
    )
    assert inspected.status_code == 200 and inspected.headers["cache-control"] == "no-store"
    preview = inspected.json()
    command = dict(
        source_uri=preview["source_uri"],
        inventory_sha256=preview["inventory_sha256"],
        dataset_id="dataset",
        processing_mode="PROCESS",
    )
    assert client.post(base + "commit", headers=headers, json=command).status_code == 422
    assert bucket.writes == []
    command.update(collection_task_id="task", robot_id="robot")
    first = client.post(base + "commit", headers=headers, json=command)
    second = client.post(base + "commit", headers=headers, json=command)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_import_configured_root_keeps_directory_semantics():
    _, storage, service = setup({"one.mcap": b"mcap"})
    preview = inspect_source(storage, "oss://bucket/root/")
    assert preview["source_uri"] == "oss://bucket/root/"
    commit(service, preview)


def zip_bytes(files):
    import zipfile

    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, body in files.items():
            archive.writestr(path, body)
    return stream.getvalue()


@pytest.mark.parametrize("mode", ["STORE_ONLY", "PROCESS"])
def test_zip_import_binds_task_and_publishes_members_once(mode):
    bodies = {"one.mcap": b"mcap-data", "notes.json": b"{}"}
    packed = zip_bytes({"capture/" + p: b for p, b in bodies.items()})
    bucket, storage, service = setup({"capture.ZIP": packed})
    preview = inspect_source(storage, "oss://bucket/root/input/capture.ZIP")
    assert preview["source_format"] == "MCAP"
    assert preview["total_bytes"] == sum(map(len, bodies.values()))
    assert bucket.writes == []
    accepted = commit(service, preview, processing_mode=mode)
    writes = [k for k in bucket.writes if "/source/" in k]
    assert len(writes) == 2
    assert commit(service, preview, processing_mode=mode) == accepted
    assert [k for k in bucket.writes if "/source/" in k] == writes
    raw = service.raw_sources.get_source(**SCOPE, raw_source_id=accepted.import_id)
    assert raw.collection_task_id == "task" and raw.robot_id == "robot"
    assert raw.dataset_id == "dataset"
    assert bucket.objects["root/input/capture.ZIP"] == packed
    browser = OriginalSourceBrowser(storage, service.raw_sources)
    manifest = browser.manifest(raw)
    assert manifest["storage_mode"] == "native_objects"
    assert manifest["oss_archive"]["source_uri"] == preview["source_uri"]
    import zipfile

    files = archive_files(
        browser, **SCOPE, command=OriginalArchiveRequest(import_ids=(accepted.import_id,))
    )
    with zipfile.ZipFile(BytesIO(b"".join(stream_archive(browser, files)))) as archive:
        assert {n.split("/", 1)[1]: archive.read(n) for n in archive.namelist()} == bodies


@pytest.mark.parametrize(
    "path", ["../escape.mcap", "/absolute.mcap", "C:/evil.mcap", "a/../../evil.mcap"]
)
def test_zip_rejects_unsafe_paths_without_writes(path):
    bucket, storage, _ = setup({"capture.zip": zip_bytes({path: b"mcap"})})
    with pytest.raises(ProblemException) as exc:
        inspect_source(storage, "oss://bucket/root/input/capture.zip")
    assert exc.value.problem.code == "OSS_IMPORT_SOURCE_INVALID"
    assert bucket.writes == []


def test_zip_limits_and_corrupt_content(monkeypatch):
    from hc_data_platform.lerobot_imports import oss_archive

    bucket, storage, _ = setup({"capture.zip": zip_bytes({"a.mcap": b"x" * 4000})})
    monkeypatch.setattr(oss_archive, "MAX_ARCHIVE_BYTES", 1000)
    with pytest.raises(ProblemException) as exc:
        inspect_source(storage, "oss://bucket/root/input/capture.zip")
    assert "2 GiB" in exc.value.problem.detail
    bucket.objects["root/input/capture.zip"] = b"not a zip"
    with pytest.raises(ProblemException):
        inspect_source(storage, "oss://bucket/root/input/capture.zip")
    assert bucket.writes == []


def test_archive_changed_after_inspection_does_not_publish():
    bucket, storage, service = setup({"capture.zip": zip_bytes({"a.mcap": b"old"})})
    preview = inspect_source(storage, "oss://bucket/root/input/capture.zip")
    bucket.objects["root/input/capture.zip"] = zip_bytes({"a.mcap": b"new"})
    with pytest.raises(ProblemException) as exc:
        commit(service, preview)
    assert exc.value.problem.code == "OSS_IMPORT_SOURCE_CHANGED"
    assert bucket.writes == []


def test_store_only_requires_task_and_robot():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CommitOssImport(
            source_uri="oss://bucket/root/input/",
            inventory_sha256="a" * 64,
            dataset_id="dataset",
            processing_mode="STORE_ONLY",
        )


@pytest.mark.parametrize(
    "invalid", ["symlink", "encrypted", "duplicate", "collision", "crc", "count"]
)
def test_zip_rejects_unsafe_or_damaged_entries(invalid, monkeypatch):
    import stat
    import zipfile

    from hc_data_platform.lerobot_imports import oss_archive

    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        entry = zipfile.ZipInfo("one.mcap")
        if invalid == "symlink":
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, b"unique-mcap-payload")
        if invalid == "duplicate":
            archive.writestr("one.mcap", b"again")
        if invalid == "collision":
            archive.writestr("one.mcap/two.mcap", b"again")
    packed = stream.getvalue()
    if invalid == "encrypted":
        packed = bytearray(packed)
        central = packed.index(b"PK\x01\x02")
        packed[central + 8] |= 1
        packed = bytes(packed)
    if invalid == "crc":
        packed = packed.replace(b"unique-mcap-payload", b"broken-mcap-payload")
    if invalid == "count":
        monkeypatch.setattr(oss_archive, "MAX_ARCHIVE_ENTRIES", 0)
    bucket, storage, _ = setup({"capture.zip": packed})
    with pytest.raises(ProblemException) as exc:
        inspect_source(storage, "oss://bucket/root/input/capture.zip")
    assert exc.value.problem.code == "OSS_IMPORT_SOURCE_INVALID"
    assert bucket.writes == []


def test_store_only_validates_task_dataset_and_robot(monkeypatch):
    from unittest.mock import MagicMock

    from hc_data_platform.lerobot_imports.configuration import validate_target

    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    factory = MagicMock()
    factory.return_value.__enter__.return_value = connection
    robots = MagicMock(return_value=[object()])
    monkeypatch.setattr("hc_data_platform.lerobot_imports.configuration.import_robots", robots)
    manifest = SimpleNamespace(
        processing_mode="STORE_ONLY",
        dataset_id="dataset",
        collection_task_id="task",
        robot_id="robot",
    )
    cursor.fetchone.return_value = ("wrong-dataset", "ACTIVE")
    with pytest.raises(ProblemException) as exc:
        validate_target(**SCOPE, manifest=manifest, connection_factory=factory)
    assert exc.value.problem.code == "LEROBOT_TARGET_INVALID"
    cursor.fetchone.return_value = ("dataset", "ARCHIVED")
    with pytest.raises(ProblemException):
        validate_target(**SCOPE, manifest=manifest, connection_factory=factory)
    cursor.fetchone.return_value = ("dataset", "ACTIVE")
    robots.return_value = []
    with pytest.raises(ProblemException) as exc:
        validate_target(**SCOPE, manifest=manifest, connection_factory=factory)
    assert exc.value.problem.code == "LEROBOT_ROBOT_UNAVAILABLE"
    robots.return_value = [object()]
    validate_target(**SCOPE, manifest=manifest, connection_factory=factory)

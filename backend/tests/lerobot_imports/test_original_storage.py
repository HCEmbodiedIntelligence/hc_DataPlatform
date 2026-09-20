from __future__ import annotations

import hashlib
import json
from io import BytesIO
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.ingest.raw_sources import RawSourceRepositoryPort
from hc_data_platform.lerobot_imports.models import (
    CommitLeRobotImportV1,
    CompleteLeRobotAssetV1,
    CreateLeRobotImportV1,
)
from hc_data_platform.lerobot_imports.service import LeRobotWebUploadService, read_bounded
from hc_data_platform.lerobot_imports.source_browser import OriginalSourceBrowser
from hc_data_platform.security.auth import AuthContext

SCOPE = dict(organization_id="org-a", project_id="project-a", region_code="cn-hz")


def store_source(
    bodies: dict[str, bytes], *, raw_sources: RawSourceRepositoryPort | None = None, **fields: Any
) -> tuple[Any, Any, Any]:
    auth = AuthContext(
        subject_id="uploader",
        organization_ids=frozenset({"org-a"}),
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset({"cn-hz"}),
        capabilities=frozenset({"upload.manage", "upload.read"}),
        organization_scope_triples=frozenset({("org-a", "project-a", "cn-hz")}),
    )
    storage = InMemoryObjectStorage()
    service = LeRobotWebUploadService(storage, raw_sources=raw_sources)
    manifest = CreateLeRobotImportV1(
        dataset_id="dataset-a",
        **fields,
        files=tuple(
            {"path": path, "size": len(body), "part_count": 1} for path, body in bodies.items()
        ),
    )
    grant = service.begin(auth=auth, **SCOPE, manifest=manifest)
    for asset in grant.assets:
        body = bodies[asset.path]
        service.upload_part(
            auth=auth,
            **SCOPE,
            import_id=grant.import_id,
            dataset_id=manifest.dataset_id,
            path=asset.path,
            multipart_upload_id=asset.multipart_upload_id,
            part_number=1,
            body=BytesIO(body),
            size=len(body),
        )
        service.complete_asset(
            auth=auth,
            **SCOPE,
            import_id=grant.import_id,
            command=CompleteLeRobotAssetV1(
                dataset_id=manifest.dataset_id,
                path=asset.path,
                multipart_upload_id=asset.multipart_upload_id,
                size=len(body),
                part_count=1,
            ),
        )
    accepted = service.commit(
        auth=auth,
        **SCOPE,
        import_id=grant.import_id,
        command=CommitLeRobotImportV1(manifest=manifest),
    )
    assert accepted.status == "RAW_COMMITTED"
    assert accepted.episode_task_count == 0
    assert not any(path.startswith("derived/") for path in storage.objects)
    browser = OriginalSourceBrowser(storage, service.raw_sources)
    raw = browser.source(**SCOPE, import_id=grant.import_id)
    for file in browser.files(raw, offset=0, limit=100).files:
        assert file.sha256 == hashlib.sha256(bodies[file.path]).hexdigest()
        assert storage.objects[f"{raw.storage_prefix}/{file.path}"] == bodies[file.path]
    job = service.raw_sources.get_job(**SCOPE, raw_source_id=grant.import_id)
    assert job is not None and job.workflow_id is None and job.status.value == "SUCCEEDED"
    return storage, browser, raw


@pytest.mark.parametrize(
    ("source_format", "bodies"),
    [
        ("MCAP", {"capture.mcap": b"original-mcap"}),
        ("ROSBAG", {"capture.bag": b"original-ros1"}),
        ("ROSBAG", {"metadata.yaml": b"original-metadata", "data_0.db3": b"original-sqlite"}),
        ("ROSBAG", {"metadata.yaml": b"original-metadata", "data_0.mcap": b"original-ros2-mcap"}),
    ],
)
def test_capture_storage_without_robot_task_or_processing(
    source_format: str, bodies: dict[str, bytes]
) -> None:
    _storage, browser, raw = store_source(bodies, source_format=source_format)
    assert raw.collection_task_id is None and raw.robot_id is None
    assert raw.processing_status.value == "NOT_REQUESTED"
    assert browser.files(raw, offset=1, limit=1).total == len(bodies)
    with pytest.raises(ProblemException) as missing:
        browser.source(**{**SCOPE, "project_id": "another-project"}, import_id=raw.raw_source_id)
    assert missing.value.problem.status == 404
    with pytest.raises(ProblemException) as traversal:
        browser.grant(raw, "../another-source/capture.bag")
    assert traversal.value.problem.status == 404


def test_four_original_video_offsets_are_read_without_loading_videos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cameras = [f"observation.images.camera_{index}" for index in range(4)]
    info = {
        "codebase_version": "v3.0",
        "robot_type": "another_robot",
        "fps": 25,
        "total_episodes": 2,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {camera: {"dtype": "video"} for camera in cameras},
    }
    rows = []
    for index in range(2):
        row: dict[str, Any] = {"episode_index": index, "length": 50}
        for camera in cameras:
            prefix = f"videos/{camera}/"
            row.update(
                {
                    prefix + "chunk_index": 0,
                    prefix + "file_index": 0,
                    prefix + "from_timestamp": 1.25 + index * 2,
                    prefix + "to_timestamp": 3.25 + index * 2,
                }
            )
        rows.append(row)
    buffer = BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buffer)
    bodies = {
        "meta/info.json": json.dumps(info).encode(),
        "meta/episodes/chunk-000/file-000.parquet": buffer.getvalue(),
        "data/chunk-000/file-000.parquet": b"original-numeric-data",
    }
    bodies.update(
        {f"videos/{camera}/chunk-000/file-000.mp4": b"original-av1-video" for camera in cameras}
    )
    storage, browser, raw = store_source(bodies, info=info)
    old_range, old_chunks = storage.read_range, storage.read_chunks

    def ranged(key: str, start: int, end: int) -> bytes:
        assert "/videos/" not in key and "/source/data/" not in key
        return old_range(key, start, end)

    def chunks(key: str, chunk_size: int = 8 * 1024**2) -> Any:
        assert "/videos/" not in key and "/source/data/" not in key
        return old_chunks(key, chunk_size)

    monkeypatch.setattr(storage, "read_range", ranged)
    monkeypatch.setattr(storage, "read_chunks", chunks)
    episode = browser.episode(raw, 1)
    assert episode.duration_ns == "2000000000" and episode.fps == 25
    assert len(episode.videos) == 4
    assert all(
        video.start_seconds == 3.25 and video.end_seconds == 5.25 for video in episode.videos
    )
    grant = browser.grant(raw, episode.videos[0].path)
    assert f"/{raw.storage_prefix}/videos/" in grant.url


def test_oversized_metadata_is_rejected_before_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = InMemoryObjectStorage()
    storage.objects["large"] = b"x" * 100
    monkeypatch.setattr(
        storage, "read_chunks", lambda *_args, **_kwargs: pytest.fail("must not read")
    )
    with pytest.raises(ProblemException) as captured:
        read_bounded(storage, "large", 20)
    assert captured.value.problem.status == 413

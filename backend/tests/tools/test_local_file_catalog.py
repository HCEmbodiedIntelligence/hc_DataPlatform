import hashlib
import json
from datetime import datetime, timezone

import pytest

from hc_data_platform.tools.local_file_catalog import Catalog, component, relative


def fixture_rows():
    stamp = datetime(2026, 10, 21, 6, 30, 5, tzinfo=timezone.utc)
    common = {"organization_id": "org", "project_id": "p", "region_code": "global"}
    raw = {
        **common,
        "raw_source_id": "raw1",
        "upload_id": "up1",
        "dataset_id": "ds",
        "collection_task_id": "task",
        "robot_id": "robot",
        "source_format": "MCAP",
        "storage_prefix": "raw/source",
        "processing_status": "READY",
        "file_count": 1,
        "total_bytes": 3,
        "committed_at": stamp,
        "created_at": stamp,
        "updated_at": stamp,
    }
    recording = {
        **common,
        "recording_id": "record1",
        "recording_upload_id": "rec-up",
        "collection_task_id": "task",
        "robot_id": "robot",
        "capture_started_at": stamp,
        "updated_at": stamp,
    }
    identity = {**common, "dataset_id": "ds", "episode_id": "ep1", "rollout_id": "record1:slice1"}
    frozen_source = {
        "recording_id": "record1",
        "slice_id": "slice1",
        "start_offset_ns": 5_000_000_000,
        "end_offset_ns": 20_125_000_000,
    }
    source_body = json.dumps(frozen_source).encode()
    manifest = {
        "input": {"identity": identity},
        "files": [
            {"path": "data/part.parquet", "size": 7, "sha256": "b" * 64},
            {
                "path": "hc/source.json",
                "size": len(source_body),
                "sha256": hashlib.sha256(source_body).hexdigest(),
            },
        ],
    }
    body = json.dumps(manifest).encode()
    rows = {
        "projects": [{**common, "display_name": "青山项目"}],
        "tasks": [
            {
                **common,
                "collection_task_id": "task",
                "task_code": "00000014",
                "name": "拿取 / 放置",
                "dataset_id": "ds",
            }
        ],
        "robots": [{"organization_id": "org", "robot_id": "robot", "display_name": "X1-001"}],
        "datasets": [],
        "raw": [raw],
        "uploads": [],
        "recordings": [recording],
        "assets": [
            {
                **common,
                "upload_id": "rec-up",
                "asset_path": "source/original/a.mcap",
                "object_key": "raw/source/a.mcap",
                "expected_size": 3,
                "expected_sha256": "a" * 64,
                "committed_at": stamp,
            },
            {
                **common,
                "upload_id": "rec-up",
                "asset_path": "videos/head.mp4",
                "object_key": "derived/hash/head.mp4",
                "expected_size": 5,
                "expected_sha256": "c" * 64,
                "committed_at": stamp,
            },
        ],
        "episodes": [],
        "slices": [
            {
                **common,
                "recording_id": "record1",
                "episode_id": "slice1",
                "revision": 1,
                "ordinal": 0,
                "start_offset_ns": 5_000_000_000,
                "end_offset_ns": 20_125_000_000,
            }
        ],
        "versions": [
            {
                **common,
                "input_document": {"task_id": "task"},
                "version": 1,
                "data_version_id": "dv1",
                "committed_at": stamp,
                "receipt_document": {
                    "data": {"identity": identity, "package_prefix": "episodes/ver1"},
                    "manifest_sha256": hashlib.sha256(body).hexdigest(),
                },
            }
        ],
    }
    documents = {
        "episodes/ver1/hc/manifest.json": (manifest, body),
        "episodes/ver1/hc/commit.json": (
            rows["versions"][0]["receipt_document"],
            json.dumps(rows["versions"][0]["receipt_document"]).encode(),
        ),
        "episodes/ver1/hc/source.json": (frozen_source, source_body),
    }

    def raw_manifest(_):
        return {"files": [{"path": "a.mcap", "size": 3, "sha256": "a" * 64}]}

    return rows, documents, raw_manifest


def test_business_names_timezone_and_complete_package():
    rows, docs, raw = fixture_rows()
    result = Catalog(rows, docs.__getitem__, raw).build()
    paths = {f["path"]: f for f in result["files"]}
    root = result["raw_locations"][0]["path"].removesuffix("/原始文件")
    assert "青山项目（项目p）/拿取 _ 放置（任务00000014）" in root
    assert "2026-10-21_14-30-05_X1-001" in root
    assert paths[root + "/原始文件/a.mcap"]["key"] == "raw/source/a.mcap"
    assert paths[root + "/处理后的文件/整段解析/videos/head.mp4"]["size"] == 5
    sliced = root + "/切片/片段001_00分05.000秒至00分20.125秒/版本001"
    assert paths[sliced + "/data/part.parquet"]["key"] == "episodes/ver1/data/part.parquet"
    assert sliced + "/hc/manifest.json" in paths
    assert sliced + "/hc/commit.json" in paths
    assert not result["warnings"]


def test_missing_capture_time_is_explicit():
    rows, docs, raw = fixture_rows()
    rows["recordings"] = []
    rows["versions"] = []
    result = Catalog(rows, docs.__getitem__, raw).build()
    assert "/导入于_2026-10-21_14-30-05_" in result["raw_locations"][0]["path"]


def test_slice_name_uses_frozen_version_window_not_current_edit():
    rows, docs, raw = fixture_rows()
    rows["slices"][0].update(start_offset_ns=12_000_000_000, end_offset_ns=15_000_000_000)
    result = Catalog(rows, docs.__getitem__, raw).build()
    paths = [f["path"] for f in result["files"]]
    assert any("片段001_00分05.000秒至00分20.125秒/版本001" in p for p in paths)
    assert not any("00分12.000秒" in p for p in paths)


def test_missing_manifest_never_leaves_a_partial_copyable_package():
    rows, docs, raw = fixture_rows()
    del docs["episodes/ver1/hc/commit.json"]
    result = Catalog(rows, docs.__getitem__, raw).build()
    assert not any("/版本001/" in f["path"] for f in result["files"])
    assert any(f["path"] == "目录状态说明.txt" for f in result["files"])
    assert any("拿取 _ 放置（任务00000014）" in warning for warning in result["warnings"])


def test_original_content_is_kept_when_processing_is_not_ready():
    rows, docs, raw = fixture_rows()
    rows["raw"][0]["processing_status"] = "FAILED"
    rows["versions"] = []
    result = Catalog(rows, docs.__getitem__, raw).build()
    assert any(f["path"].endswith("/原始文件/a.mcap") for f in result["files"])
    assert any("处理状态：处理失败" in f.get("text", "") for f in result["files"])


def test_names_do_not_escape_and_windows_reserved_names_are_readable():
    assert component("../../a:b\\c") == "_.._a_b_c"
    assert component("CON") == "名称_CON"
    assert len(component("机器人" * 100).encode()) <= 150
    for value in ("../outside", "/absolute", "a//b", "a/../b", "a\\b"):
        with pytest.raises(ValueError):
            relative(value)

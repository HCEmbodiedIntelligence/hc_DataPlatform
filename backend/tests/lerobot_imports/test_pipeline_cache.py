from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq

from hc_data_platform.lerobot_imports.pipeline import LeRobotPipeline
from hc_data_platform.tools import hf_unitree_g1_to_mcap as reader


def test_later_episode_reclaims_old_chunks_in_the_same_pinned_source(tmp_path):
    info = {
        "data_path": "data/{file_index}.parquet",
        "video_path": "videos/{video_key}/{file_index}.mp4",
    }
    rows = []
    for index in range(2):
        row = {"episode_index": index, "data/chunk_index": 0, "data/file_index": index}
        for camera_index, camera in enumerate(reader.CAMERAS):
            row[f"videos/{camera.feature_key}/chunk_index"] = 0
            row[f"videos/{camera.feature_key}/file_index"] = 0 if camera_index == 0 else index
        rows.append(row)
    metadata = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows), metadata)
    originals = {
        "meta/info.json": json.dumps(info).encode(),
        "meta/episodes/chunk-000/file-000.parquet": metadata.getvalue().to_pybytes(),
    }
    for index in range(2):
        originals[f"data/{index}.parquet"] = bytes([index]) * 1000
        for camera in reader.CAMERAS:
            originals[f"videos/{camera.feature_key}/{index}.mp4"] = bytes([index]) * 20_000
    manifest = {
        "files": [
            {"path": path, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            for path, body in originals.items()
        ]
    }
    storage = Mock()
    storage.read_chunks.side_effect = lambda key: (originals[key.removeprefix("raw/")],)
    one_episode_bytes = sum(
        len(body) + 64 for path, body in originals.items() if path.startswith("meta/")
    )
    one_episode_bytes += 1064 + len(reader.CAMERAS) * 20_064
    pipeline = LeRobotPipeline(
        Mock(), storage, Mock(), Mock(), tmp_path, cache_max_bytes=one_episode_bytes + 100
    )
    raw = SimpleNamespace(content_hash="a" * 64, storage_prefix="raw")
    with pipeline.cache.pin(raw.content_hash):
        root = pipeline._localize(raw, manifest, 0)
    with pipeline.cache.pin(raw.content_hash):
        pipeline._localize(raw, manifest, 1)
        assert not (root / "data/0.parquet").exists()
        assert (root / "data/1.parquet").read_bytes() == originals["data/1.parquet"]
        assert (
            sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
            <= pipeline.cache.max_bytes
        )
    shared = f"raw/videos/{reader.CAMERAS[0].feature_key}/0.mp4"
    assert sum(call.args[0] == shared for call in storage.read_chunks.call_args_list) == 1
    assert all(
        hashlib.sha256(originals[item["path"]]).hexdigest() == item["sha256"]
        for item in manifest["files"]
    )
    storage.delete.assert_not_called()

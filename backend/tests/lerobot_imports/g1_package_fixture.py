"""Small synthetic legacy G1 source with four genuine H264 videos and split joints."""

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from hc_data_platform.data_formats.lerobot_g1 import CAMERAS
from hc_data_platform.data_formats.rgb_video import RgbVideoWriter
from tests.tools.test_hf_unitree_g1_to_mcap import _source_row


def make_native_g1(root: Path) -> Path:
    root.mkdir(parents=True)
    rows = [_source_row(i) for i in range(6)]
    table = pa.Table.from_pylist(rows)
    path = root / "data/chunk-000/file-000.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(table, path)
    info = {
        "codebase_version": "v3.0",
        "robot_type": "unitree_g1",
        "fps": 30,
        "total_episodes": 1,
        "total_frames": 6,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {key: {} for key in rows[0] if key.startswith(("action.", "observation."))},
    }
    episode = {
        "episode_index": 0,
        "length": 6,
        "tasks": ["synthetic G1"],
        "data/chunk_index": 0,
        "data/file_index": 0,
        "dataset_from_index": 0,
        "dataset_to_index": 6,
    }
    for index, camera in enumerate(CAMERAS):
        relative = f"videos/{camera.feature_key}/chunk-000/file-000.mp4"
        path = root / relative
        path.parent.mkdir(parents=True)
        writer = RgbVideoWriter(path, 30, (24, 32, 3))
        try:
            for frame in range(6):
                image = np.full((24, 32, 3), 30 + index * 30 + frame * 10, dtype=np.uint8)
                writer.write(image)
        finally:
            writer.close()
        info["features"][camera.feature_key] = {
            "dtype": "video",
            "shape": [24, 32, 3],
            "names": ["height", "width", "channels"],
            "info": {
                "video.fps": 30,
                "video.codec": "h264",
                "video.pix_fmt": "yuv420p",
                "video.channels": 3,
                "has_audio": False,
            },
        }
        prefix = f"videos/{camera.feature_key}"
        episode.update(
            {
                prefix + "/chunk_index": 0,
                prefix + "/file_index": 0,
                prefix + "/from_timestamp": 0.0,
                prefix + "/to_timestamp": 6 / 30,
            }
        )
    path = root / "meta/episodes/chunk-000/file-000.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([episode]), path)
    (root / "meta/info.json").write_text(json.dumps(info))
    return root

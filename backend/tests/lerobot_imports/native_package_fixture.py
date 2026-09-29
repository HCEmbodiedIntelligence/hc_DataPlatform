"""Small explicit synthetic RGB-D source; no sensor/real-recording claim."""

import json
from fractions import Fraction

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from hc_data_platform.publishing.depth_evidence import DepthEvidenceVideo
from hc_data_platform.publishing.holobrain_depth import DEFAULT_PROFILE


def make_native_rgbd(root):
    for directory in ("meta/episodes/chunk-000", "data/chunk-000"):
        (root / directory).mkdir(parents=True)
    count, fps = 6, 25
    features = {
        "observation.state": {"dtype": "float64", "shape": [2], "names": ["x", "y"]},
        "action": {"dtype": "float64", "shape": [2], "names": ["x", "y"]},
        "observation.extra.matrix": {"dtype": "float64", "shape": [2, 2], "names": None},
    }
    episode = {
        "episode_index": 0,
        "length": count,
        "tasks": ["synthetic package check"],
        "data/chunk_index": 0,
        "data/file_index": 0,
        "dataset_from_index": 0,
        "dataset_to_index": count,
    }
    for camera in ("head_rgb", "head_depth"):
        key = "observation.images." + camera
        path = root / f"videos/{key}/chunk-000/file-000.mp4"
        path.parent.mkdir(parents=True)
        is_depth = camera.endswith("depth")
        features[key] = {
            "dtype": "video",
            "shape": [16, 16, 1 if is_depth else 3],
            "info": {**(DEFAULT_PROFILE.metadata() if is_depth else {}), "video.fps": fps},
        }
        for suffix, value in (
            ("chunk_index", 0),
            ("file_index", 0),
            ("from_timestamp", 0.0),
            ("to_timestamp", count / fps),
        ):
            episode[f"videos/{key}/{suffix}"] = value
        if is_depth:
            writer = DepthEvidenceVideo(path, root / "hc/depth/head", fps, (16, 16))
            for index in range(count):
                pixels = np.full((16, 16), 3000 + index * 10, dtype=np.uint16)
                pixels[0, 0] = 0
                writer.write(
                    pixels,
                    0.00025,
                    source_id="synthetic-head",
                    source_seq=index,
                    source_timestamp_ns=index * 40_000_000,
                    target_timestamp_ns=index * 40_000_000,
                )
            writer.close()
        else:
            with av.open(str(path), "w") as container:
                encoder = container.add_stream("libx265", rate=fps)
                encoder.width = encoder.height = 16
                encoder.pix_fmt = "yuv420p"
                encoder.options = {
                    "x265-params": "bframes=0:pools=none:frame-threads=1:log-level=error"
                }
                for index in range(count):
                    pixels = np.full((16, 16, 3), 60 + index * 20, dtype=np.uint8)
                    frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                    frame.pts, frame.time_base = index, Fraction(1, fps)
                    for packet in encoder.encode(frame):
                        container.mux(packet)
                for packet in encoder.encode():
                    container.mux(packet)
    table = pa.table(
        {
            "index": pa.array(range(count), pa.int64()),
            "frame_index": pa.array(range(count), pa.int64()),
            "episode_index": pa.array([0] * count, pa.int64()),
            "task_index": pa.array([0] * count, pa.int64()),
            "timestamp": pa.array([i / fps for i in range(count)], pa.float32()),
            "observation.state": pa.array(
                [[0.1 + i * 1e-12, 0.2] for i in range(count)], pa.list_(pa.float64(), 2)
            ),
            "action": pa.array(
                [[0.01 * i, 0.02 * i] for i in range(count)], pa.list_(pa.float64(), 2)
            ),
            "observation.extra.matrix": pa.array(
                [[[0.1 + i * 1e-12, 0.2], [0.3, 0.4]] for i in range(count)],
                pa.list_(pa.list_(pa.float64(), 2), 2),
            ),
        }
    )
    for key in ("index", "frame_index", "episode_index", "task_index", "timestamp"):
        features[key] = {
            "dtype": "float32" if key == "timestamp" else "int64",
            "shape": [1],
            "names": None,
        }
    pq.write_table(table, root / "data/chunk-000/file-000.parquet")
    pq.write_table(
        pa.Table.from_pylist([episode]), root / "meta/episodes/chunk-000/file-000.parquet"
    )
    from hc_data_platform.publishing.lerobot_format import make_task_table

    pq.write_table(make_task_table(["synthetic package check"]), root / "meta/tasks.parquet")
    info = {
        "codebase_version": "v3.0",
        "fps": fps,
        "robot_type": "generic",
        "features": features,
        "total_episodes": 1,
        "total_frames": count,
        "total_tasks": 1,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
    }
    (root / "meta/info.json").write_text(json.dumps(info))
    (root / "hc/modalities.json").write_text(
        json.dumps({"depth": {"observation.images.head_depth": {"evidence_path": "hc/depth/head"}}})
    )
    (root / "hc/calibration.json").write_text(
        json.dumps({"status": "unknown", "reason": "synthetic"})
    )
    return root

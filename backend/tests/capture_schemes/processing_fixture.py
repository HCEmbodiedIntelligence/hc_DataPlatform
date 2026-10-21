"""Synthetic values with exact, read-only-inventoried X1 source definitions."""

import copy
import json
from contextlib import closing
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from hc_data_platform.capture_schemes.common import digest
from hc_data_platform.capture_schemes.models import OutputContract
from hc_data_platform.data_formats.rgb_video import RgbVideoWriter

SOURCES = json.loads((Path(__file__).parent / "fixtures/x1_sources.json").read_text())["sources"]


def output_for(info):
    fields = {}
    for key, value in info["features"].items():
        if key in {"timestamp", "frame_index", "episode_index", "index", "task_index"}:
            continue
        role = (
            "state"
            if key == "observation.state"
            else "action"
            if key == "action"
            else "rgb"
            if value["dtype"] == "video"
            else "validity"
            if key.endswith(("source_valid", "source_available"))
            else "numeric"
        )
        fields[key] = {
            **{k: value[k] for k in ("dtype", "shape", "names")},
            "role": role,
            "media": value.get("info", {}),
        }
        if role in {"state", "action"}:
            # Inventory has no source unit evidence. These are explicit test-only
            # declarations, not a claim that production joints use radians.
            fields[key]["units"] = ["fixture_source_unit"] * value["shape"][0]
    return OutputContract(frequency_hz=info["fps"], time_source="FRAME_TIMESTAMP", features=fields)


def snapshot(output, *, scope=("org", "project", "global"), raw=None):
    raw = raw or {
        "dataset_id": "dataset_capture_test",
        "collection_task_id": "task",
        "robot_id": "x1",
        "source_format": "LEROBOT_V3",
    }
    content = {
        "schema_version": "capture-recording-snapshot/v1",
        "organization_id": scope[0],
        "project_id": scope[1],
        "region_code": scope[2],
        **raw,
        "recording_id": "recording",
        "plan_revision": 1,
        "plan_sha256": "a" * 64,
        "scheme": {"scheme_id": "x1", "version": 1, "content_sha256": "b" * 64},
        "mapping_version": 1,
        "mapping_sha256": "c" * 64,
        "effective_adapter": {
            "source_format": "LEROBOT_V3",
            "parser_version": "lerobot-v3/1",
            "codebase_version": "v3.0",
            "feature_sources": {},
        },
        "output": output.model_dump(mode="json"),
        "output_contract_sha256": digest(output.model_dump(mode="json")),
        "source": "TASK_PLAN",
        "prepared_at": "2026-10-21T00:00:00Z",
    }
    return {**content, "snapshot_sha256": digest(content)}


def make_source(root, variant=0, *, count=167):
    root.mkdir(parents=True)
    info = copy.deepcopy(SOURCES[variant]["info"])
    info.update(total_frames=count, total_episodes=1, total_tasks=1)
    (root / "meta/episodes/chunk-000").mkdir(parents=True)
    (root / "data/chunk-000").mkdir(parents=True)
    metadata = {
        "episode_index": 0,
        "tasks": ["synthetic X1"],
        "length": count,
        "dataset_from_index": 0,
        "dataset_to_index": count,
        "data/chunk_index": 0,
        "data/file_index": 0,
        "meta/episodes/chunk_index": 0,
        "meta/episodes/file_index": 0,
    }
    columns = {}
    for key, feature in info["features"].items():
        if feature["dtype"] == "video":
            path = info["video_path"].format(video_key=key, chunk_index=0, file_index=0)
            target = root / path
            target.parent.mkdir(parents=True)
            with closing(RgbVideoWriter(target, info["fps"], feature["shape"])) as writer:
                for index in range(count):
                    pixels = np.full(feature["shape"], (index * 13) % 190 + 30, dtype=np.uint8)
                    writer.write(pixels)
            for suffix, value in {
                "chunk_index": 0,
                "file_index": 0,
                "from_timestamp": 0.0,
                "to_timestamp": count / info["fps"],
            }.items():
                metadata[f"videos/{key}/{suffix}"] = value
            continue
        dtype = pa.from_numpy_dtype(feature["dtype"])
        if key in {"timestamp", "frame_index", "episode_index", "index", "task_index"}:
            values = [
                i / info["fps"]
                if key == "timestamp"
                else i
                if key in {"index", "frame_index"}
                else 0
                for i in range(count)
            ]
        else:
            dtype = pa.list_(dtype, feature["shape"][0])
            values = [
                [
                    1
                    if key.endswith(("source_available", "source_valid"))
                    else i / info["fps"]
                    if "time" in key
                    else i * 0.0001 + axis * 0.001
                    for axis in range(feature["shape"][0])
                ]
                for i in range(count)
            ]
        columns[key] = pa.array(values, type=dtype)
    pq.write_table(pa.table(columns), root / "data/chunk-000/file-000.parquet")
    pq.write_table(
        pa.Table.from_pylist([metadata]), root / "meta/episodes/chunk-000/file-000.parquet"
    )
    pq.write_table(
        pa.Table.from_pylist([{"task_index": 0, "task": "synthetic X1"}]),
        root / "meta/tasks.parquet",
    )
    (root / "meta/info.json").write_text(json.dumps(info))
    return info

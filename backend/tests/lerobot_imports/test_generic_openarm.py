from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from hc_data_platform.lerobot_imports.adapter import LeRobotAdapter
from hc_data_platform.lerobot_imports.capture import verify_completion
from hc_data_platform.lerobot_imports.orchestration import LeRobotEpisodeSourceRefV1
from hc_data_platform.lerobot_imports.profiles import quality_profile
from hc_data_platform.lerobot_imports.reader import read_episode
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.tools.lerobot_platform_upload import build_native_source

FIXTURES = Path(__file__).parents[1] / "fixtures/openarm-g0"


@pytest.mark.parametrize(
    "name,width,cameras", [("valid-openarm", 16, 3), ("valid-generic", 2, 1), ("a-actual", 16, 3)]
)
def test_real_source_to_quality_and_numeric_streams(name, width, cameras):
    root = FIXTURES / name
    verify_completion(root)
    source = build_native_source(
        root, dataset_id="dataset", collection_task_id="task", robot_id="robot"
    )
    assert source.manifest.processing_mode == "PROCESS"
    original_files = {
        path: (f"raw/{path}", p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
        for path, p in source.files.items()
    }
    for index in range(source.manifest.episode_count):
        ref = LeRobotEpisodeSourceRefV1(
            raw_upload_id="a" * 32, raw_manifest_key="raw/manifest.json", episode_index=index
        )
        with LeRobotAdapter().open_episode(
            root, ref, raw_manifest_sha256="b" * 64, original_files=original_files
        ) as stream:
            assert len(stream.original_videos) == cameras
            assert len(stream.profile.state_names) == width
            rows = pq.read_table(
                stream.layout.data_file, filters=[("episode_index", "=", index)]
            ).to_pylist()
            samples = list(stream.alignment_samples())
            actions = [
                s.value["values"] for topic, s in samples if topic == stream.profile.action_topic
            ]
            states = [
                s.value["positions"] for topic, s in samples if topic == stream.profile.joint_topic
            ]
            np.testing.assert_array_equal(actions, [r["action"] for r in rows])
            np.testing.assert_array_equal(states, [r["observation.state"] for r in rows])
            assert stream.episode.source_episode["source_episode_id"]
            report = QualityEngine().evaluate_stream(
                stream.quality_input,
                stream.quality_observations(),
                quality_profile(stream.profile, int(stream.episode.fps)),
            )
            assert report.status.value == "PASS", report.findings


@pytest.mark.parametrize(
    "name",
    [
        "wrong-dimension",
        "future-action",
        "single-arm-paused",
        "invalid-segment",
        "missing-video",
        "wrong-hash",
        "incomplete-export",
    ],
)
def test_public_negative_fixtures_are_rejected(name):
    with pytest.raises((ValueError, FileNotFoundError)):
        root = FIXTURES / name
        verify_completion(root)
        for index in (0, 1):
            read_episode(root, index)


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("names", "FEATURE"),
        ("column", "COLUMN_MISSING"),
        ("timestamp", "TIME_GRID"),
        ("video_range", "VIDEO_RANGE"),
        ("length", "EPISODE_OFFSETS"),
        ("corrupt_video", "VIDEO_DECODE"),
        ("metadata", "METADATA_INVALID"),
        ("units", "FEATURE_UNITS"),
        ("source_clock", "SOURCE_TIME_GRID"),
        ("clock_epoch", "SOURCE_TIME_GRID"),
    ],
)
def test_structural_diagnostics(tmp_path, mutation, code):
    import pyarrow as pa

    root = tmp_path / "source"
    shutil.copytree(FIXTURES / "valid-generic", root)
    if mutation in {"names", "units"}:
        path = root / "meta/info.json"
        info = json.loads(path.read_text())
        info["features"]["action"][mutation] = ["x", "x"] if mutation == "names" else ["m"]
        path.write_text(json.dumps(info))
    elif mutation == "metadata":
        path = root / "capture-context.json"
        context = json.loads(path.read_text())
        del context["episodes"]
        path.write_text(json.dumps(context))
    elif mutation == "corrupt_video":
        next((root / "videos").rglob("*.mp4")).write_bytes(b"invalid-video")
    elif mutation in {"source_clock", "clock_epoch"}:
        path = root / "source_mapping.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[1]["target_ns" if mutation == "source_clock" else "clock_epoch"] += 1_000
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    elif mutation in {"column", "timestamp"}:
        path = root / "data/chunk-000/file-000.parquet"
        table = pq.read_table(path)
        if mutation == "column":
            table = table.drop(["action"])
        else:
            values = table["timestamp"].to_pylist()
            values[1] = 0.8
            table = table.set_column(
                table.schema.get_field_index("timestamp"),
                "timestamp",
                pa.array(values, type=pa.float32()),
            )
        pq.write_table(table, path)
    else:
        path = root / "meta/episodes/chunk-000/file-000.parquet"
        table = pq.read_table(path)
        rows = table.to_pylist()
        if mutation == "length":
            rows[0]["length"] += 1
        else:
            rows[0]["videos/observation.images.single/to_timestamp"] += 0.5
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    with pytest.raises(ValueError, match=code):
        read_episode(root, 0)

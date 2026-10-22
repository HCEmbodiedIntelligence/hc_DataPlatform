import json
import os
import shutil
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pyarrow.parquet as pq
import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.episode_store.models import EpisodeIdentity, FileEntry, PackageInput
from hc_data_platform.episode_store.prepared_assets import PreparedAssets, PreparedVideo
from hc_data_platform.episode_store.validation import validate_package
from hc_data_platform.episode_store.writer import write_episode
from hc_data_platform.lance_catalog.models import StepRecord
from hc_data_platform.publishing.depth_evidence import DepthEvidenceVideo, file_hash
from hc_data_platform.publishing.holobrain_depth import DEPTH_INFO
from hc_data_platform.publishing.lerobot_export import DATA_PATH, VIDEO_PATH


def identity():
    return EpisodeIdentity(
        organization_id="writer-org",
        project_id="writer-project",
        region_code="writer-region",
        dataset_id="writer-dataset",
        episode_id="writer-episode",
        rollout_id="explicit:writer-rollout",
    )


def test_streaming_numeric_precision_and_row_group_boundary(tmp_path):
    ident = identity()
    source = PackageInput(
        identity=ident,
        source_sha256="1" * 64,
        processor_version="test/1",
        configuration_sha256="2" * 64,
        schema_snapshot_id="schema-1",
        required_features=("observation.state",),
    )
    token = bind_request_context(
        RequestContext(
            organization_id=ident.organization_id,
            project_id=ident.project_id,
            region_code=ident.region_code,
        )
    )
    try:
        rows = (
            StepRecord(
                rollout_id=ident.rollout_id,
                step_index=i,
                timestamp_ns=1_790_000_000_000_000_001 + i * 33_333_333,
                modalities={"/observation/state": [0.1 + i * 1e-12]},
                valid={"/observation/state": True},
            )
            for i in range(2051)
        )
        write_episode(
            tmp_path / "episode",
            source,
            rows,
            PreparedAssets(ident, "attempt", {}),
            fps=30,
            metadata={"task": "numeric fidelity"},
        )
    finally:
        reset_request_context(token)
    parquet = pq.ParquetFile(tmp_path / "episode" / DATA_PATH)
    assert parquet.metadata.num_row_groups == 3
    table = parquet.read()
    assert table["observation.state"].to_pylist() == [0.1 + i * 1e-12 for i in range(2051)]
    info = json.loads((tmp_path / "episode/meta/info.json").read_bytes())
    assert info["hc.source_shapes"]["observation.state"] == [1]
    assert table["hc.timestamp_ns"][2050].as_py() == 1_790_000_000_000_000_001 + 2050 * 33_333_333


@pytest.mark.parametrize("false_validity", [False, True])
def test_frozen_numeric_type_keeps_initial_missing_axes_and_rejects_false_validity(
    tmp_path, false_validity
):
    from hc_data_platform.capture_schemes.models import OutputContract

    ident = identity()
    source = PackageInput(
        identity=ident,
        source_sha256="1" * 64,
        processor_version="test/1",
        configuration_sha256="2" * 64,
        schema_snapshot_id="schema-1",
        required_features=("observation.state",),
    )
    contract = OutputContract(
        frequency_hz=15,
        time_source="FRAME_TIMESTAMP",
        features={
            "observation.state": {
                "dtype": "float32",
                "shape": [2],
                "names": ["a", "b"],
                "units": ["rad", "rad"],
                "role": "state",
            },
        },
    ).model_dump(mode="json")
    rows = [
        StepRecord(
            rollout_id=ident.rollout_id,
            step_index=i,
            timestamp_ns=round(i * 1e9 / 15),
            modalities={
                "/humanoid/observation/state": {
                    "values": [None, 0.0] if i == 0 else [0.5, 0.0],
                    "names": ["a", "b"],
                    "units": ["rad", "rad"],
                    "validity": [false_validity or i > 0, True],
                }
            },
            valid={"/humanoid/observation/state": True},
        )
        for i in range(3)
    ]
    token = bind_request_context(
        RequestContext(
            organization_id=ident.organization_id,
            project_id=ident.project_id,
            region_code=ident.region_code,
        )
    )
    root = tmp_path / "episode"
    try:
        if false_validity:
            with pytest.raises(ValueError, match="missing numerical value is marked valid"):
                write_episode(
                    root,
                    source,
                    rows,
                    PreparedAssets(ident, "attempt", {}),
                    fps=15,
                    metadata={"capture": {"output": contract}},
                )
            return
        write_episode(
            root,
            source,
            rows,
            PreparedAssets(ident, "attempt", {}),
            fps=15,
            metadata={"capture": {"output": contract}},
        )
    finally:
        reset_request_context(token)
    table = pq.read_table(root / DATA_PATH)
    assert table["observation.state"].to_pylist() == [[None, 0.0], [0.5, 0.0], [0.5, 0.0]]
    assert str(table["observation.state"].type) == "fixed_size_list<element: float>[2]"
    assert table["hc.valid.observation.state"].to_pylist() == [False, True, True]
    assert json.loads(table["hc.axis_validity.observation.state"][0].as_py()) == [False, True]
    assert json.loads((root / "meta/stats.json").read_text())["observation.state"]["mean"] == [
        0.5,
        0.0,
    ]


def build_rgbd_package(
    tmp_path,
    ident=None,
    *,
    mcap_topics=False,
    rgb_codec="libx265",
    source_profile=None,
    intensity_offset=0,
    depth_offset=0,
    depth_missing_frames=(),
    depth_invalid_frames=(),
    task="synthetic RGB-D codec acceptance",
):
    ident = ident or identity()
    rgb, depth = "observation.images.head_rgb", "observation.images.head_depth"
    source = PackageInput(
        identity=ident,
        source_sha256="1" * 64,
        processor_version="test/1",
        configuration_sha256="2" * 64,
        schema_snapshot_id="schema-1",
        required_features=("observation.state", "action", rgb, depth),
        required_depth_features=(depth,),
    )
    input_root = tmp_path / "prepared"
    input_root.mkdir()
    rgb_path = input_root / "rgb-h265.mp4"
    depth_path, evidence = input_root / "depth.mp4", input_root / "depth"
    with av.open(str(rgb_path), "w") as container:
        stream = container.add_stream(rgb_codec, rate=30)
        stream.width = stream.height = 16
        stream.pix_fmt = "yuv420p"
        stream.options = (
            {"x265-params": "bframes=0:pools=none:frame-threads=1:log-level=error"}
            if rgb_codec == "libx265"
            else {"bf": "0", "threads": "1"}
        )
        for index in range(4):
            frame = av.VideoFrame.from_ndarray(
                np.full((16, 16, 3), intensity_offset + index * 50, dtype=np.uint8), format="rgb24"
            )
            frame.pts, frame.time_base = index, Fraction(1, 30)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    writer = DepthEvidenceVideo(
        depth_path, evidence, 30, (16, 16), require_source_profile=source_profile is not None
    )
    for index in range(4):
        pixels = np.full((16, 16), depth_offset + 500 + index * 500, dtype=np.uint16)
        pixels[0, 0] = 0
        if index in depth_invalid_frames:
            pixels.fill(0)
        missing = index in depth_missing_frames
        writer.write(
            None if missing else pixels,
            None if missing else 0.001,
            source_id="head",
            source_seq=None if missing else index,
            source_timestamp_ns=None if missing else index * 33_333_333,
            target_timestamp_ns=index * 33_333_333,
            missing=missing,
            source_profile=None if missing else source_profile,
            invalid_values=source_profile["invalid_values"] if source_profile else (),
        )
    writer.close()
    evidence_files = tuple(
        FileEntry(
            path=p.relative_to(evidence).as_posix(),
            size=p.stat().st_size,
            sha256=file_hash(p),
            media_type="application/octet-stream",
        )
        for p in sorted(evidence.rglob("*"))
        if p.is_file()
    )
    assets = PreparedAssets(
        ident,
        "attempt-1",
        {
            rgb: PreparedVideo(
                rgb_path, file_hash(rgb_path), rgb_path.stat().st_size, {"shape": [16, 16, 3]}
            ),
            depth: PreparedVideo(
                depth_path,
                file_hash(depth_path),
                depth_path.stat().st_size,
                {"shape": [16, 16, 1], "info": DEPTH_INFO},
                evidence,
                evidence_files,
            ),
        },
    )
    rows = [
        StepRecord(
            rollout_id=ident.rollout_id,
            step_index=i,
            timestamp_ns=i * 33_333_333,
            modalities={
                "/observation/state": [0.1 + i * 1e-12],
                "action": [float(i)],
                rgb: {"frame_index": i},
                depth: {"frame_index": i},
            },
            valid={
                name: name != depth or i not in depth_missing_frames
                for name in ("/observation/state", "action", rgb, depth)
            },
            source_timestamps_ns={depth: () if i in depth_missing_frames else (i * 33_333_333,)},
            time_error_ns={depth: None if i in depth_missing_frames else 0},
        )
        for i in range(4)
    ]
    if mcap_topics:
        names = {
            rgb: "/camera/head_rgb/image",
            depth: "/camera/head_depth/image",
            "action": "/action",
        }
        rows = [
            row.model_copy(
                update={
                    field: {
                        names.get(key, key): value for key, value in getattr(row, field).items()
                    }
                    for field in ("modalities", "valid", "source_timestamps_ns", "time_error_ns")
                }
            )
            for row in rows
        ]
    token = bind_request_context(
        RequestContext(
            organization_id=ident.organization_id,
            project_id=ident.project_id,
            region_code=ident.region_code,
        )
    )
    try:
        output = tmp_path / "episode"
        manifest = write_episode(
            output,
            source,
            rows,
            assets,
            fps=30,
            metadata={"task": task},
        )
    finally:
        reset_request_context(token)
    return output, source, manifest, assets


@pytest.mark.parametrize("missing", [True, False])
def test_zero_measured_depth_has_no_fabricated_statistics_and_rejects_false_claims(
    tmp_path, missing
):
    root, source, _, _ = build_rgbd_package(
        tmp_path,
        depth_missing_frames=range(4) if missing else (),
        depth_invalid_frames=() if missing else range(4),
    )
    depth = "observation.images.head_depth"
    info_path, stats_path = root / "meta/info.json", root / "meta/stats.json"
    original_info, original_stats = info_path.read_bytes(), stats_path.read_bytes()
    info, stats = json.loads(original_info), json.loads(original_stats)
    assert depth not in stats
    assert info["features"][depth]["info"]["hc.stats_status"] == "NO_VALID_PIXELS"
    report = json.loads((root / "hc/depth" / depth / "report.json").read_bytes())
    assert report["measurement_status"] == ("NO_SOURCE_FRAMES" if missing else "NO_VALID_PIXELS")
    assert report["quantization"]["unclipped_max_error_m"] is None
    for tamper in ("absent-marker", "invented-mean", "invented-count"):
        info, stats = json.loads(original_info), json.loads(original_stats)
        if tamper == "absent-marker":
            del info["features"][depth]["info"]["hc.stats_status"]
        elif tamper == "invented-mean":
            stats[depth] = {"mean": [[[10.0]]]}
        else:
            info["features"][depth]["info"]["hc.stats_valid_pixels"] = 1024
        info_path.write_text(json.dumps(info))
        stats_path.write_text(json.dumps(stats))
        with pytest.raises(ValueError, match="depth statistics"):
            validate_package(root, source)
    info_path.write_bytes(original_info)
    stats_path.write_bytes(original_stats)


def test_standalone_rgbd_writer_reuses_h265_and_depth_without_reencoding(tmp_path):
    output, source, manifest, assets = build_rgbd_package(tmp_path)
    depth = "observation.images.head_depth"
    assert manifest.frame_count == 4
    for key, media in assets.videos.items():
        path = media.path
        assert file_hash(
            output / VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0)
        ) == file_hash(path)
    stats = json.loads((output / "meta/stats.json").read_bytes())
    # Invalid fill at (0,0) is not included as a measured depth_min in stats.
    assert stats[depth]["min"][0][0][0] > 490
    evidence_root = os.environ.get("HC_G1_WRITER_EVIDENCE_ROOT")
    if evidence_root:
        destination = Path(evidence_root) / "standalone-rgbd"
        if destination.exists():
            destination = Path(evidence_root) / ("standalone-rgbd-" + manifest.content_hash[:16])
        if not destination.exists():
            shutil.copytree(output, destination)
        (Path(evidence_root) / "standalone-rgbd-latest.txt").write_text(str(destination) + "\n")
        print("Official reader input:", destination)
    # Valid encoded bytes from a different time window must not be reused merely
    # because resolution/fps/profile/frame count happen to match.
    path = output / DATA_PATH
    table = pq.read_table(path)
    column = table.schema.get_field_index("hc.timestamp_ns")
    import pyarrow as pa

    table = table.set_column(
        column, "hc.timestamp_ns", pa.array([1_000_000_000 + i * 33_333_333 for i in range(4)])
    )
    pq.write_table(table, path)
    with pytest.raises(ValueError, match="different Episode time grid"):
        validate_package(output, source)

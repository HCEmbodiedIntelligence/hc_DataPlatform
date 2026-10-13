"""Real HEVC selections and package validation; synthetic, not sensor acceptance."""

import json
from dataclasses import replace
from fractions import Fraction

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hc_data_platform.data_formats import depth_evidence, holobrain_depth
from hc_data_platform.episode_store.depth_window import DepthWindow, prepare_depth_window
from hc_data_platform.episode_store.models import FileEntry
from hc_data_platform.episode_store.prepared_assets import PreparedVideo
from hc_data_platform.episode_store.validation import parquet_rows, validate_depth
from hc_data_platform.publishing.depth_evidence import DepthEvidenceVideo, file_hash, read_validity
from hc_data_platform.publishing.holobrain_depth import DEFAULT_PROFILE

START = 1_790_000_000_000_000_001


def inventory(root):
    return tuple(
        FileEntry(
            path=p.relative_to(root).as_posix(),
            size=p.stat().st_size,
            sha256=file_hash(p),
            media_type="application/octet-stream",
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
    )


def make_depth(root, *, fps=25, count=9, missing_reference=False):
    root.mkdir()
    path, evidence = root / "video.mp4", root / "evidence"
    profile = dict(
        schema_version="hc-depth-source/1",
        encoding="16UC1",
        unit="source_unit",
        scale_m_per_unit=0.00025,
        invalid_values=[0, 65535],
        invalid_nonfinite=True,
        registered_to_rgb=False,
        registration_evidence="synthetic independent grid",
        evidence=dict(kind="synthetic_fixture", reference=__name__, version="1"),
    )
    writer = DepthEvidenceVideo(path, evidence, fps, (16, 16), require_source_profile=True)
    for index in range(count):
        # Invalidity and saturation must survive independently of code zero.
        source_index = 3 if index == 4 else index
        pixels = np.full((16, 16), 4000 + source_index, dtype=np.uint16)
        pixels[0, :4] = [0, 65535, 5, 48000]
        missing = index == 2
        writer.write(
            None if missing else pixels,
            None if missing else 0.00025,
            source_id="synthetic-sensor",
            source_seq=None if missing else source_index,
            source_timestamp_ns=None
            if missing
            else START + round(Fraction(source_index * 10**9, fps)),
            target_timestamp_ns=None if missing and missing_reference else START + round(Fraction(index * 10**9, fps)),
            missing=missing,
            max_time_error_ns=15_000_000,
            source_profile=None if missing else profile,
            invalid_values=() if missing else profile["invalid_values"],
        )
    writer.close()
    return PreparedVideo(
        path,
        file_hash(path),
        path.stat().st_size,
        {
            "dtype": "video",
            "shape": [16, 16, 1],
            "info": {**DEFAULT_PROFILE.metadata(), "video.fps": fps},
        },
        evidence,
        inventory(evidence),
    )


def decoded(path):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.format.name == "gray12le"
        return [frame.to_ndarray(format="gray12le") for frame in container.decode(stream)]


def forbid(*args, **kwargs):
    raise AssertionError("a verified 12-bit stream must not be requantized")


def forbid_quantization(monkeypatch):
    monkeypatch.setattr(depth_evidence, "quantize", forbid)
    monkeypatch.setattr(holobrain_depth, "quantize", forbid)
    monkeypatch.setattr(holobrain_depth.DepthVideo, "write", forbid)


def test_identical_stream_reuses_every_byte_and_no_encoder(tmp_path, monkeypatch):
    media = make_depth(tmp_path / "input", fps=30)
    before = inventory(tmp_path / "input")
    forbid_quantization(monkeypatch)
    monkeypatch.setattr(holobrain_depth.DepthVideo, "__init__", forbid)
    output = tmp_path / "output"
    result = prepare_depth_window(media, DepthWindow(0, 9, 9, 30, START, "nearest"), output)
    assert result is media
    assert not output.exists()
    assert before == inventory(tmp_path / "input")


@pytest.mark.parametrize("first,stop", [(0, 2), (2, 3)])
def test_missing_rgb_reference_in_raw_evidence_does_not_block_valid_cuts(tmp_path, first, stop):
    media = make_depth(tmp_path / "input", fps=30, missing_reference=True)
    before = inventory(tmp_path / "input")
    result = prepare_depth_window(media, DepthWindow(first, stop, stop-first, 30, START),
                                  tmp_path / "output")
    rows = list(parquet_rows(result.evidence_root / "frames.parquet"))
    assert all(type(row["target_timestamp_ns"]) is int for row in rows)
    assert rows[0]["missing"] is (first == 2)
    if first == 2:
        assert rows[0]["parent_target_timestamp_ns"] is None
        assert rows[0]["source_timestamp_ns"] is None
    assert before == inventory(tmp_path / "input")
    validate_depth(result.evidence_root.parent, result.evidence_root.name, result.path,
        result.feature, stop-first, 30,
        target_rows=iter({"hc.timestamp_ns": row["target_timestamp_ns"]} for row in rows),
        feature_key="depth")


def test_final_episode_cannot_claim_a_null_time_even_for_missing_depth(tmp_path):
    media = make_depth(tmp_path / "input", fps=30, missing_reference=True)
    with pytest.raises(ValueError, match="temporal status"):
        validate_depth(media.evidence_root.parent, media.evidence_root.name, media.path,
            media.feature, 9, 30,
            target_rows=({"hc.timestamp_ns": row["target_timestamp_ns"]}
                         for row in parquet_rows(media.evidence_root / "frames.parquet")),
            feature_key="depth")


def test_missing_only_window_is_explicit_and_reuses_codes_without_inventing_a_source(
    tmp_path, monkeypatch
):
    media = make_depth(tmp_path / "input")
    before = inventory(tmp_path / "input")
    forbid_quantization(monkeypatch)
    result = prepare_depth_window(
        media, DepthWindow(2, 3, 1, 25, START + 80_000_000), tmp_path / "missing"
    )
    assert not decoded(result.path)[0].any()
    assert before == inventory(tmp_path / "input")
    report = json.loads((result.evidence_root / "report.json").read_bytes())
    assert report["measurement_status"] == "NO_SOURCE_FRAMES"
    assert report["totals"]["missing_frames"] == report["frames"] == 1
    assert report["quantization"]["unclipped_max_error_m"] is None
    monkeypatch.setattr(holobrain_depth.DepthVideo, "__init__", forbid)
    assert (
        prepare_depth_window(
            result, DepthWindow(0, 1, 1, 25, START + 80_000_000), tmp_path / "reuse"
        )
        is result
    )
    # Exact-byte reuse still validates measured/absent semantics independently.
    with pytest.raises(ValueError, match="claims measurement/source"):
        validate_depth(
            result.evidence_root.parent,
            result.evidence_root.name,
            result.path,
            result.feature,
            1,
            25,
            target_rows=iter([{"hc.timestamp_ns": START + 80_000_000, "hc.valid.depth": True}]),
            feature_key="depth",
        )
    report["measurement_status"] = "NO_VALID_PIXELS"
    (result.evidence_root / "report.json").write_text(json.dumps(report))
    forged = replace(result, evidence_files=inventory(result.evidence_root))
    with pytest.raises(ValueError, match="completeness"):
        prepare_depth_window(
            forged, DepthWindow(0, 1, 1, 25, START + 80_000_000), tmp_path / "forged"
        )


def test_parent_time_cut_keeps_original_nanoseconds_and_avoids_rounding_shift(
    tmp_path, monkeypatch
):
    media = make_depth(tmp_path / "input", fps=30)
    rows = list(parquet_rows(media.evidence_root / "frames.parquet"))
    window = DepthWindow(1, 6, 5, 30.0, rows[1]["target_timestamp_ns"])
    assert window.target_time(1) != rows[2]["target_timestamp_ns"]
    forbid_quantization(monkeypatch)
    result = prepare_depth_window(media, window, tmp_path / "output", preserve_parent_times=True)
    selected = list(parquet_rows(result.evidence_root / "frames.parquet"))
    assert [r["target_timestamp_ns"] for r in selected] == [
        r["target_timestamp_ns"] for r in rows[1:6]
    ]
    assert [r["source_timestamp_ns"] for r in selected] == [
        r["source_timestamp_ns"] for r in rows[1:6]
    ]
    np.testing.assert_array_equal(decoded(result.path), decoded(media.path)[1:6])
    assert selected[0]["repeated"] is False
    with pytest.raises(ValueError, match="without resampling"):
        prepare_depth_window(
            media, DepthWindow(1, 6, 3, 15, START), tmp_path / "bad", preserve_parent_times=True
        )


def test_fractional_frame_rate_uses_exact_rational_pts(tmp_path):
    from hc_data_platform.continuous_recordings.openarm_raw.video import TrainingVideo
    from hc_data_platform.publishing.export_video import inspect_depth_video

    fps = 29.97
    video = tmp_path / "depth.mp4"
    writer = holobrain_depth.DepthVideo(video, fps, (16, 16))
    for _ in range(3):
        writer.write_codes(np.full((16, 16), 2000, dtype=np.uint16))
    writer.close()
    feature, _ = inspect_depth_video(video, 3, fps, DEFAULT_PROFILE.metadata())
    assert feature["info"]["video.fps"] == fps
    rgb = tmp_path / "rgb.mp4"
    writer = TrainingVideo(rgb, fps, (16, 16, 3))
    for _ in range(3):
        writer.write(np.full((16, 16, 3), 100, dtype=np.uint8))
    writer.close()
    for path in (video, rgb):
        with av.open(str(path)) as container:
            assert container.streams.video[0].average_rate == Fraction(2997, 100)
            assert [frame.pts * frame.time_base for frame in container.decode(video=0)] == [
                Fraction(i * 100, 2997) for i in range(3)
            ]


@pytest.mark.parametrize(
    "fps,window,selected",
    [
        (25, DepthWindow(1, 8, 7, 30, START + 40_000_000), [1, 2, 3, 3, 4, 5, 6]),
        (30, DepthWindow(1, 9, 4, 15, START + 33_333_333), [1, 3, 5, 7]),
        (25, DepthWindow(3, 6, 3, 25, START + 120_000_000), [3, 4, 5]),
        (
            25,
            DepthWindow(2, 8, 6, 30, START + 60_000_000, source_start_offset_ns=60_000_000),
            [2, 2, 3, 4, 5, 6],
        ),
    ],
)
def test_selection_preserves_codes_masks_source_units_and_measured_errors(
    tmp_path, monkeypatch, fps, window, selected
):
    media = make_depth(tmp_path / "input", fps=fps)
    before = inventory(tmp_path / "input")
    original_codes = decoded(media.path)
    original_rows = list(parquet_rows(media.evidence_root / "frames.parquet"))
    original_masks = list(parquet_rows(media.evidence_root / "validity/flags.parquet"))
    original_report = json.loads((media.evidence_root / "report.json").read_bytes())
    forbid_quantization(monkeypatch)
    result = prepare_depth_window(media, window, tmp_path / "output")
    np.testing.assert_array_equal(decoded(result.path), [original_codes[i] for i in selected])
    rows = list(parquet_rows(result.evidence_root / "frames.parquet"))
    masks = list(parquet_rows(result.evidence_root / "validity/flags.parquet"))
    assert [row["parent_frame_index"] for row in rows] == selected
    assert [row["target_timestamp_ns"] for row in rows] == [
        window.target_time(i) for i in range(len(selected))
    ]
    for index, selected_index in enumerate(selected):
        for key in (
            "source_id",
            "source_seq",
            "source_timestamp_ns",
            "depth_scale_m_per_unit",
            "source_profile_sha256",
            "missing",
            "unclipped_max_error_m",
            "saturated_low_pixels",
            "saturated_high_pixels",
        ):
            assert rows[index][key] == original_rows[selected_index][key]
        assert masks[index]["flags_png"] == original_masks[selected_index]["flags_png"]
    assert rows[0]["repeated"] is False
    if fps == 25 and window.frequency_hz == 30 and window.frame_count == 7:
        assert [row["missing"] for row in rows] == [False, True, False, False, False, False, False]
        assert [row["repeated"] for row in rows] == [False, False, False, True, True, False, False]
        assert rows[4]["time_threshold_exceeded"] is True
    report = json.loads((result.evidence_root / "report.json").read_bytes())
    assert report["source_profiles"] == original_report["source_profiles"]
    assert report["profile_sha256"] == original_report["profile_sha256"]
    assert report["window"]["operation"] == "lossless_code_selection"
    assert report["window"]["requantized"] is False
    assert report["window"]["source_video_sha256"] == media.sha256
    assert report["window"]["source_evidence"] == {
        entry.path: entry.sha256 for entry in media.evidence_files
    }
    assert report["video_encoding"]["lossless_codes"] is True
    with av.open(str(result.path)) as container:
        assert (
            report["video_encoding"]["codec_profile"]
            == container.streams.video[0].codec_context.profile
        )
    assert result.feature["info"]["video.fps"] == window.frequency_hz
    assert before == inventory(tmp_path / "input")


def test_explicit_fractional_origin_does_not_round_before_selecting_each_frame():
    # Old integer-origin windows retain their previous tie behavior. Arbitrary
    # source cuts apply nearest/ties-even once to the absolute rational position.
    original = DepthWindow(1, 5, 3, 30, START)
    offset = DepthWindow(1, 5, 3, 30, START, source_start_offset_ns=66_666_667)
    assert [original.source_frame(i, Fraction(15)) for i in range(3)] == [1, 1, 2]
    assert [offset.source_frame(i, Fraction(15)) for i in range(3)] == [1, 2, 2]


def test_time_grid_change_copies_full_video_without_encoder(tmp_path, monkeypatch):
    media = make_depth(tmp_path / "input", fps=30)
    forbid_quantization(monkeypatch)
    monkeypatch.setattr(holobrain_depth.DepthVideo, "__init__", forbid)
    # 30 Hz floor and nearest grids differ by 1 ns. Rebuild only evidence.
    result = prepare_depth_window(media, DepthWindow(0, 9, 9, 30, START), tmp_path / "output")
    assert result.path.read_bytes() == media.path.read_bytes()
    rows = list(parquet_rows(result.evidence_root / "frames.parquet"))
    assert rows[5]["target_timestamp_ns"] == START + 166_666_666
    assert rows[5]["source_timestamp_ns"] == START + 166_666_667
    assert rows[5]["time_error_ns"] == 1
    report = json.loads((result.evidence_root / "report.json").read_bytes())
    assert report["window"]["operation"] == "byte_copy"


@pytest.mark.parametrize(
    "tamper",
    [
        "video",
        "inventory",
        "mask",
        "source-time",
        "profile",
        "counts",
        "codec-profile",
        "encoder-config",
        "error",
        "totals",
    ],
)
def test_rejects_changed_bytes_or_inconsistent_evidence_before_writing(tmp_path, tamper):
    media = make_depth(tmp_path / "input")
    if tamper == "video":
        media.path.write_bytes(media.path.read_bytes() + b"changed")
    elif tamper == "inventory":
        media = replace(media, evidence_files=media.evidence_files[:-1])
    elif tamper == "mask":
        path = media.evidence_root / "validity/flags.parquet"
        path.write_bytes(path.read_bytes() + b"changed")
    elif tamper in {"source-time", "counts", "error"}:
        path = media.evidence_root / "frames.parquet"
        table = pq.read_table(path)
        rows = table.to_pylist()
        if tamper == "source-time":
            rows[0]["source_timestamp_ns"] = None
        elif tamper == "counts":
            rows[0]["invalid_pixels"] = 0
        else:
            rows[0]["unclipped_p95_error_m"] = -1
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
        # Matching object hashes cannot legitimize an internally false contract.
        media = replace(media, evidence_files=inventory(media.evidence_root))
    else:
        path = media.evidence_root / ("profile.json" if tamper == "profile" else "report.json")
        value = json.loads(path.read_bytes())
        if tamper == "profile":
            value["depth_max"] = 9
        elif tamper == "codec-profile":
            value["video_encoding"]["codec_profile"] = "Main 12"
        elif tamper == "encoder-config":
            value["video_encoding"]["encoder_options"]["x265-params"] += ":keyint=2"
        else:
            value["totals"]["missing_frames"] = 0
        path.write_text(json.dumps(value))
        media = replace(media, evidence_files=inventory(media.evidence_root))
    with pytest.raises(ValueError):
        prepare_depth_window(media, DepthWindow(0, 9, 9, 25, START), tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "window", [DepthWindow(0, 10, 9, 25, START), DepthWindow(0, 9, 10, 25, START)]
)
def test_selection_never_reads_past_frozen_window(tmp_path, window):
    media = make_depth(tmp_path / "input")
    with pytest.raises(ValueError, match="outside the frozen source window"):
        prepare_depth_window(media, window, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_selected_contract_is_rejected_against_a_different_episode_clock(tmp_path):
    media = make_depth(tmp_path / "input")
    result = prepare_depth_window(
        media, DepthWindow(3, 6, 3, 25, START + 120_000_000), tmp_path / "output"
    )
    with pytest.raises(ValueError, match="different Episode time grid"):
        validate_depth(
            result.evidence_root.parent,
            result.evidence_root.name,
            result.path,
            result.feature,
            3,
            25,
            target_rows=iter([{"hc.timestamp_ns": 0}]),
            feature_key="observation.images.head_depth",
        )


def test_row_group_boundary_and_a_window_of_a_window_keep_parent_hashes(tmp_path, monkeypatch):
    media = make_depth(tmp_path / "input", fps=30, count=140)
    forbid_quantization(monkeypatch)
    first = DepthWindow(1, 139, 130, 30, START + 33_333_333)
    output = prepare_depth_window(media, first, tmp_path / "first")
    assert (
        pq.ParquetFile(output.evidence_root / "validity/flags.parquet").metadata.num_row_groups == 2
    )
    selected = read_validity(output.evidence_root, 128)
    original = read_validity(media.evidence_root, 129)
    for key in selected:
        np.testing.assert_array_equal(selected[key], original[key])
    child_window = DepthWindow(3, 130, 127, 30, first.target_time(3))
    child = prepare_depth_window(output, child_window, tmp_path / "second")
    frame = next(parquet_rows(child.evidence_root / "frames.parquet"))
    assert frame["source_seq"] == 3 and frame["parent_frame_index"] == 3
    assert frame["repeated"] is False
    report = json.loads((child.evidence_root / "report.json").read_bytes())
    assert report["window"]["source_video_sha256"] == output.sha256
    assert report["window"]["source_evidence"]["report.json"] == file_hash(
        output.evidence_root / "report.json"
    )
    np.testing.assert_array_equal(decoded(child.path), decoded(output.path)[3:130])


def test_capture_depth_keeps_own_rgb_association_when_export_grid_moves(tmp_path):
    media = make_depth(tmp_path/'input', fps=30)
    # Camera RGB/depth is synchronized locally. A different primary camera's
    # export grid is 20 ms away; it must not invalidate all of this camera's depth.
    result = prepare_depth_window(media, DepthWindow(0, 3, 3, 30, START+20_000_000),
                                  tmp_path/'output', preserve_association_reference=True)
    rows = list(parquet_rows(result.evidence_root/'frames.parquet'))
    assert rows[0]['time_error_ns'] == -20_000_000
    assert rows[0]['association_target_timestamp_ns'] == START
    assert not rows[0]['time_threshold_exceeded']
    assert rows[2]['missing']

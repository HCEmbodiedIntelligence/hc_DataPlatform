import json

import av
import numpy as np
import pyarrow.parquet as pq
import pytest

from hc_data_platform.publishing.depth_evidence import DepthEvidenceVideo, read_validity
from hc_data_platform.publishing.holobrain_depth import (
    DEFAULT_PROFILE,
    DepthProfile,
    dequantize,
    quantize,
)


def test_invalid_saturation_and_explicit_source_scale_are_distinct():
    source = np.array([[0, -1, np.nan, np.inf, 65535, 5, 10, 1000, 10000, 12000]])
    original = source.copy()
    result = quantize(source, 0.001, invalid_values=(65535,))
    np.testing.assert_equal(source, original)
    assert result.valid.tolist() == [[False] * 5 + [True] * 5]
    assert result.saturated_low.tolist() == [[False] * 5 + [True] + [False] * 4]
    assert result.saturated_high.tolist() == [[False] * 9 + [True]]
    assert result.codes[0, 0] == result.codes[0, 6] == 0
    assert dequantize(result.codes)[0, 0] == pytest.approx(0.01)
    report = result.error_report()
    assert report["unclipped_pixels"] == 3
    assert report["clipped_max_error_m"] == pytest.approx(2)
    assert report["half_cell_bound_passed"]
    # A real sensor scale of 0.25 mm/count must not be treated as 1 mm/count.
    raw = np.full((16, 16), 4000, dtype=np.uint16)
    metres = np.ones((16, 16), dtype=np.float32)
    np.testing.assert_array_equal(quantize(raw, 0.00025).codes, quantize(metres, 1).codes)


@pytest.mark.parametrize("scale", [None, 0, -0.001, float("nan"), float("inf")])
def test_unknown_or_invalid_scale_rejected(scale):
    with pytest.raises(ValueError, match="depth_scale"):
        quantize(np.ones((16, 16)), scale)


@pytest.mark.parametrize(
    "profile", [DEFAULT_PROFILE, DepthProfile(depth_max=5), DepthProfile(use_log=False)]
)
def test_full_range_quantization_obeys_half_cell_bound(profile):
    values = np.linspace(profile.depth_min, profile.depth_max, 65536).reshape(256, 256)
    result = quantize(values, 1, profile)
    assert result.codes.min() == 0 and result.codes.max() == 4095
    assert result.error_report(profile)["half_cell_bound_passed"]
    m, mm = dequantize(result.codes, profile), dequantize(result.codes, profile, output_unit="mm")
    np.testing.assert_allclose(mm, np.rint(m * 1000), atol=0)
    assert DepthProfile(depth_max=5).sha256 != DEFAULT_PROFILE.sha256


def test_hevc_code_roundtrip_with_independent_masks_and_missing_frame(tmp_path):
    video = tmp_path / "depth.mp4"
    evidence = tmp_path / "hc/depth/head"
    writer = DepthEvidenceVideo(video, evidence, 30, (16, 16))
    writer.write(
        None,
        None,
        source_id="head",
        source_seq=None,
        source_timestamp_ns=None,
        target_timestamp_ns=0,
        missing=True,
    )
    values = np.linspace(0.01, 10, 256).reshape(16, 16)
    values[0, :4] = [0, np.nan, 0.001, 11]
    expected = []
    for index in (1, 2):
        expected.append(
            writer.write(
                values,
                1,
                source_id="head",
                source_seq=8,
                source_timestamp_ns=33_333_333,
                target_timestamp_ns=index * 33_333_333,
                max_time_error_ns=8_000_000,
            )
        )
    writer.close()
    writer.close()
    with av.open(str(video)) as container:
        decoded = [frame.to_ndarray(format="gray12le") for frame in container.decode(video=0)]
    assert len(decoded) == 3
    assert not decoded[0].any()
    np.testing.assert_array_equal(decoded[1:], expected)
    assert not read_validity(evidence, 0)["valid"].any()
    mask = read_validity(evidence, 1)
    assert not mask["valid"][0, 0] and not mask["valid"][0, 1]
    assert mask["saturated_low"][0, 2] and mask["saturated_high"][0, 3]
    physical = dequantize(decoded[1])
    physical[~mask["valid"]] = np.nan
    assert np.isnan(physical[0, 0])
    rows = pq.read_table(evidence / "frames.parquet").to_pylist()
    assert rows[0]["missing"] and rows[0]["source_seq"] is None
    assert rows[0]["depth_scale_m_per_unit"] is None
    assert rows[2]["repeated"] and rows[2]["time_threshold_exceeded"]
    assert rows[1]["source_seq"] == 8 and rows[1]["source_dtype"] == "<f8"
    report = json.loads((evidence / "report.json").read_text())
    assert report["video_encoding"]["lossless_codes"]
    assert report["totals"]["missing_frames"] == 1
    assert report["totals"]["repeated_frames"] == 1
    assert not report["validity"]["official_reader_applies_mask"]



def test_depth_time_evidence_keeps_missing_reference_unknown(tmp_path):
    writer = DepthEvidenceVideo(tmp_path / "depth.mp4", tmp_path / "evidence", 30, (16, 16))
    for index, target in enumerate((1_000_000_000, 999_999_999, None)):
        writer.write(np.ones((16, 16), dtype=np.uint16), .001,
                     source_id="wrist", source_seq=index, source_timestamp_ns=1_002_000_000,
                     target_timestamp_ns=target, max_time_error_ns=2_000_000)
    writer.close()
    rows = pq.read_table(tmp_path / "evidence/frames.parquet").to_pylist()
    assert rows[0]["time_error_ns"] == 2_000_000 and not rows[0]["time_threshold_exceeded"]
    assert rows[1]["time_error_ns"] == 2_000_001 and rows[1]["time_threshold_exceeded"]
    assert rows[2]["target_timestamp_ns"] is None and rows[2]["time_error_ns"] is None

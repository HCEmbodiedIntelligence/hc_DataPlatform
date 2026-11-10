import numpy as np
import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.data_formats.rgb_video import RgbVideoWriter
from hc_data_platform.data_formats.video import (
    RGB_STATISTICS_HISTOGRAM,
    RgbHistogram,
    inspect_video,
)


@pytest.mark.parametrize("constant", [False, True])
def test_rgb_histogram_counts_every_pixel_and_matches_direct_statistics(constant):
    frames = np.random.default_rng(43).integers(0, 256, (7, 48, 64, 3), dtype=np.uint8)
    if constant:
        frames[:] = [0, 127, 255]
    histogram = RgbHistogram()
    for frame in frames:
        histogram.add(frame.tobytes())
    pixels = frames.reshape(-1, 3).astype(np.float64) / 255
    expected = {
        "min": pixels.min(axis=0),
        "max": pixels.max(axis=0),
        "mean": pixels.mean(axis=0),
        "std": pixels.std(axis=0),
    }
    assert histogram.counts.sum(axis=1).tolist() == [7 * 48 * 64] * 3
    for key, actual in histogram.statistics().items():
        np.testing.assert_allclose(np.array(actual).reshape(3), expected[key], atol=1e-12)
    if constant:
        np.testing.assert_allclose(histogram.statistics()["std"], 0, atol=1e-15)


def test_fast_video_statistics_match_legacy_for_actual_mp4_and_keep_frame_checks(tmp_path):
    path = tmp_path / "rgb.mp4"
    writer = RgbVideoWriter(path, 29, (48, 64, 3), lossless_rgb=True)
    for frame in np.random.default_rng(5).integers(0, 256, (12, 48, 64, 3), dtype=np.uint8):
        writer.write(frame)
    writer.close()
    old_feature, old_stats = inspect_video(path, frame_count=12, fps=29)
    new_feature, new_stats = inspect_video(
        path, frame_count=12, fps=29, statistics_method=RGB_STATISTICS_HISTOGRAM
    )
    assert new_feature == old_feature
    for key in old_stats:
        np.testing.assert_allclose(new_stats[key], old_stats[key], rtol=1e-10, atol=1e-12)
    for method in ("legacy", RGB_STATISTICS_HISTOGRAM):
        with pytest.raises(ProblemException, match="EXPORT_VIDEO_INVALID"):
            inspect_video(path, frame_count=11, fps=29, statistics_method=method)


def test_empty_statistics_and_unknown_algorithm_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="nonempty"):
        RgbHistogram().statistics()
    with pytest.raises(ValueError, match="unsupported"):
        inspect_video(tmp_path / "missing.mp4", frame_count=1, fps=30, statistics_method="bad")

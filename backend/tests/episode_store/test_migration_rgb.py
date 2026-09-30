"""Real encoders/decoders: selected RGB pixels stay exact, including repetitions."""

from dataclasses import replace

import pytest

from hc_data_platform.episode_store.migration_rgb import prepare_rgb_selection
from hc_data_platform.episode_store.postgres import PackageConflict
from hc_data_platform.episode_store.prepared_assets import PreparedVideo
from tests.episode_store.test_writer import build_rgbd_package


@pytest.fixture
def media(tmp_path):
    _, _, _, assets = build_rgbd_package(tmp_path)
    media = assets.videos["observation.images.head_rgb"]
    return replace(media, feature={**media.feature, "info": {"video.fps": 30}})


def test_whole_h265_rgb_is_reused_byte_for_byte(media, tmp_path):
    prepared, report = prepare_rgb_selection(media, (0, 1, 2, 3), 30, tmp_path / "unused.mp4")
    assert prepared.path == media.path and prepared.sha256 == media.sha256
    assert not report["transcoded"] and report["decoded_pixels_exact"]
    assert report["output_rgb_sha256"] == report["selected_source_rgb_sha256"]
    assert not (tmp_path / "unused.mp4").exists()


def test_selected_h265_rgb_pixels_are_lossless_in_independent_mp4(media, tmp_path):
    prepared, report = prepare_rgb_selection(media, (1, 1, 3), 30, tmp_path / "window.mp4")
    assert prepared.path == tmp_path / "window.mp4" and prepared.sha256 != media.sha256
    assert report["transcoded"] and report["decoded_pixels_exact"]
    assert not report["requantized"] and report["frame_count"] == 3
    assert report["operation"] == "lossless_rgb_window"
    assert report["output_rgb_sha256"] == report["selected_source_rgb_sha256"]
    assert report["output_pix_fmt"] == "gbrp"
    assert prepared.feature["info"]["video.codec"] == "h264"


@pytest.mark.parametrize("indexes", [(), (-1,), (True,), (2, 1), (4,), (0, 9)])
def test_invalid_selection_fails_without_a_success_receipt(media, tmp_path, indexes):
    with pytest.raises(PackageConflict):
        prepare_rgb_selection(media, indexes, 30, tmp_path / "bad.mp4")


@pytest.mark.parametrize("change", ["hash", "size", "fps", "shape"])
def test_changed_media_contract_rejected(media, tmp_path, change):
    if change == "hash":
        media = replace(media, sha256="f" * 64)
    elif change == "size":
        media = replace(media, size=media.size + 1)
    else:
        feature = {**media.feature, "info": dict(media.feature.get("info", {}))}
        if change == "fps":
            feature["info"]["video.fps"] = 29
        else:
            feature["shape"] = [16, 32, 3]
        media = replace(media, feature=feature)
    with pytest.raises(PackageConflict):
        prepare_rgb_selection(media, (1, 2), 30, tmp_path / "bad.mp4")


def test_depth_stream_never_enters_rgb_window_encoder(tmp_path):
    _, _, _, assets = build_rgbd_package(tmp_path)
    media: PreparedVideo = assets.videos["observation.images.head_depth"]
    with pytest.raises(PackageConflict):
        prepare_rgb_selection(media, (1, 2), 30, tmp_path / "bad.mp4")


@pytest.mark.parametrize("change", [None, "hash", "pixel_hash", "references", "indexes", "clock"])
def test_frozen_rgb_preparation_is_reverified(media, tmp_path, change):
    from hc_data_platform.aligned_media.models import AlignedMediaFrameReferenceV1
    from hc_data_platform.episode_store.migration_rgb import verify_rgb_preparation
    from hc_data_platform.episode_store.models import digest

    indexes = (1, 1, 3)
    prepared, report = prepare_rgb_selection(media, indexes, 30, tmp_path / "frozen.mp4")
    refs = [
        AlignedMediaFrameReferenceV1(
            camera_id="head",
            artifact_id="frozen",
            object_key="raw/frozen.mp4",
            alignment_version="legacy/1",
            frame_index=index,
            pts=index,
            pts_time_base_denominator=30,
            timestamp_ns=i * 1000000000 // 30,
            valid=True,
            repeated=i == 1,
            placeholder=False,
            dropped=False,
        )
        for i, index in enumerate(indexes)
    ]
    report["source_references_sha256"] = digest([ref.model_dump(mode="json") for ref in refs])
    if change == "hash":
        report["output_sha256"] = "a" * 64
    elif change == "pixel_hash":
        report["selected_source_rgb_sha256"] = report["output_rgb_sha256"] = "a" * 64
    elif change == "references":
        refs[1] = refs[1].model_copy(update={"timestamp_ns": refs[1].timestamp_ns + 1})
    elif change == "indexes":
        report["frame_indexes_sha256"] = "a" * 64
    elif change == "clock":
        report["target_fps"] = 29
    if change is None:
        verify_rgb_preparation(prepared.path, report, refs, frame_count=3, fps=30)
    else:
        with pytest.raises(PackageConflict):
            verify_rgb_preparation(prepared.path, report, refs, frame_count=3, fps=30)

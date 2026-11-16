import hashlib
from types import SimpleNamespace

import pytest

from hc_data_platform.continuous_recordings.models import RecordingScope
from hc_data_platform.continuous_recordings.previews import _download, preview_key, read_preview
from hc_data_platform.ingest.ports import InMemoryObjectStorage


def fixture():
    upload = SimpleNamespace(
        upload_id="u",
        manifest_sha256="a" * 64,
        scope=RecordingScope(organization_id="org", project_id="p", region_code="r"),
    )
    asset = SimpleNamespace(
        asset_id="asset",
        object_key="source",
        manifest=SimpleNamespace(
            path="videos/depth.mp4", sha256=hashlib.sha256(b"source").hexdigest(), size=6
        ),
    )
    return upload, asset


def test_selected_preview_has_dense_frames_and_keeps_raw_slice_clock(tmp_path):
    import av
    import numpy as np

    from hc_data_platform.continuous_recordings.previews import select_preview
    from tests.episode_store.test_writer import build_rgbd_package

    root, _, _, _ = build_rgbd_package(tmp_path)
    source = next((root / "videos/observation.images.head_rgb").rglob("*.mp4"))
    frames = [
        {"source_frame": i, "offset_ns": t, "valid": i in (1, 3)}
        for i, t in enumerate((0, 33_122_000, 67_979_000, 100_081_000))
    ]
    output = tmp_path / "selected.mp4"
    assert select_preview(source, output, frames, 30) == 2
    with av.open(str(output)) as video:
        decoded = list(video.decode(video=0))
        assert [round(f.pts * f.time_base * 1e9) for f in decoded] == [33_122_000, 100_081_000]
        assert [float(np.mean(f.to_ndarray(format="rgb24"))) for f in decoded] == pytest.approx(
            [50, 150], abs=4
        )


def test_preview_identity_is_scoped_and_bound_to_all_inputs():
    upload, asset = fixture()
    key = preview_key(upload, asset, (asset,))
    upload.scope = upload.scope.model_copy(update={"organization_id": "another"})
    assert preview_key(upload, asset, (asset,)) != key
    upload, asset = fixture()
    evidence = SimpleNamespace(manifest=SimpleNamespace(path="mask.parquet", sha256="b" * 64))
    assert preview_key(upload, asset, (asset, evidence)) != key


def test_download_rejects_source_corruption_and_cached_preview_must_match_source(tmp_path):
    upload, asset = fixture()
    storage = InMemoryObjectStorage()
    assert read_preview(storage, upload, asset, (asset,)) is None

    class BrokenStorage:
        def read_chunks(self, key):
            yield b"broken"

    with pytest.raises(ValueError, match="checksum"):
        _download(BrokenStorage(), asset, tmp_path / "source", lambda: None)


def test_complete_h264_preview_remuxes_packets_with_source_timestamps(tmp_path):
    import av
    import numpy as np
    from hc_data_platform.continuous_recordings.previews import select_preview
    from hc_data_platform.data_formats.rgb_video import RgbVideoWriter

    src = tmp_path / "input.mp4"
    writer = RgbVideoWriter(src, 30, (32, 32, 3))
    stamps = (0, 33122000, 67979000, 100081000)
    for i in range(4):
        writer.write(np.full((32, 32, 3), i * 50, dtype=np.uint8))
    writer.close()
    dest = tmp_path / "retimed.mp4"
    frames = [dict(source_frame=i, offset_ns=t, valid=True) for i, t in enumerate(stamps)]
    assert select_preview(src, dest, frames, 30) == 4
    with av.open(str(src)) as incoming, av.open(str(dest)) as out:
        assert [bytes(p) for p in incoming.demux(video=0) if p.size] == [
            bytes(p) for p in out.demux(video=0) if p.size
        ]
    with av.open(str(dest)) as out:
        assert [round(f.pts * f.time_base * 10**9) for f in out.decode(video=0)] == list(stamps)


def test_depth_preview_memory_budget_and_verified_pixels(tmp_path):
    import av
    import numpy as np
    from hc_data_platform.data_formats.depth_evidence import DepthEvidenceVideo, VerifiedDepthFrames

    shape = (32, 32)
    memory = VerifiedDepthFrames(max_bytes=2 * 32 * 32 * 2)
    path = tmp_path / "depth.mp4"
    writer = DepthEvidenceVideo(path, tmp_path / "evidence", 30, shape, preview_frames=memory)
    for i in range(2):
        writer.write(np.full(shape, 100 + i, np.uint16), 0.001, source_id="test", source_seq=i,
                     source_timestamp_ns=i, target_timestamp_ns=i)
    assert path not in memory.frames
    writer.close()
    with av.open(str(path)) as video:
        expected = np.stack([frame.to_ndarray(format="gray12le") for frame in video.decode(video=0)])
    np.testing.assert_array_equal(memory.frames[path], expected)
    assert not memory.frames[path].flags.writeable
    assert memory.allocate(1, shape) is None


def test_depth_preview_colorizes_only_valid_rows_on_source_clock(tmp_path):
    import json

    import av
    import numpy as np
    import pyarrow.parquet as pq
    from hc_data_platform.data_formats.depth_evidence import DepthEvidenceVideo
    from hc_data_platform.data_formats.holobrain_depth import DepthProfile
    from hc_data_platform.episode_store.previews import EpisodePreviews
    from hc_data_platform.episode_store.validation import parquet_rows

    shape = (32, 32)
    path, evidence = tmp_path / "depth.mp4", tmp_path / "evidence"
    writer = DepthEvidenceVideo(path, evidence, 30, shape)
    for i in range(4):
        writer.write(np.full(shape, 500 + 100 * i, np.uint16), 0.001, source_id="test",
                     source_seq=i, source_timestamp_ns=i, target_timestamp_ns=i)
    writer.close()
    declaration = json.loads((evidence / "profile.json").read_text())
    profile = DepthProfile(**{k: declaration[k] for k in ("depth_min", "depth_max", "shift", "use_log", "version")})
    flags = evidence / "validity/flags.parquet"
    offsets = [None, 33_122_000, None, 100_081_000]
    output = tmp_path / "preview.mp4"
    with av.open(str(path)) as source:
        EpisodePreviews._encode(source, source.streams.video[0], output, pq.read_metadata(flags).num_rows,
                                30, 32, 32, masks=iter(parquet_rows(flags)), profile=profile, offsets=offsets)
    with av.open(str(output)) as video:
        assert [round(f.pts * f.time_base * 1e9) for f in video.decode(video=0)] == [33_122_000, 100_081_000]

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

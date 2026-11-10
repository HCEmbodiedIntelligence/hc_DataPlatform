"""Data registration must respect the actual sampling clock for native MCAP slices."""

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hc_data_platform.alignment.models import ModalityKind
from hc_data_platform.dataset_registry.projection_models import DatasetIngestProjectionConflict
from hc_data_platform.dataset_registry.projection_writer import DatasetProjectionWriter
from hc_data_platform.episode_store.models import DataVersionRef, EpisodeIdentity


def project(tmp_path, *, native=True, sparse=False, stamps=None, archive_first=None, first=None):
    start, end = 10_520_832_000, 10_620_832_000
    source = (
        stamps
        if stamps is not None
        else [start + 4_042_000, start + 36_000_000, start + 69_333_333]
    )
    first = first if first is not None else source[int(sparse)]
    trace = tmp_path / "trace.jsonl"
    trace.write_text("".join(json.dumps({"row": {"target_time_ns": x}}) + "\n" for x in source))
    archive = tmp_path / "archive.parquet"
    times = [archive_first if archive_first is not None else source[0], *source[1:]]
    pq.write_table(pa.table({"hc.timestamp_ns": times}), archive)
    info = {
        "fps": 30,
        "features": {"state": {"dtype": "float64"}},
        "hc.feature_mapping": {"/state": "state"},
    }
    if sparse:
        info.update(
            {
                "hc.training_policy": "drop_invalid_rows_preserve_source_time/v1",
                "hc.time_origin_ns": first,
                "hc.media_frame_count": 2,
                "hc.source_frame_count": 3,
            }
        )
    docs = {
        "meta/info.json": info,
        "hc/source.json": {"mcap": {"format": "native-mcap-recording/4"}} if native else {},
    }
    package = SimpleNamespace(
        document=docs.__getitem__,
        file=lambda name: nullcontext(
            {
                "hc/recording/source_frames.jsonl": trace,
                "hc/recording/aligned_rows.parquet": archive,
            }[name]
        ),
    )
    step = SimpleNamespace(timestamp_ns=first, modalities={"/state": [0.5]})
    writer = DatasetProjectionWriter(
        files=SimpleNamespace(open=lambda *a: package),
        steps=SimpleNamespace(read_steps=lambda *a: SimpleNamespace(steps=[step])),
    )
    return writer._package_streams(
        alignment=SimpleNamespace(
            start_ns=start,
            end_ns=end,
            streams={"/state": SimpleNamespace(kind=ModalityKind.CONTINUOUS)},
        ),
        camera_topics=frozenset(),
        ready=SimpleNamespace(
            step_count=2 if sparse else 3,
            rollout_id="rollout",
            data=DataVersionRef(
                identity=EpisodeIdentity(
                    organization_id="org",
                    project_id="project",
                    region_code="region",
                    dataset_id="dataset_test",
                    episode_id="episode_0001",
                    rollout_id="rollout",
                ),
                data_version_id="dv_" + "a" * 64,
                version=1,
                content_hash="a" * 64,
                schema_snapshot_id="schema",
                package_prefix="episode/data",
                frame_count=2 if sparse else 3,
            ),
        ),
        frequency_hz=30,
        media_artifacts=(),
    )


@pytest.mark.parametrize("sparse", [True, False])
def test_cut_between_samples_uses_first_acquisition_and_keeps_slice_bounds(tmp_path, sparse):
    (stream,) = project(tmp_path, sparse=sparse)
    assert (stream.t_start_ns, stream.t_end_ns) == ("10520832000", "10620832000")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"native": False},
        {"sparse": True, "native": False},
        {"sparse": True, "archive_first": 10_520_832_000},
        {"first": 10_525_000_000},
        {"stamps": [10_520_000_000, 10_540_000_000, 10_570_000_000]},
        {"stamps": [10_525_000_000, 10_560_000_000, 10_620_832_000]},
    ],
)
def test_rejects_wrong_first_row_outside_window_or_shifted_fixed_grid(tmp_path, kwargs):
    with pytest.raises(DatasetIngestProjectionConflict):
        project(tmp_path, **kwargs)


def test_adjacent_slices_include_each_boundary_frame_once(tmp_path):
    from hc_data_platform.continuous_recordings.package import preserved_frames

    trace = tmp_path / "frames.jsonl"
    trace.write_text(
        "".join(
            json.dumps({"source_frame": i, "offset_ns": t}) + "\n"
            for i, t in enumerate((100, 130, 160, 190))
        )
    )

    def window(lo, hi):
        req = SimpleNamespace(
            start_offset_ns=lo,
            end_offset_ns=hi,
            recording_config=SimpleNamespace(recorder_version="native-mcap-recording/4"),
        )
        return preserved_frames(req, {"source/frames.jsonl": trace})

    assert window(100, 145) == [(0, 100), (1, 130)]
    assert window(145, 190) == [(2, 160)]
    assert window(190, 210) == [(3, 190)]

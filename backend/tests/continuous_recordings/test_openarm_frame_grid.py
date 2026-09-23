import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pyarrow.ipc as ipc
import pytest
from mcap.writer import Writer

from hc_data_platform.alignment.arrow_writer import ArrowFragmentWriter
from hc_data_platform.alignment.engine import AlignmentEngine
from hc_data_platform.alignment.models import AlignmentProfileV1, AlignmentStrategy, ModalityKind
from hc_data_platform.continuous_recordings.asset_models import SensorTimestampMode
from hc_data_platform.continuous_recordings.openarm_grid import frame_time, slice_frame_time
from hc_data_platform.continuous_recordings.processing import (
    ContinuousEpisodeProcessingError,
    ContinuousEpisodeProcessingService,
    _CompleteFrameWriter,
)
from hc_data_platform.verification.ports import RegisteredDecoderProbe


@pytest.mark.parametrize("first", [0, 1, 2, 9])
@pytest.mark.parametrize("round_timestamps", [False, True])
def test_native_mcap_slices_preserve_every_action(tmp_path, first, round_timestamps):
    _align(tmp_path, first, round_timestamps)


def test_missing_action_aborts_before_fragment_commit(tmp_path):
    with pytest.raises(ContinuousEpisodeProcessingError, match="missing required samples"):
        _align(tmp_path, 1, True, missing_frame=3)
    assert not list(tmp_path.rglob("*.arrow"))


def test_wrong_grid_is_not_silently_retimed():
    with pytest.raises(ValueError, match="outside its declared frame grid"):
        slice_frame_time(60_000_000, 0, 200_000_000)


def _align(tmp_path, first, rounded, missing_frame=None):
    raw = tmp_path / "sensor.mcap"
    topics = {"/action": ModalityKind.ACTION, "/observation/state": ModalityKind.CONTINUOUS}
    with raw.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        schema = writer.register_schema("vector", "jsonschema", b'{"type":"object"}')
        channels = {t: writer.register_channel(t, "json", schema) for t in topics}
        for k in range(30):
            stamp = round(k * 1e9 / 30) if rounded else frame_time(k)
            for topic, channel in channels.items():
                if topic == "/action" and k == missing_frame:
                    continue
                writer.add_message(
                    channel,
                    log_time=stamp,
                    publish_time=stamp,
                    sequence=k,
                    data=json.dumps(
                        {
                            "values": [k, k + 0.5],
                            "names": ["joint", "gripper"],
                            "units": ["rad", "m"],
                        }
                    ).encode(),
                )
        writer.finish()
    start = datetime(2026, 9, 23, tzinfo=timezone.utc) + timedelta(
        microseconds=frame_time(first) // 1000
    )
    request = SimpleNamespace(
        recording_config=SimpleNamespace(
            recorder_version="openarm-session/v2",
            sensors=tuple(
                SimpleNamespace(topic=t, timestamp_mode=SensorTimestampMode.RECORDING_OFFSET_NS)
                for t in topics
            ),
        ),
        projection=SimpleNamespace(started_at=start, ended_at=start + timedelta(milliseconds=200)),
        start_offset_ns=frame_time(first),
        end_offset_ns=frame_time(first + 6),
    )
    processor = object.__new__(ContinuousEpisodeProcessingService)
    processor._decoder = RegisteredDecoderProbe(
        {("json", "jsonschema"): lambda _, body: json.loads(body)}
    )
    samples = processor._sensor_alignment_samples(raw, request)
    start_ns = int(start.timestamp()) * 1_000_000_000 + start.microsecond * 1000
    manifest = AlignmentEngine().align_stream_to_writer(
        rollout_id="native-grid",
        source_sha256="a" * 64,
        attempt_id="attempt",
        start_ns=start_ns,
        end_ns=start_ns + 200_000_000,
        stream_kinds=topics,
        samples=((topic, sample) for _, topic, sample in samples),
        profile=AlignmentProfileV1(
            profile_id="native",
            converter_version="test",
            frequency_hz=30,
            required_modalities=frozenset(topics),
            default_tolerance_ns=20_000_000,
            stream_strategies={
                "/action": AlignmentStrategy.CAUSAL,
                "/observation/state": AlignmentStrategy.LINEAR,
            },
        ),
        writer=_CompleteFrameWriter(ArrowFragmentWriter(tmp_path / "arrow")),
    )
    table = ipc.open_file(Path(manifest.staging_uri.removeprefix("file://"))).read_all()
    rows = table.to_pylist()
    assert len(rows) == 6 and all(row["sample_valid"] for row in rows)
    for k, row in enumerate(rows, first):
        modalities = json.loads(row["modalities_json"])
        for topic in topics:
            assert modalities[topic]["value"]["values"] == [k, k + 0.5]
            assert modalities[topic]["value"]["units"] == ["rad", "m"]

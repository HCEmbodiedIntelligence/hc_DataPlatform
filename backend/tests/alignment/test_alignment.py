from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hc_data_platform.alignment import (
    AlignmentEngine,
    AlignmentInputV1,
    AlignmentProfileV1,
    AlignmentStrategy,
    ArrowFragmentWriter,
    FakeFragmentWriter,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from hc_data_platform.alignment.models import AlignedRowV1

SHA = "c" * 64


def _stream(kind: ModalityKind, samples: list[tuple[int, object]]) -> ModalityStreamV1:
    return ModalityStreamV1(
        kind=kind,
        samples=tuple(
            TimedSampleV1(timestamp_ns=timestamp_ns, value=value) for timestamp_ns, value in samples
        ),
    )


def _input(
    *,
    end_ns: int,
    streams: dict[str, ModalityStreamV1],
    attempt: str = "attempt-1",
    start_ns: int = 0,
) -> AlignmentInputV1:
    return AlignmentInputV1(
        rollout_id="r1",
        source_sha256=SHA,
        attempt_id=attempt,
        start_ns=start_ns,
        end_ns=end_ns,
        streams=streams,
    )


def _profile(required: set[str], **updates: object) -> AlignmentProfileV1:
    values: dict[str, object] = {
        "profile_id": "align-v1",
        "required_modalities": required,
    }
    values.update(updates)
    return AlignmentProfileV1(**values)


def test_sixty_seconds_at_30hz_has_exactly_1800_contiguous_steps() -> None:
    data = _input(
        end_ns=60_000_000_000,
        streams={"camera": _stream(ModalityKind.IMAGE, [(0, b"frame")])},
    )

    manifest, rows = AlignmentEngine().align_in_memory(
        data, _profile(set(), default_tolerance_ns=0)
    )

    assert manifest.row_count == 1800
    assert [row.step_index for row in rows] == list(range(1800))
    assert [row.timestamp_ns for row in rows] == [
        index * 1_000_000_000 // 30 for index in range(1800)
    ]
    assert {
        right.timestamp_ns - left.timestamp_ns for left, right in zip(rows, rows[1:], strict=False)
    } == {
        33_333_333,
        33_333_334,
    }
    assert rows[-1].timestamp_ns < data.end_ns


def test_nonzero_start_uses_a_half_open_integer_timeline() -> None:
    data = _input(
        start_ns=1_000_000_007,
        end_ns=1_100_000_007,
        streams={"camera": _stream(ModalityKind.IMAGE, [])},
    )

    _, rows = AlignmentEngine().align_in_memory(data, _profile(set()))

    assert [row.timestamp_ns for row in rows] == [
        1_000_000_007,
        1_033_333_340,
        1_066_666_673,
    ]


def test_nearest_modalities_and_causal_modalities_use_prescribed_defaults() -> None:
    streams = {
        "camera": _stream(ModalityKind.IMAGE, [(0, "f0"), (60_000_000, "f1")]),
        "cloud": _stream(ModalityKind.POINT_CLOUD, [(0, "p0"), (50_000_000, "p1")]),
        "action": _stream(ModalityKind.ACTION, [(0, "a0"), (70_000_000, "future")]),
        "state": _stream(ModalityKind.DISCRETE, [(0, "open"), (60_000_000, "future")]),
    }
    profile = _profile(set(streams), default_tolerance_ns=100_000_000)

    _, rows = AlignmentEngine().align_in_memory(_input(end_ns=70_000_000, streams=streams), profile)
    step = rows[1]

    assert step.modalities["camera"].value == "f1"
    assert step.modalities["camera"].strategy is AlignmentStrategy.NEAREST
    assert step.modalities["cloud"].value == "p1"
    assert step.modalities["cloud"].strategy is AlignmentStrategy.NEAREST
    assert step.modalities["action"].value == "a0"
    assert step.modalities["action"].strategy is AlignmentStrategy.CAUSAL
    assert step.modalities["state"].value == "open"
    assert step.modalities["state"].strategy is AlignmentStrategy.RECENT
    assert step.sample_valid is True


@pytest.mark.parametrize("kind", [ModalityKind.ACTION, ModalityKind.DISCRETE])
def test_causal_selection_never_reads_a_future_value(kind: ModalityKind) -> None:
    data = _input(
        end_ns=1,
        streams={"causal": _stream(kind, [(10, "future")])},
    )

    _, rows = AlignmentEngine().align_in_memory(
        data, _profile({"causal"}, default_tolerance_ns=1_000)
    )
    value = rows[0].modalities["causal"]

    assert value.value is None
    assert value.valid is False
    assert value.source_timestamps_ns == (10,)
    assert value.time_error_ns == 10
    assert rows[0].sample_valid is False


def test_nested_continuous_values_are_interpolated_linearly() -> None:
    joint = _stream(
        ModalityKind.CONTINUOUS,
        [
            (0, {"arm": {"position": [0.0, 2.0]}}),
            (100_000_000, {"arm": {"position": [10.0, 6.0]}}),
        ],
    )
    profile = _profile({"joint"}, frequency_hz=20, default_tolerance_ns=100_000_000)

    _, rows = AlignmentEngine().align_in_memory(
        _input(end_ns=60_000_000, streams={"joint": joint}), profile
    )
    value = rows[1].modalities["joint"]

    assert value.value["arm"]["position"] == pytest.approx([5.0, 4.0])
    assert value.source_timestamps_ns == (0, 100_000_000)
    assert value.time_error_ns == 50_000_000
    assert value.strategy is AlignmentStrategy.LINEAR


def test_imu_and_force_use_nested_window_means_by_default() -> None:
    streams = {
        "imu": _stream(
            ModalityKind.IMU,
            [(0, [0.0, 2.0]), (10_000_000, [2.0, 4.0])],
        ),
        "force": _stream(
            ModalityKind.FORCE,
            [(0, {"x": 2.0}), (10_000_000, {"x": 6.0})],
        ),
    }

    _, rows = AlignmentEngine().align_in_memory(
        _input(end_ns=1, streams=streams),
        _profile(set(streams), default_tolerance_ns=10_000_000),
    )

    assert rows[0].modalities["imu"].value == [1.0, 3.0]
    assert rows[0].modalities["force"].value == {"x": 4.0}
    assert rows[0].modalities["imu"].source_timestamps_ns == (0, 10_000_000)
    assert rows[0].modalities["force"].strategy is AlignmentStrategy.WINDOW_MEAN


def test_profile_tolerance_invalidates_without_losing_candidate_provenance() -> None:
    data = _input(
        end_ns=70_000_000,
        streams={"camera": _stream(ModalityKind.IMAGE, [(0, "frame")])},
    )
    profile = _profile(
        {"camera"},
        default_tolerance_ns=100_000_000,
        stream_tolerance_ns={"camera": 10_000_000},
    )

    _, rows = AlignmentEngine().align_in_memory(data, profile)
    invalid = rows[1].modalities["camera"]

    assert rows[0].modalities["camera"].valid is True
    assert invalid.value is None
    assert invalid.valid is False
    assert invalid.source_timestamps_ns == (0,)
    assert invalid.time_error_ns == 33_333_333
    assert invalid.repeated is False
    assert rows[1].sample_valid is False


def test_reused_image_retains_real_source_and_sets_repeated() -> None:
    data = _input(
        end_ns=100_000_000,
        streams={"camera": _stream(ModalityKind.IMAGE, [(0, "f0"), (50_000_000, "f1")])},
    )

    _, rows = AlignmentEngine().align_in_memory(
        data, _profile({"camera"}, default_tolerance_ns=100_000_000)
    )

    assert rows[1].modalities["camera"].source_timestamps_ns == (50_000_000,)
    assert rows[1].modalities["camera"].repeated is False
    assert rows[2].modalities["camera"].source_timestamps_ns == (50_000_000,)
    assert rows[2].modalities["camera"].time_error_ns == 16_666_666
    assert rows[2].modalities["camera"].repeated is True


def test_same_content_has_same_hash_but_isolated_uri_across_attempts() -> None:
    streams = {"camera": _stream(ModalityKind.IMAGE, [(0, b"binary")])}
    profile = _profile(set(), default_tolerance_ns=0)

    first, first_rows = AlignmentEngine().align_in_memory(
        _input(end_ns=1, streams=streams, attempt="a"), profile
    )
    second, second_rows = AlignmentEngine().align_in_memory(
        _input(end_ns=1, streams=streams, attempt="b"), profile
    )

    assert first.content_sha256 == second.content_sha256
    assert first.schema_sha256 == second.schema_sha256
    assert first_rows == second_rows
    assert first.staging_uri != second.staging_uri
    assert "/a/" in first.staging_uri
    assert "/b/" in second.staging_uri


def test_staging_failure_aborts_only_current_attempt() -> None:
    data = _input(
        end_ns=100_000_000,
        streams={"camera": _stream(ModalityKind.IMAGE, [(0, "f")])},
        attempt="successful-attempt",
    )
    writer = FakeFragmentWriter()
    first = AlignmentEngine().align_to_writer(data, _profile(set(), default_tolerance_ns=0), writer)
    committed_rows = writer.rows_for_attempt("successful-attempt")
    writer.fail_at_row = 1

    with pytest.raises(OSError, match="injected staging failure"):
        AlignmentEngine().align_to_writer(
            data.model_copy(update={"attempt_id": "failed-attempt"}),
            _profile(set(), default_tolerance_ns=0),
            writer,
        )

    assert first.attempt_id == "successful-attempt"
    assert writer.committed_attempt_ids == ("successful-attempt",)
    assert writer.rows_for_attempt("successful-attempt") == committed_rows
    assert writer.attempt_status("failed-attempt") == "ABORTED"
    assert writer.rows_for_attempt("failed-attempt") == ()


def test_arrow_writer_streams_an_attempt_isolated_deterministic_fragment(
    tmp_path: Path,
) -> None:
    pyarrow = pytest.importorskip("pyarrow")
    ipc = pytest.importorskip("pyarrow.ipc")
    streams = {"camera": _stream(ModalityKind.IMAGE, [(0, b"binary")])}
    profile = _profile(set(), default_tolerance_ns=100_000_000)
    engine = AlignmentEngine()

    first = engine.align_to_writer(
        _input(end_ns=70_000_000, streams=streams, attempt="arrow-a"),
        profile,
        ArrowFragmentWriter(tmp_path),
    )
    second = engine.align_to_writer(
        _input(end_ns=70_000_000, streams=streams, attempt="arrow-b"),
        profile,
        ArrowFragmentWriter(tmp_path),
    )
    first_path = Path(first.staging_uri.removeprefix("file://"))
    second_path = Path(second.staging_uri.removeprefix("file://"))

    assert first_path != second_path
    assert first.content_sha256 == second.content_sha256
    assert (
        hashlib.sha256(first_path.read_bytes()).digest()
        == hashlib.sha256(second_path.read_bytes()).digest()
    )
    with pyarrow.memory_map(str(first_path), "r") as source:
        reader = ipc.open_file(source)
        table = reader.read_all()
    assert table.num_rows == 3
    assert table.column("step_index").to_pylist() == [0, 1, 2]
    assert table.column("sample_valid").to_pylist() == [True, True, True]
    second_modalities = json.loads(table.column("modalities_json")[1].as_py())
    assert second_modalities["camera"] == {
        "repeated": True,
        "source_timestamps_ns": [0],
        "strategy": "nearest",
        "time_error_ns": 33_333_333,
        "valid": True,
        "value": {"$bytes_base64": "YmluYXJ5"},
    }


def test_arrow_write_failure_leaves_no_committed_fragment(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    data = _input(
        end_ns=1,
        streams={"camera": _stream(ModalityKind.IMAGE, [(0, object())])},
        attempt="bad-value",
    )

    with pytest.raises(TypeError, match="unsupported aligned value type"):
        AlignmentEngine().align_to_writer(
            data,
            _profile(set(), default_tolerance_ns=0),
            ArrowFragmentWriter(tmp_path),
        )

    assert list(tmp_path.rglob("*.arrow")) == []
    assert list(tmp_path.rglob("*.part")) == []


class _CountingWriter:
    def __init__(self) -> None:
        self.row_count = 0
        self.aborted = False

    def begin(self, *, rollout_id: str, attempt_id: str) -> None:
        assert rollout_id and attempt_id

    def write_row(self, row: AlignedRowV1) -> None:
        assert row.step_index == self.row_count
        self.row_count += 1

    def commit(self, *, row_count: int, content_sha256: str, schema_sha256: str) -> str:
        assert row_count == self.row_count
        assert len(content_sha256) == len(schema_sha256) == 64
        return "memory://counting/fragment.arrow"

    def abort(self) -> None:
        self.aborted = True


def test_align_to_writer_does_not_materialize_output_rows() -> None:
    writer = _CountingWriter()
    data = _input(
        end_ns=60_000_000_000,
        streams={"camera": _stream(ModalityKind.IMAGE, [(0, "frame")])},
    )

    manifest = AlignmentEngine().align_to_writer(
        data, _profile(set(), default_tolerance_ns=0), writer
    )

    assert manifest.row_count == writer.row_count == 1800
    assert writer.aborted is False


def test_profile_cannot_silently_configure_an_absent_stream() -> None:
    data = _input(
        end_ns=1,
        streams={"camera": _stream(ModalityKind.IMAGE, [(0, "frame")])},
    )

    with pytest.raises(ValueError, match="configures absent streams"):
        AlignmentEngine().align_in_memory(
            data,
            _profile(set(), stream_tolerance_ns={"camrea": 1}),
        )


def test_ready_alignment_attempt_migration_is_immutable_but_allows_writing_cleanup() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "alignment"
        / "0003_ready_attempt_immutable.sql"
    ).read_text()

    assert "IF OLD.status = 'READY'" in migration
    assert "BEFORE UPDATE OR DELETE ON aligned_fragment_attempts" in migration
    assert "IF TG_OP = 'DELETE'" in migration

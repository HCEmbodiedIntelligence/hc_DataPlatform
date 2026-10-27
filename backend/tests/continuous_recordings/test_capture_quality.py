from types import SimpleNamespace

import pytest

from hc_data_platform.continuous_recordings.capture_quality import aggregate


def test_qc_offsets_include_accepted_state_and_rejected_nearest_action(tmp_path):
    import json

    from mcap.writer import Writer

    from hc_data_platform.continuous_recordings.capture_quality import alignment_offsets

    path = tmp_path / "sensor.mcap"
    with path.open("wb") as file:
        writer = Writer(file)
        writer.start()
        schema = writer.register_schema("value", "jsonschema", b"{}")
        for topic, value in {
            "source.timestamp_ns": 1_000_000_000,
            "/observation/state": {"source_timestamp_ns": 998_000_000},
            "/action": {
                "source_timestamp_ns": None,
                "nearest_source_timestamp_ns": 950_000_000,
                "alignment_reference_timestamp_ns": 1_000_000_000,
            },
        }.items():
            channel = writer.register_channel(topic, "json", schema)
            writer.add_message(channel, 0, json.dumps(value).encode(), 0)
        writer.finish()
    assert alignment_offsets([path], 0, 100_000_000) == {
        "/action": [-50_000_000],
        "/observation/state": [-2_000_000],
    }


def profile():
    return SimpleNamespace(
        complete_step=SimpleNamespace(minimum_ratio_reject=0.8, minimum_ratio_risk=0.98),
        default_timing=SimpleNamespace(
            target_frequency_hz=30,
            maximum_consecutive_missing_reject=29,
            maximum_consecutive_missing_risk=2,
        ),
    )


def summary(missing, count=120):
    frames = {
        i * 10**9 // 30: {
            "valid": i not in missing,
            "reasons": ["missing_rgb"] if i in missing else [],
        }
        for i in range(count)
    }
    return aggregate(frames, 0, count * 10**9 // 30, profile())


def test_isolated_drops_warn_without_rejecting_the_recording():
    result = summary({49, 81})
    assert result["reject_codes"] == []
    assert result["invalid_rows"] == 2
    assert result["longest_invalid_run"] == 1
    assert result["training_policy"] == "drop_invalid_rows_preserve_source_time/v1"


def test_major_loss_and_long_interruptions_still_reject():
    assert "CAPTURE_VALID_RATIO_LOW" in summary(set(range(40)))["reject_codes"]
    assert "CAPTURE_INTERRUPTION_TOO_LONG" in summary(set(range(30)), 300)["reject_codes"]


def test_missing_evidence_is_not_a_success():
    with pytest.raises(ValueError, match="cover"):
        aggregate({}, 0, 10**9, profile())


def test_null_source_timestamp_is_an_invalid_alignment_not_an_exception():
    from hc_data_platform.alignment.engine import AlignmentEngine
    from hc_data_platform.alignment.models import AlignmentStrategy, TimedSampleV1

    value = AlignmentEngine._single(
        TimedSampleV1(timestamp_ns=12, value=None), 12, AlignmentStrategy.RECENT, 100
    )
    assert not value.valid and value.value is None
    assert value.source_timestamps_ns == (12,)

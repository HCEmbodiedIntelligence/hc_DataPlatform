from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from hc_data_platform.continuous_recordings.presentation import decoded_filename, recording_filename


@pytest.mark.parametrize(
    "source",
    [
        "source/original/raw.mcap",
        "recording-ebb4d87bf9e95de78177e505e748156e.mcap",
        "8b0f915d-12a4-4537-8230-6945eae735a4_recording.mcap",
    ],
)
def test_machine_names_use_task_number_and_capture_time(source):
    recording = SimpleNamespace(
        capture_started_at=datetime(2026, 10, 16, 5, 21, 44, 463582, timezone.utc)
    )
    assert recording_filename(recording, "00000012", source) == "T12_20261016_132144_463.mcap"


def test_readable_original_filename_is_decoded_and_preserved():
    recording = SimpleNamespace(capture_started_at=datetime.now(timezone.utc))
    source = "folder/%E6%8A%93%E5%8F%96%E6%B5%8B%E8%AF%95%20A.mcap"
    assert recording_filename(recording, "00000012", source) == "抓取测试 A.mcap"
    assert decoded_filename(r"C:\recordings\抓取测试.mcap") == "抓取测试.mcap"
    assert decoded_filename("folder/%2E%2E%2Fsafe%0A.mcap") == "safe.mcap"


def test_unknown_task_does_not_fabricate_a_task_number_or_file_format():
    recording = SimpleNamespace(capture_started_at=datetime(2026, 10, 16, tzinfo=timezone.utc))
    assert recording_filename(recording, None, None) == "recording_20261016_080000_000"

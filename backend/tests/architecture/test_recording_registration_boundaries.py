import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "tools/check_architecture.py"


@pytest.mark.parametrize(
    "source",
    [
        "from hc_data_platform.continuous_recordings.asset_repository import _asset_values",
        "from hc_data_platform.continuous_recordings.repository import _recording_values",
        'cursor.execute("INSERT INTO ingest.recording_uploads VALUES (...)")',
        'cursor.execute("SELECT asset_path FROM ingest.recording_upload_assets")',
        'cursor.execute("SELECT recording_id FROM ingest.continuous_recordings")',
    ],
)
def test_bridge_cannot_read_or_write_recording_owned_tables(tmp_path, source):
    path = tmp_path / "hc_data_platform/robot_ingest/recording_bridge.py"
    path.parent.mkdir(parents=True)
    path.write_text(source)
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr

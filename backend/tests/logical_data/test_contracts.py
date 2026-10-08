import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from hc_data_platform.logical_data.models import LogicalSample, LogicalSampleBatch, LogicalStream


def test_independent_samples_never_require_or_fabricate_time_or_robot_fields():
    stream = LogicalStream(
        stream_id="photos", name="images", kind="image", sample_count=1, temporal=False
    )
    sample = LogicalSample(
        sample_id="image-7", sample_index=0, value={"label": "cup"}, valid=True, repeated=False
    )
    assert stream.start_ns is None and sample.timestamp_ns is None
    assert "robot" not in stream.model_dump_json() + sample.model_dump_json()
    with pytest.raises(ValidationError, match="fabricated"):
        LogicalStream(**{**stream.model_dump(), "start_ns": "0"})


def test_batch_rejects_partial_reordered_and_out_of_window_samples():
    sample = LogicalSample(
        sample_id="frame-7",
        sample_index=7,
        timestamp_ns="9007199254740993",
        value=[1.2],
        valid=True,
        repeated=False,
    )
    valid = LogicalSampleBatch(
        snapshot_id="snapshot", stream_id="joint", start=7, end=8, samples=(sample,)
    )
    assert valid.samples[0].timestamp_ns == "9007199254740993"
    for start, end, samples in ((0, 1, (sample,)), (7, 9, (sample,)), (7, 7, (sample,))):
        with pytest.raises(ValidationError, match="completely cover"):
            LogicalSampleBatch(
                snapshot_id="snapshot", stream_id="joint", start=start, end=end, samples=samples
            )


def test_logical_contracts_do_not_import_storage_or_runtime():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import hc_data_platform.logical_data.ports; "
            "assert not any(n in sys.modules for n in "
            "['lance', 'pyarrow', 'fastapi', 'psycopg', 'hc_data_platform.runtime', "
            "'hc_data_platform.episode_store.models', 'hc_data_platform.lance_catalog.models'])",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("physical", ["episode_store.models", "lance_catalog.models", "runtime"])
def test_gate_rejects_physical_dependencies_in_logical_contract(tmp_path, physical):
    file = tmp_path / "hc_data_platform/logical_data/ports.py"
    file.parent.mkdir(parents=True)
    file.write_text(f"import hc_data_platform.{physical}")
    checker = Path(__file__).parents[2] / "tools/check_architecture.py"
    result = subprocess.run(
        [sys.executable, str(checker), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr

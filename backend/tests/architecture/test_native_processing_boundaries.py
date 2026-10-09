import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "tools/check_architecture.py"


@pytest.mark.parametrize("module", ["lerobot_imports.pipeline", "robot_ingest.lerobot_processor"])
@pytest.mark.parametrize(
    "source",
    [
        "from hc_data_platform.quality.postgres import PostgresQualityRepository",
        "from ..ingest.raw_sources import PostgresRawSourceRepository",
        "def process(pipeline):\n    return pipeline.connections()",
        "import importlib\nimportlib.import_module(target)",
    ],
)
def test_native_processor_cannot_recreate_or_borrow_persistence(tmp_path, module, source):
    path = tmp_path / "hc_data_platform" / Path(*module.split(".")).with_suffix(".py")
    path.parent.mkdir(parents=True)
    path.write_text(source)
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr

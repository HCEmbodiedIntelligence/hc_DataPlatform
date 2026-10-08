from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "tools/check_architecture.py"


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("capabilities.registry", "from hc_data_platform.publishing.ports import ExporterPort"),
        (
            "data_formats.video",
            "from hc_data_platform.publishing.export_assets import export_error",
        ),
        ("data_formats.arrow", "from ..publishing.service import canonical_json_bytes"),
        ("data_formats.feature_names", "from ..lerobot_imports import profiles"),
        (
            "data_formats.rgb_video",
            "from ..continuous_recordings.openarm_raw.video import TrainingVideo",
        ),
        (
            "episode_store.migration_rgb",
            "from ..continuous_recordings.openarm_raw.video import TrainingVideo",
        ),
        (
            "episode_store.mcap_package",
            "from hc_data_platform.continuous_recordings.openarm_raw import video",
        ),
        ("data_formats.depth_source", "import importlib\nimportlib.import_module(target)"),
        ("episode_store.writer", "from ..publishing.exporters import _feature_type"),
        ("continuous_recordings.package", "from ..publishing.depth_evidence import file_hash"),
        ("publishing.lerobot_export", "def build():\n    from .exporters import _lerobot_value"),
        ("publishing.service", "from .exporters import LeRobotV3Exporter"),
    ],
)
def test_format_reverse_dependencies_are_rejected(tmp_path: Path, module: str, source: str) -> None:
    file = tmp_path / "hc_data_platform" / Path(*module.split(".")).with_suffix(".py")
    file.parent.mkdir(parents=True)
    file.write_text(source)
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr

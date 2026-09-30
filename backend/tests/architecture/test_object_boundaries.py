from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "tools/check_architecture.py"


@pytest.mark.parametrize(
    "source",
    [
        "from hc_data_platform.ingest.ports import ObjectStoragePort",
        "from ..ingest import ports",
        "def late():\n    from hc_data_platform.ingest.adapters import S3ObjectStorage",
        'import importlib\nimportlib.import_module("hc_data_platform.ingest.models")',
        "from .providers import S3ObjectStorage",
        "from hc_data_platform.object_storage import providers",
        "import boto3",
        "import importlib\nimportlib.import_module(computed_name)",
    ],
)
def test_contract_boundary_rejects_implementation_dependencies(tmp_path: Path, source: str) -> None:
    package = tmp_path / "hc_data_platform/object_storage"
    package.mkdir(parents=True)
    (package / "ports.py").write_text(source)
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr


def test_repository_migrated_boundaries() -> None:
    result = subprocess.run([sys.executable, str(CHECKER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr

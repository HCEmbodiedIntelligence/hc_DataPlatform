import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "tools/check_architecture.py"


@pytest.mark.parametrize(
    "module,table",
    [
        ("robot_assets", "registry.robot_model_versions"),
        ("registry", "registry.organization_robot_model_bindings"),
        ("registry", "robotics.robot_assets"),
    ],
)
def test_binding_repositories_cannot_reacquire_foreign_model_or_instance_sql(
    tmp_path, module, table
):
    path = tmp_path / "hc_data_platform" / module / "repository.py"
    path.parent.mkdir(parents=True)
    path.write_text(f'cursor.execute("SELECT 1 FROM {table}")')
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr

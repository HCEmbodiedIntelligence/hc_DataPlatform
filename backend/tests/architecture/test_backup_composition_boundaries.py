import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "tools/check_architecture.py"


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("restore_cli", "from .whole_cli import _repository"),
        ("restore_cli", "import hc_data_platform.backup.whole_cli as whole"),
        ("composition", "from .restore_cli import _environment"),
    ],
)
def test_rejects_backup_command_composition_cycles(tmp_path, module, source):
    file = tmp_path / "hc_data_platform/backup" / (module + ".py")
    file.parent.mkdir(parents=True)
    file.write_text(source)
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr


def test_shared_composition_and_restore_import_without_whole_command():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import hc_data_platform.backup.composition; "
            "import hc_data_platform.backup.restore_cli; "
            'assert "hc_data_platform.backup.whole_cli" not in sys.modules',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

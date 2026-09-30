import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "tools/check_architecture.py"


@pytest.mark.parametrize(
    "source",
    [
        "from hc_data_platform.dataset_registry.publication_postgres "
        "import PostgresDatasetPublicationProjection",
        "from ..continuous_recordings.lineage import materialize_rollout",
        "from ..integrations.publication import postgres_publication_effects",
        'cursor.execute("INSERT INTO dataset_registry.dataset_versions VALUES (...)")',
        'cursor.execute("SELECT owner_id FROM dataset_registry.datasets")',
        'cursor.execute(f"INSERT INTO access_control.{table} VALUES (...)")',
    ],
)
def test_publication_cannot_reacquire_foreign_sql_or_concrete_participants(tmp_path, source):
    file = tmp_path / "hc_data_platform/publishing/postgres.py"
    file.parent.mkdir(parents=True)
    file.write_text(source)
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr

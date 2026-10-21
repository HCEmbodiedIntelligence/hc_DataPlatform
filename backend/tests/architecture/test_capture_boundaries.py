"""Protect the small integration points; reject restored implementation coupling."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module,source",
    [
        (
            "capture_schemes/router.py",
            "from hc_data_platform.lerobot_imports.router import get_service",
        ),
        (
            "capture_schemes/router.py",
            "from hc_data_platform.robot_assets.router import get_organization_robot_asset_service",
        ),
        ("capture_schemes/router.py", "from hc_data_platform.core.config import get_settings"),
        (
            "capture_schemes/router.py",
            "from hc_data_platform.core.dbapi import psycopg_connection_factory",
        ),
        ("robot_ingest/connections.py", "service.repository._scoped()"),
        (
            "robot_ingest/connections.py",
            "from hc_data_platform.capture_schemes.plans import for_robot",
        ),
        ("robot_ingest/repository.py", "from hc_data_platform.capture_schemes.common import lock"),
        (
            "collection_tasks/postgres.py",
            "from hc_data_platform.capture_schemes.plans import save_plan",
        ),
        (
            "collection_tasks/postgres.py",
            'db.execute("SELECT 1 FROM collection_tasks.capture_plan_heads")',
        ),
        ("capture_schemes/ports.py", "from .postgres import PostgresRecordingPlans"),
        (
            "capture_schemes/references.py",
            "from ..continuous_recordings.mcap_native import McapProcessingConfig",
        ),
        (
            "collection_tasks/models.py",
            "from hc_data_platform.capture_schemes.models import SetTaskPlan",
        ),
        (
            "robot_ingest/models.py",
            "from hc_data_platform.capture_schemes.models import CaptureReference",
        ),
    ],
)
def test_capture_integration_cannot_reacquire_internal_dependencies(tmp_path, module, source):
    path = tmp_path / "hc_data_platform" / module
    path.parent.mkdir(parents=True)
    path.write_text(source)
    checker = Path(__file__).resolve().parents[2] / "tools/check_architecture.py"
    result = subprocess.run(
        [sys.executable, str(checker), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr

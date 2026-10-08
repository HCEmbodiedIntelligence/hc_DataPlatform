"""Contract imports must work without numerical, database, HTTP or workflow SDKs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module,symbol",
    [
        ("annotation.sampling", "FrameSelectionSampler"),
        ("ingest.projection_metadata", "build_alignment_metadata"),
        ("core.execution", "ExecutionScope"),
        ("lance_catalog", "DatasetSchemaSnapshot"),
        ("lance_catalog", "StepReaderPort"),
        ("lance_catalog", "compute_schema_fingerprint"),
        ("lance_catalog.models", "DatasetSchemaSnapshot"),
        ("alignment", "AlignmentInputV1"),
        ("quality", "QualityProfileV1"),
        ("verification", "RawVerificationReportV1"),
        ("publishing", "PublishedDatasetManifestV1"),
        ("workflow", "JobRecord"),
        ("backup", "BackupManifestV1"),
        ("backup.contracts", "BackupManifestV1"),
        ("audit_projection", "AuditEventEnvelope"),
        ("collection_tasks", "CollectionTask"),
        ("dashboard", "DashboardActivityEvent"),
        ("lerobot_imports.local_files", "safe_relative"),
        ("continuous_recordings.openarm_raw.clock_diagnostics", "clock_reasons"),
    ],
)
def test_import_contract_without_loading_implementations(module, symbol):
    script = """
import importlib, importlib.abc, sys
blocked = ('psycopg', 'sqlalchemy', 'boto3', 'oss2', 'pyarrow', 'lance', 'numpy',
           'fastapi', 'temporalio', 'hc_data_platform.runtime')
class NoImplementation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in blocked):
            raise AssertionError('contract loaded implementation: ' + fullname)
sys.meta_path.insert(0, NoImplementation())
module, symbol = __import__('json').loads(sys.argv[1])
value = getattr(importlib.import_module('hc_data_platform.' + module), symbol)
assert value is not None
assert 'hc_data_platform.lance_catalog.adapters' not in sys.modules
assert 'hc_data_platform.lance_catalog.service' not in sys.modules
if symbol == 'DatasetSchemaSnapshot':
    value.create(project_id='p', dataset_id='d', schema_snapshot_id='s',
                 frequency_hz=30, fields={'state': 'list<float32>'})
"""
    result = subprocess.run(
        [sys.executable, "-c", script, json.dumps((module, symbol))],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "module,source",
    [
        ("lance_catalog/models.py", "from .schema import compute_schema_fingerprint"),
        ("lance_catalog/schema_spec.py", "from .models import DatasetSchemaSnapshot"),
        ("lerobot_imports/capture.py", "from .reader import local_file"),
        ("lerobot_imports/local_files.py", "from .capture import read_capture_episode"),
        ("continuous_recordings/openarm_raw/named.py", "from .alignment import clock_reasons"),
        ("continuous_recordings/openarm_raw/clock_diagnostics.py", "import numpy"),
        ("lance_catalog/__init__.py", "from .adapters import LanceAdapter"),
        ("backup/__init__.py", "from .whole import WholeBackupPlanV1"),
        (
            "robot_ingest/processing_worker.py",
            "from hc_data_platform.workflow.activities import _worker_scope",
        ),
        (
            "lerobot_imports/pipeline.py",
            "from hc_data_platform.workflow.ingest_plan import _FrameSelectionSampler",
        ),
        ("annotation/sampling.py", "import temporalio"),
        (
            "ingest/projection_metadata.py",
            "from hc_data_platform.workflow.models import IngestProjectionSourceV1",
        ),
    ],
)
def test_architecture_rejects_restored_import_side_effects(tmp_path, module, source):
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

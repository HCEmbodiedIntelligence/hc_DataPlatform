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
        "from hc_data_platform.workflow.projection_store import ProjectionArtifactStorePort",
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


@pytest.mark.parametrize("directory", ["tools", "scripts"])
def test_real_tool_entries_cannot_bypass_canonical_imports(tmp_path: Path, directory: str) -> None:
    backend = tmp_path / "backend"
    (backend / "tools").mkdir(parents=True)
    package = backend / "src/hc_data_platform"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    checker = backend / "tools/check_architecture.py"
    checker.write_text(CHECKER.read_text())
    tool = backend / directory / "migration.py"
    tool.parent.mkdir(exist_ok=True)
    tool.write_text("from hc_data_platform.ingest import S3ObjectStorage\n")
    rejected = subprocess.run([sys.executable, str(checker)], capture_output=True, text=True)
    assert rejected.returncode == 1
    assert f"{directory}/migration.py:1:" in rejected.stderr
    tool.write_text("from hc_data_platform.object_storage.providers import S3ObjectStorage\n")
    accepted = subprocess.run([sys.executable, str(checker)], capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_ingest_http_dto_keeps_its_public_namespace(tmp_path: Path) -> None:
    package = tmp_path / "hc_data_platform/consumer"
    package.mkdir(parents=True)
    (package / "http.py").write_text("from hc_data_platform.ingest import CompletedPart\n")
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("consumer", ["commit", "delivery"])
def test_episode_writers_cannot_reintroduce_multipart(tmp_path: Path, consumer: str) -> None:
    package = tmp_path / "hc_data_platform/episode_store"
    package.mkdir(parents=True)
    (package / f"{consumer}.py").write_text("storage.create_multipart(key)\n")
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "ImmutableObjectWriter" in result.stderr

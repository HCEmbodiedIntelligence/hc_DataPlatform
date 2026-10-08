"""Tag projection cannot recover the ingest service or its internal collaborators."""

import ast
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "tools/check_architecture.py"


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("tag_projection", "projector._episode_packages.files.open(identity, version)"),
        ("tag_projection", "projector._insert_projection(cursor)"),
        ("tag_projection", "from .ingest_projection import PostgresDatasetIngestProjector"),
        ("projection_writer", "from .ingest_projection import _ProjectionFacts"),
        ("projection_models", "from .ingest_projection import _stable_id"),
    ],
)
def test_tag_projection_rejects_service_cycles_and_private_access(tmp_path, module, source):
    file = tmp_path / "hc_data_platform/dataset_registry" / (module + ".py")
    file.parent.mkdir(parents=True)
    file.write_text(source)
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr


def test_projection_modules_are_acyclic_including_late_imports():
    package = "hc_data_platform.dataset_registry"
    directory = CHECKER.parent.parent / "src/hc_data_platform/dataset_registry"
    modules = {
        package + "." + n: directory / (n + ".py")
        for n in [
            "ingest_projection",
            "tag_projection",
            "projection_models",
            "projection_writer",
            "migration_projection",
        ]
    }
    edges = {n: set() for n in modules}
    for name, path in modules.items():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                edges[name].update(a.name for a in node.names if a.name in modules)
            if isinstance(node, ast.ImportFrom):
                imported = (
                    resolve_name("." * node.level + (node.module or ""), package)
                    if node.level
                    else node.module
                )
                if imported in modules:
                    edges[name].add(imported)

    def visit(name, path):
        assert name not in path, " -> ".join((*path, name))
        for edge in edges[name]:
            visit(edge, (*path, name))

    for name in modules:
        visit(name, ())

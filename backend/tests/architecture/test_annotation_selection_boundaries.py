"""Consumers cannot reacquire annotation internals or build foreign repositories."""

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
        ("episode_store/derivation_service", "annotations._freeze(task, body, auth)"),
        ("episode_store/collection_delivery", "owner.annotations._snapshot(row, plan, auth)"),
        (
            "episode_store/derivation_service",
            "from hc_data_platform.annotation.automation "
            "import PostgresAutomaticAnnotationRepository",
        ),
        (
            "episode_store/derivation_service",
            "from hc_data_platform.dataset_registry.ingest_projection "
            "import PostgresDatasetIngestProjector",
        ),
        ("episode_store/collection_delivery", "from .delivery import inventory_document"),
        ("annotation/selection_ports", "from .revision_files import RevisionFileDownload"),
        (
            "annotation/delivery_models",
            "from hc_data_platform.episode_store.annotated_delivery import ReviewReference",
        ),
    ],
)
def test_rejects_cross_domain_implementation_dependencies(tmp_path, module, source):
    file = tmp_path / "hc_data_platform" / (module + ".py")
    file.parent.mkdir(parents=True)
    file.write_text(source)
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--source-root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr


def test_selection_contract_imports_do_not_load_storage_or_execution():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import hc_data_platform.annotation.selection_ports; "
            "assert not any(n in sys.modules for n in "
            "['psycopg', 'pyarrow', 'fastapi', "
            "'hc_data_platform.annotation.revision_files', "
            "'hc_data_platform.annotation.delivery', "
            "'hc_data_platform.episode_store.annotated_delivery'])",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_episode_module_imports_have_no_cycles_including_late_imports():
    root = CHECKER.parent.parent / "src/hc_data_platform/episode_store"
    prefix = "hc_data_platform.episode_store"
    modules = {f"{prefix}.{file.stem}": file for file in root.glob("*.py")}
    edges = {module: set() for module in modules}
    for module, file in modules.items():
        for node in ast.walk(ast.parse(file.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ""
                if node.level:
                    name = resolve_name("." * node.level + name, prefix)
                names.extend([name, *(f"{name}.{a.name}" for a in node.names)])
            edges[module].update(name for name in names if name in modules)

    def visit(module, path):
        assert module not in path, " -> ".join((*path, module))
        for dependency in edges[module]:
            visit(dependency, (*path, module))

    for module in modules:
        visit(module, ())

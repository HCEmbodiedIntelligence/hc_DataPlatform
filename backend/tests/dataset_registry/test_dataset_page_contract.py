from __future__ import annotations

from pathlib import Path

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.health import ReadinessProbe


class ReadyProbe:
    async def check(self) -> None:
        return None


def test_p05_runtime_openapi_has_exact_page_aggregate_contract() -> None:
    ready: ReadinessProbe = ReadyProbe()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={"postgresql": ready, "temporal": ready, "object_storage": ready},
    )
    paths = app.openapi()["paths"]
    root = "/api/v1/projects/{project_id}/datasets"
    expected = {
        root: {"get", "post"},
        f"{root}:page-capabilities": {"get"},
        f"{root}:summary": {"get"},
        f"{root}:facets": {"get"},
    }
    assert {
        path: {method for method in item if method in {"get", "post", "patch"}}
        for path, item in paths.items()
        if path == root or path.startswith(f"{root}:")
    } == expected
    assert paths[root]["get"]["operationId"] == "listDatasets"
    assert paths[root]["post"]["operationId"] == "createDataset"
    assert paths[f"{root}:summary"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/DatasetPageSummaryEnvelope"}


def test_p05_migration_is_scoped_and_keeps_page_documents_safe() -> None:
    text = (
        Path(__file__).parents[2] / "migrations/dataset_registry/0001_dataset_page_registry.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS dataset_registry" in text
    assert "REFERENCES registry.organization_projects" in text
    assert "dataset_page_scope_name_uq" in text
    assert "dataset_page_channels_gin_idx" in text
    assert "NOT (dataset_document ? 'credential_input')" in text
    assert "core.apply_project_rls('dataset_registry.datasets'::regclass)" in text

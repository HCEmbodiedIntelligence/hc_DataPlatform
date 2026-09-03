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
    assert "tags" in app.openapi()["components"]["schemas"]["DatasetPageFacets"]["properties"]
    for path in (root, f"{root}:summary", f"{root}:facets"):
        parameters = {
            parameter["name"]: parameter for parameter in paths[path]["get"]["parameters"]
        }
        workflow_schema = parameters["workflow_state"]["schema"]
        assert workflow_schema["anyOf"][0]["enum"] == [
            "pendingReview",
            "returned",
            "actionableDraft",
        ]
        assert "channels" in parameters
        assert "channel_match" in parameters


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


def test_dataset_folders_and_task_membership_are_project_safe() -> None:
    root = Path(__file__).parents[2]
    folders = (
        root / "migrations/dataset_registry/0005_dataset_folders_and_task_assignment.sql"
    ).read_text(encoding="utf-8")
    membership = (
        root / "migrations/dataset_registry/0006_dataset_task_membership_lookup.sql"
    ).read_text(encoding="utf-8")
    cross_region_guard = (
        root / "migrations/dataset_registry/0007_cross_region_dataset_reassignment_guard.sql"
    ).read_text(encoding="utf-8")

    assert "DROP CONSTRAINT IF EXISTS collection_tasks_dataset_id_uq" in folders
    assert "valid_folder_path" in folders
    assert "cardinality(selected_path) <= 16" in folders
    assert "collection_task_dataset_reassignment_guard" in folders
    assert "collection_tasks_dataset_membership_idx" in membership
    assert "dataset_has_collection_task" in membership
    assert "SECURITY DEFINER" in membership
    assert "SECURITY DEFINER" in cross_region_guard
    assert "rollout.region_code" not in cross_region_guard


def test_p05_postgres_task_filter_is_parameterized_exists_semantics() -> None:
    text = (
        Path(__file__).parents[2] / "src/hc_data_platform/dataset_registry/repository.py"
    ).read_text(encoding="utf-8")

    assert "STRPOS(LOWER(COALESCE(dataset.task, '')), LOWER(%s)) > 0" in text
    assert "STRPOS(LOWER(COALESCE(episode.task, '')), LOWER(%s)) > 0" in text
    assert "EXISTS (" in text
    assert "dataset_registry.dataset_has_collection_task" in text
    assert "CASE %s" in text

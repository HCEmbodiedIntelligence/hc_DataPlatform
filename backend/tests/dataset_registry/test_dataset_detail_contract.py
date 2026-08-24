from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.health import ReadinessProbe


class ReadyProbe:
    async def check(self) -> None:
        return None


def _app() -> FastAPI:
    ready: ReadinessProbe = ReadyProbe()
    return create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={"postgresql": ready, "temporal": ready, "object_storage": ready},
    )


def test_p06_runtime_openapi_owns_the_strict_dataset_detail_paths() -> None:
    paths = _app().openapi()["paths"]
    root = "/api/v1/projects/{project_id}/datasets/{dataset_id}"
    expected = {
        f"{root}/bootstrap": "getDatasetBootstrap",
        f"{root}/versions": "listDatasetVersions",
        f"{root}/versions/{{version_id}}/schema-summary": "getDatasetVersionSchemaSummary",
        f"{root}/versions/{{version_id}}/source-provenance": "listDatasetVersionSourceProvenance",
        f"{root}/versions/{{version_id}}/capacity-facts": "getDatasetVersionCapacityFacts",
        f"{root}/versions/{{version_id}}/episodes": "listVersionEpisodes",
    }
    assert {path: paths[path]["get"]["operationId"] for path in expected} == expected
    assert paths[f"{root}/versions"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/DatasetPageVersionListEnvelope"}
    assert f"{root}/lance-versions" in paths
    assert paths[f"{root}/lance-versions"]["get"]["operationId"] == "listLanceCatalogVersions"


def test_p06_projection_migration_is_normalized_scoped_and_safe() -> None:
    text = (
        Path(__file__).parents[2]
        / "migrations/dataset_registry/0002_dataset_detail_projections.sql"
    ).read_text(encoding="utf-8")
    for table in (
        "dataset_versions",
        "dataset_detail_facts",
        "dataset_version_schema_summaries",
        "dataset_version_source_provenance",
        "dataset_version_capacity_facts",
        "dataset_version_episodes",
    ):
        assert f"dataset_registry.{table}" in text
        assert f"core.apply_project_rls('dataset_registry.{table}'::regclass)" in text
    assert "REFERENCES dataset_registry.datasets" in text
    assert "REFERENCES dataset_registry.dataset_versions" in text
    assert "dataset_detail_episodes_ordinal_idx" in text
    assert "NOT (provenance_document ? 'object_locator')" in text
    assert "NOT (episode_document ? 'object_path')" in text

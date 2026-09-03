from __future__ import annotations

from pathlib import Path

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.health import ReadinessProbe


class ReadyProbe:
    async def check(self) -> None:
        return None


def test_p17_runtime_openapi_has_real_authoring_validation_and_publication() -> None:
    ready: ReadinessProbe = ReadyProbe()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={"postgresql": ready, "temporal": ready, "object_storage": ready},
    )
    paths = app.openapi()["paths"]
    org = "/api/v1/organizations/{organization_id}/stream-schemas"
    assert {path for path in paths if path.startswith(org)} == {
        org,
        f"{org}:import",
        f"{org}/{{schema_id}}/versions/{{schema_version}}",
        f"{org}/{{schema_id}}/versions/{{schema_version}}:validate",
        f"{org}/{{schema_id}}/versions/{{schema_version}}:preflight-publish",
        f"{org}/{{schema_id}}/versions/{{schema_version}}:publish",
    }
    assert paths[org]["get"]["operationId"] == "listStreamSchemas"
    assert {parameter["name"] for parameter in paths[org]["get"]["parameters"]} >= {
        "organization_id",
        "X-Project-ID",
        "q",
        "status",
        "logical_type",
        "after",
        "before",
        "limit",
    }
    assert paths[org]["post"]["operationId"] == "createStreamSchema"
    assert paths[f"{org}:import"]["post"]["operationId"] == "importStreamSchema"
    references = (
        "/api/v1/organizations/{organization_id}/projects/{project_id}/regions/{region_code}"
        "/stream-schemas/{schema_id}/versions/{schema_version}/dataset-references"
    )
    assert paths[references]["get"]["operationId"] == "listDataSchemaDatasetReferences"
    assert paths[references]["post"]["operationId"] == "associateDataSchemaDatasetReference"
    assert (
        paths[f"{org}/{{schema_id}}/versions/{{schema_version}}"]["patch"]["operationId"]
        == "updateStreamSchemaDraft"
    )
    assert (
        paths[f"{org}/{{schema_id}}/versions/{{schema_version}}:validate"]["post"]["operationId"]
        == "validateStreamSchemaVersion"
    )
    route = "/api/v1/projects/{project_id}/regions/{region_code}/route-resolutions/p15-to-p17"
    assert paths[route]["get"]["operationId"] == "resolveP15DataSchemaRoute"
    for suffix in ("preflight-publish", "publish"):
        operation = paths[f"{org}/{{schema_id}}/versions/{{schema_version}}:{suffix}"]["post"]
        assert "501" not in operation["responses"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("Envelope")
    text = (
        Path(__file__).parents[2] / "migrations/data_schemas/0001_data_schema_reads.sql"
    ).read_text(encoding="utf-8")
    assert "data_schemas.stream_schema_versions" in text
    authoring = (
        Path(__file__).parents[2] / "migrations/data_schemas/0002_schema_authoring.sql"
    ).read_text(encoding="utf-8")
    for required in (
        "data_schemas.schema_validation_reports",
        "data_schemas.schema_publish_preflights",
        "data_schemas.schema_command_receipts",
        "hc_organization_scope_isolation",
    ):
        assert required in authoring
    references_migration = (
        Path(__file__).parents[2] / "migrations/data_schemas/0003_dataset_version_references.sql"
    ).read_text(encoding="utf-8")
    for required in (
        "data_schemas.stream_schema_dataset_version_references",
        "dataset_registry.dataset_versions",
        "ASSOCIATE_DATASET",
        "core.apply_project_rls",
    ):
        assert required in references_migration

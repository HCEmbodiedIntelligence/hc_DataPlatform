from __future__ import annotations

from pathlib import Path

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.health import ReadinessProbe


class ReadyProbe:
    async def check(self) -> None:
        return None


def test_p02_runtime_openapi_has_full_read_write_and_connection_job_contract() -> None:
    ready: ReadinessProbe = ReadyProbe()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={"postgresql": ready, "temporal": ready, "object_storage": ready},
    )
    paths = app.openapi()["paths"]
    root = "/api/v1/projects/{project_id}/regions/{region_code}/data-sources"
    expected = {
        root: {"post"},
        f"{root}/page": {"get"},
        f"{root}/{{source_id}}": {"get", "patch"},
        f"{root}/{{source_id}}:rotate-credential": {"post"},
        f"{root}/{{source_id}}:test-connection": {"post"},
        f"{root}/{{source_id}}:enable": {"post"},
        f"{root}/{{source_id}}:disable": {"post"},
    }
    assert {
        path: {method for method in item if method in {"get", "post", "patch"}}
        for path, item in paths.items()
        if path == root or path.startswith(f"{root}/")
    } == expected
    assert paths[root]["post"]["operationId"] == "createDataSource"
    assert paths[f"{root}/{{source_id}}:test-connection"]["post"]["responses"]["202"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ConnectionTestJobEnvelope"}
    assert (
        "DataSourceAsyncJob"
        in paths["/api/v1/jobs/{job_id}"]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["anyOf"][1]["$ref"]
    )


def test_p02_migration_keeps_secrets_out_of_source_projection() -> None:
    text = (
        Path(__file__).parents[2] / "migrations/data_sources/0001_data_source_registry.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in text
    assert "ingest.data_source_credentials" in text
    assert "secret_ciphertext bytea NOT NULL" in text
    assert "NOT (source_document ? 'credential_input')" in text
    assert "core.apply_project_rls('ingest.data_sources'::regclass)" in text

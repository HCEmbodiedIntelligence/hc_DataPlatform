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


def test_p11_runtime_openapi_exposes_the_server_owned_workbench_contract() -> None:
    paths = _app().openapi()["paths"]
    root = "/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts/{draft_id}"
    expected = {
        f"{root}/bootstrap": ("get", "getCleaningDraftBootstrap"),
        f"{root}/edl": ("put", "saveCleaningEdl"),
        f"{root}/previews": ("post", "createCleaningPreview"),
        f"{root}/commits": ("post", "commitCleaningDraft"),
        f"{root}/review-findings": ("get", "getCleaningReviewFindings"),
    }
    assert {
        path: paths[path][method]["operationId"]
        for path, (method, _operation_id) in expected.items()
    } == {path: operation_id for path, (_method, operation_id) in expected.items()}
    assert paths[f"{root}/edl"]["put"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/SaveCleaningEdlEnvelope"}
    assert paths[f"{root}/previews"]["post"]["responses"]["202"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/PreviewAcceptedEnvelope"}
    assert paths[f"{root}/commits"]["post"]["responses"]["202"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/CommitAcceptedEnvelope"}
    assert "post" not in paths[f"{root}/review-findings"]


def test_p11_migration_preserves_raw_identity_and_creates_atomic_return_successors() -> None:
    text = (
        Path(__file__).parents[2] / "migrations/manual_cleaning/0003_cleaning_workbench.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_workbench_drafts" in text
    assert "CREATE TABLE IF NOT EXISTS manual_cleaning.cleaning_draft_edl_revisions" in text
    assert "client_mutation_id text NOT NULL" in text
    assert "cleaning_draft_edl_revisions_mutation_uq" in text
    assert "cleaning_workbench_draft_source_immutable" in text
    assert "dataset_version_successors_seed_workbench" in text
    assert "Review Return requires exactly one editable base revision" in text
    assert "core.apply_project_rls('manual_cleaning.cleaning_workbench_drafts'::regclass)" in text

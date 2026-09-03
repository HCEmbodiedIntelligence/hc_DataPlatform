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


def test_p10_runtime_openapi_is_read_only_for_its_projection_paths() -> None:
    paths = _app().openapi()["paths"]
    root = "/api/v1/projects/{project_id}/regions/{region_code}/cleaning-drafts"
    expected = {
        root: ("get", "listCleaningDrafts"),
        f"{root}:summary": ("get", "getCleaningDraftSummary"),
        f"{root}/{{draft_id}}/summary": ("get", "getCleaningDraftDetail"),
        f"{root}/{{draft_id}}/events": ("get", "listCleaningDraftEvents"),
    }

    assert {
        path: paths[path][method]["operationId"]
        for path, (method, _operation_id) in expected.items()
    } == {path: operation_id for path, (_method, operation_id) in expected.items()}
    # P11 now owns additional workbench paths under the same stable Draft URL.
    # P10 itself remains read-only and keeps ownership of only these projections.
    assert set(expected).issubset(paths)
    assert "post" not in paths[root]
    assert all("post" not in paths[path] for path in expected)

    list_parameters = {parameter["name"] for parameter in paths[root]["get"]["parameters"]}
    assert list_parameters >= {
        "scope",
        "status",
        "q",
        "dataset_id",
        "base_version_id",
        "episode_id",
        "robot_id",
        "creator_id",
        "updated_from",
        "updated_to",
        "preview_status",
        "commit_status",
        "version_review_status",
        "finding_type",
        "finding_severity",
        "sort",
        "after",
        "before",
        "limit",
    }
    assert paths[f"{root}/{{draft_id}}/summary"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/CleaningDraftDetailEnvelope"}
    assert f"{root}/{{draft_id}}/operations" not in paths
    assert f"{root}/{{draft_id}}:preview" not in paths
    assert f"{root}/{{draft_id}}:commit" not in paths


def test_p10_migration_adds_only_safe_read_projection_support() -> None:
    text = (
        Path(__file__).parents[2]
        / "migrations/manual_cleaning/0002_cleaning_draft_read_projections.sql"
    ).read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS projection_version bigint NOT NULL DEFAULT 1" in text
    assert "cleaning_drafts_projection_version_positive" in text
    assert "CHECK (projection_version > 0)" in text
    assert "cleaning_drafts_projection_page_idx" in text
    assert "cleaning_drafts_projection_status_idx" in text
    assert "CREATE TABLE" not in text
    assert "cleaning_draft_commits" not in text
    assert "dataset_version_successor_drafts" not in text

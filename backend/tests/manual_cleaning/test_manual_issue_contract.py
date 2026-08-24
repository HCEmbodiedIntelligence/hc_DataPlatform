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


def test_p09_runtime_openapi_owns_the_manual_issue_contract() -> None:
    paths = _app().openapi()["paths"]
    root = "/api/v1/projects/{project_id}/regions/{region_code}/manual-issues"
    expected = {
        f"{root}:page": ("get", "getManualIssuesPage"),
        f"{root}/{{issue_id}}": ("get", "getManualIssue"),
        f"{root}/{{issue_id}}:triage": ("post", "triageManualIssue"),
        f"{root}/{{issue_id}}/cleaning-drafts": ("post", "createCleaningDraftFromManualIssue"),
        f"{root}/{{issue_id}}:resolve": ("post", "resolveManualIssue"),
    }
    assert paths[f"{root}:page"]["get"]["operationId"] == "getManualIssuesPage"
    assert paths[root]["get"]["operationId"] == "listManualIssues"
    assert paths[root]["post"]["operationId"] == "createManualIssue"
    assert {
        path: paths[path][method]["operationId"]
        for path, (method, _operation_id) in expected.items()
    } == {path: operation_id for path, (_method, operation_id) in expected.items()}

    list_operation = paths[root]["get"]
    assert {parameter["name"] for parameter in list_operation["parameters"]} >= {
        "q",
        "dataset_id",
        "version_id",
        "episode_id",
        "status",
        "issue_type",
        "severity",
        "assignee_id",
        "sort",
        "after",
        "before",
        "limit",
    }
    assert paths[root]["post"]["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ManualIssueDetailEnvelope"
    }
    assert paths[f"{root}/{{issue_id}}/cleaning-drafts"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ManualIssueCreateDraftEnvelope"}
    assert f"{root}/{{issue_id}}/preview-descriptors" not in paths


def test_p09_migration_keeps_manual_issues_scoped_safe_and_separate() -> None:
    text = (
        Path(__file__).parents[2] / "migrations/manual_cleaning/0001_manual_issue_registry.sql"
    ).read_text(encoding="utf-8")
    tables = (
        "manual_issues",
        "cleaning_drafts",
        "manual_issue_draft_links",
        "cleaning_draft_ancestry",
        "cleaning_draft_commits",
    )
    for table in tables:
        assert f"manual_cleaning.{table}" in text
        assert f"core.apply_project_rls('manual_cleaning.{table}'::regclass)" in text
    assert "REFERENCES dataset_registry.dataset_version_episode_revisions" in text
    assert "REFERENCES dataset_registry.dataset_versions" in text
    assert "cleaning_draft_commits_output_version_uq" in text
    assert "manual_issue_source_immutable" in text
    assert "cleaning_draft_source_immutable" in text
    assert "manual_issue_draft_links_append_only" in text
    assert "cleaning_draft_ancestry_append_only" in text
    assert "dataset_version_review_findings" not in text
    for forbidden in ("credential", "secret", "token", "object_locator", "object_path"):
        assert f"NOT (issue_document ? '{forbidden}')" in text
        assert f"NOT (draft_document ? '{forbidden}')" in text

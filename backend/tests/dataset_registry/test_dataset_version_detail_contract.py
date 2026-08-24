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


def test_p07_runtime_openapi_owns_fixed_version_delivery_and_review_paths() -> None:
    paths = _app().openapi()["paths"]
    root = "/api/v1/projects/{project_id}/datasets/{dataset_id}"
    expected = {
        f"{root}/versions/{{version_id}}/bootstrap": ("get", "getDatasetVersionBootstrap"),
        f"{root}/versions/{{version_id}}/episode-revisions/{{revision_id}}": (
            "get",
            "getEpisodeRevision",
        ),
        f"{root}/versions/{{version_id}}/manifest": ("get", "getVersionManifest"),
        f"{root}/versions/{{version_id}}/schema": ("get", "getVersionSchema"),
        f"{root}/versions/{{version_id}}/required-storage": ("get", "listRequiredStorage"),
        f"{root}/versions/{{version_id}}/operational-inventory": (
            "get",
            "listOperationalInventory",
        ),
        f"{root}/versions/{{version_id}}/review-checks": ("post", "runVersionReviewChecks"),
        f"{root}/versions/{{version_id}}:approve": ("post", "approveVersionReview"),
        f"{root}/versions/{{version_id}}:return": ("post", "returnVersionReview"),
        f"{root}/versions/{{version_id}}/diff-jobs": ("post", "createVersionDiffJob"),
        f"{root}/deletion-checks": ("post", "preflightDatasetDeletion"),
        f"{root}/versions/{{version_id}}/deletion-checks": ("post", "preflightVersionDeletion"),
    }
    assert {path: paths[path][method]["operationId"] for path, (method, _) in expected.items()} == {
        path: operation_id for path, (_, operation_id) in expected.items()
    }

    required_storage = paths[f"{root}/versions/{{version_id}}/required-storage"]["get"]
    assert {parameter["name"] for parameter in required_storage["parameters"]} >= {
        "snapshot_token",
        "sort",
        "after",
        "before",
        "limit",
    }
    operational_inventory = paths[f"{root}/versions/{{version_id}}/operational-inventory"]["get"]
    assert {parameter["name"] for parameter in operational_inventory["parameters"]} >= {
        "operational_revision",
        "sort",
        "after",
        "before",
        "limit",
    }
    approve_schema = paths[f"{root}/versions/{{version_id}}:approve"]["post"]["responses"]["202"][
        "content"
    ]["application/json"]["schema"]
    assert approve_schema == {"$ref": "#/components/schemas/DatasetPageApproveReviewResult"}


def test_p07_migration_is_normalized_scoped_and_safe() -> None:
    text = (
        Path(__file__).parents[2]
        / "migrations/dataset_registry/0003_dataset_version_review_and_delivery.sql"
    ).read_text(encoding="utf-8")
    tables = (
        "dataset_version_content_projections",
        "dataset_version_episode_revisions",
        "dataset_version_schema_details",
        "dataset_version_manifest_entries",
        "dataset_version_required_storage",
        "dataset_version_operational_inventory",
        "dataset_version_review_decisions",
        "dataset_version_review_findings",
        "dataset_version_successor_drafts",
        "dataset_version_async_jobs",
    )
    for table in tables:
        assert f"dataset_registry.{table}" in text
        assert f"core.apply_project_rls('dataset_registry.{table}'::regclass)" in text
    assert "REFERENCES dataset_registry.dataset_versions" in text
    assert "REFERENCES dataset_registry.dataset_version_review_decisions" in text
    assert "dataset_version_async_jobs_version_idx" in text
    for forbidden in ("credential", "secret", "token", "object_locator", "object_path"):
        assert f"NOT (content_document ? '{forbidden}')" in text
    assert "position('/' IN safe_locator) = 0" in text

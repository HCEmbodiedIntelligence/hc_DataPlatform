from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI

from hc_data_platform.core.openapi import aggregate_fragments
from hc_data_platform.dashboard.router import router

BACKEND = Path(__file__).resolve().parents[2]
FRAGMENT = BACKEND / "openapi" / "dashboard.yaml"
MIGRATION = BACKEND / "migrations" / "dashboard" / "0001_dashboard_query_indexes.sql"
BUSINESS_MIGRATION = (
    BACKEND / "migrations" / "dashboard" / "0002_dashboard_business_feed_indexes.sql"
)
LINEAGE_MIGRATION = (
    BACKEND / "migrations" / "publishing" / "0002_rollout_publication_region_lineage.sql"
)
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def operations(document: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (path, method)
        for path, path_item in document["paths"].items()
        for method in path_item
        if method in HTTP_METHODS
    }


def test_dashboard_fragment_matches_router_and_aggregates_without_duplicate_contracts() -> None:
    fragment = yaml.safe_load(FRAGMENT.read_text(encoding="utf-8"))
    app = FastAPI()
    app.include_router(router)
    runtime = app.openapi()

    expected = {
        ("/api/v1/projects/{project_id}/dashboard/snapshot", "get"),
        ("/api/v1/projects/{project_id}/dashboard/activity", "get"),
        ("/api/v1/projects/{project_id}/dashboard/coverage", "get"),
        ("/api/v1/projects/{project_id}/dashboard/pending-items", "get"),
        ("/api/v1/projects/{project_id}/dashboard/task-status", "get"),
    }
    assert operations(fragment) == expected
    assert operations(runtime) == expected
    aggregate = aggregate_fragments(BACKEND / "openapi")
    assert expected <= operations(aggregate)
    assert fragment["security"] == [{"bearerAuth": []}]

    for path, method in expected:
        formal = fragment["paths"][path][method]
        generated = runtime["paths"][path][method]
        assert formal["operationId"] == generated["operationId"]
        assert set(formal["responses"]) >= {"200", "401", "403", "422", "429"}
        assert aggregate["paths"][path][method]["security"] == [{"bearerAuth": []}]


def test_contract_requires_explicit_exact_scope_time_and_bounded_cursor_pages() -> None:
    fragment = yaml.safe_load(FRAGMENT.read_text(encoding="utf-8"))
    for path, path_item in fragment["paths"].items():
        parameters = path_item["get"]["parameters"]
        refs = {parameter.get("$ref") for parameter in parameters}
        assert "#/components/parameters/DashboardProjectId" in refs
        assert "#/components/parameters/DashboardRegionCode" in refs
        if not path.endswith("/task-status"):
            assert "#/components/parameters/DashboardFrom" in refs
            assert "#/components/parameters/DashboardTo" in refs
            assert "#/components/parameters/DashboardTimezone" in refs
        else:
            assert "#/components/parameters/DashboardTaskId" in refs
        if path.endswith(("/activity", "/pending-items")):
            assert "#/components/parameters/DashboardCursor" in refs
            assert "#/components/parameters/DashboardLimit" in refs

    parameters = fragment["components"]["parameters"]
    assert parameters["DashboardFrom"]["description"].startswith("Inclusive")
    assert parameters["DashboardTo"]["description"].startswith("Exclusive")
    assert parameters["DashboardTimezone"]["required"] is True
    assert parameters["DashboardLimit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 50,
    }
    assert "principal" in parameters["DashboardCursor"]["description"]


def test_contract_has_factual_event_pending_and_lineage_shapes_without_storage() -> None:
    fragment = yaml.safe_load(FRAGMENT.read_text(encoding="utf-8"))
    schemas = fragment["components"]["schemas"]
    assert schemas["DashboardSectionStatus"]["enum"] == [
        "READY",
        "EMPTY",
        "PARTIAL",
        "STALE",
        "ERROR",
        "BLOCKED",
    ]
    assert schemas["SignalStage"]["enum"] == [
        "COLLECTED",
        "RECEIVED",
        "AUTO_QC",
        "ALIGNED_30_HZ",
        "LANCE",
        "ANNOTATION",
        "REVIEW",
        "PUBLISHED",
    ]
    signal_pipeline = schemas["DashboardSignalPipelineState"]
    assert "stage_counts" in signal_pipeline["required"]
    assert signal_pipeline["properties"]["stage_counts"]["items"] == {
        "$ref": "#/components/schemas/DashboardSignalStageCount"
    }
    assert "CLEAN" not in FRAGMENT.read_text(encoding="utf-8").upper().replace(
        "CLEANING IS NOT A SEPARATE SIGNAL STAGE", ""
    )
    snapshot_sections = schemas["DashboardSnapshotSections"]
    assert set(snapshot_sections["properties"]) == {"signal_pipeline", "episodes", "work"}
    assert "storage" not in snapshot_sections["properties"]
    task_status = schemas["DashboardTaskStatusResponse"]
    assert "pipeline" in task_status["required"]
    assert task_status["properties"]["pipeline"] == {
        "$ref": "#/components/schemas/TaskPipelineStatus"
    }
    assert schemas["TaskPipelineStatus"]["properties"]["stages"]["minItems"] == 8
    assert schemas["DashboardActivityEventType"]["enum"] == [
        "UPLOAD_COMMITTED",
        "QC_COMPLETED",
        "TAG_REVIEW_DECIDED",
        "DATASET_PUBLISHED",
    ]
    assert schemas["DashboardPendingItemType"]["enum"] == [
        "UPLOAD_FAILED",
        "QC_ANOMALY",
        "TAG_REVIEW_PENDING",
        "PUBLICATION_PENDING",
    ]
    activity_properties = schemas["DashboardActivityEvent"]["properties"]
    assert {"event_id", "source_id", "deduplication_key", "occurred_at", "target"} <= set(
        activity_properties
    )
    pending_properties = schemas["DashboardPendingItem"]["properties"]
    assert {"source_id", "source_state", "severity", "opened_at", "target"} <= set(
        pending_properties
    )
    assert "published_region" in schemas["DashboardSignalPipelineState"]["properties"]
    for schema_name in (
        "DashboardPublishedRegionState",
        "DashboardSignalPipelineState",
        "DashboardActivityPage",
        "DashboardPendingItemsPage",
    ):
        assert schemas[schema_name]["additionalProperties"] is False
        assert "allOf" not in schemas[schema_name]
    for response_name in (
        "DashboardSnapshotResponse",
        "DashboardActivityResponse",
        "DashboardCoverageResponse",
        "DashboardPendingItemsResponse",
    ):
        schema = schemas[response_name]
        assert "as_of" in schema["required"]
        assert {"project_id", "region_code", "from", "to", "timezone"} <= set(schema["required"])
    source = FRAGMENT.read_text(encoding="utf-8").lower()
    assert "not a device captured/saved fact" in source
    assert "not throughput buckets" in source
    assert "historical observations are not projected as a percentage" in source
    assert "region storage is not a p01 field" in source
    assert "no sla duration is implied" in source


def test_measured_index_migration_contains_only_raw_scope_time_indexes() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "dashboard_rollout_objects_scope_committed_idx" in sql
    assert "(project_id, region_code, committed_at DESC, rollout_id DESC)" in sql
    assert "dashboard_rollouts_scope_created_idx" in sql
    assert "(project_id, region_code, created_at DESC, rollout_id DESC)" in sql
    forbidden = ("MATERIALIZED VIEW", "CACHE", "ROLLUP", "CREATE TABLE")
    executable = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert all(token not in executable.upper() for token in forbidden)


def test_business_indexes_and_lineage_migration_are_append_only_and_exact_scope() -> None:
    business = BUSINESS_MIGRATION.read_text(encoding="utf-8")
    assert "dashboard_upload_sessions_state_opened_idx" in business
    assert "dashboard_quality_pending_scope_idx" in business
    assert "dashboard_annotation_tasks_state_opened_idx" in business
    assert "MATERIALIZED VIEW" not in business.upper()

    lineage = LINEAGE_MIGRATION.read_text(encoding="utf-8")
    assert "publishing.rollout_publication_lineage" in lineage
    assert "rollout_publication_lineage_immutable" in lineage
    assert "materialize_rollout_publication_lineage" in lineage
    assert "dashboard_publication_lineage_summary" in lineage
    assert "rollout.project_id = version.project_id" in lineage
    assert "rollout.rollout_id = manifest_rollout.rollout_id" in lineage
    executable = "\n".join(
        line for line in lineage.splitlines() if not line.lstrip().startswith("--")
    ).lower()
    assert "default_region" not in executable
    assert "coalesce(rollout.region_code" not in executable

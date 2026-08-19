from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI

from hc_data_platform.annotation.router import router
from hc_data_platform.core.openapi import aggregate_fragments

BACKEND_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = BACKEND_ROOT / "openapi" / "annotation.yaml"
MIGRATION_PATH = BACKEND_ROOT / "migrations" / "annotation" / "0001_annotation.sql"
TAG_SCHEMA_MIGRATION_PATH = (
    BACKEND_ROOT / "migrations" / "annotation" / "0002_tag_schema_revisions.sql"
)
AUTOMATIC_TASK_MIGRATION_PATH = (
    BACKEND_ROOT / "migrations" / "annotation" / "0003_automatic_tasks.sql"
)
BASE_STEP_BACKFILL_MIGRATION_PATH = (
    BACKEND_ROOT / "migrations" / "annotation" / "0004_backfill_task_base_step_count.sql"
)
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _operations(document: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (path, method)
        for path, path_item in document["paths"].items()
        for method in path_item
        if method in HTTP_METHODS
    }


def _local_refs(value: object) -> set[str]:
    if isinstance(value, dict):
        refs: set[str] = {
            item
            for key, item in value.items()
            if key == "$ref" and isinstance(item, str) and item.startswith("#/")
        }
        for item in value.values():
            refs.update(_local_refs(item))
        return refs
    if isinstance(value, list):
        refs = set()
        for item in value:
            refs.update(_local_refs(item))
        return refs
    return set()


def test_openapi_fragment_matches_router_and_all_local_refs_resolve() -> None:
    fragment = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    app = FastAPI()
    app.include_router(router)
    generated = app.openapi()

    assert _operations(fragment) == _operations(generated)
    for ref in _local_refs(fragment):
        current: object = fragment
        for part in ref.removeprefix("#/").split("/"):
            assert isinstance(current, dict), ref
            assert part in current, ref
            current = current[part]

    save = fragment["components"]["schemas"]["SaveDraftRequest"]
    assert {"expected_revision", "client_mutation_id", "operations"} <= set(save["required"])
    capability = fragment["components"]["schemas"]["AutoAnnotationCapability"]
    assert capability["properties"]["enabled"]["const"] is False
    exclusion = fragment["components"]["schemas"]["ExclusionRange"]
    assert exclusion["properties"]["modality_scope"]["const"] == "ALL_MODALITIES"


def test_annotation_fragment_aggregates_without_contract_collision() -> None:
    aggregate = aggregate_fragments(BACKEND_ROOT / "openapi")
    assert "/api/v1/annotation-tasks/{task_id}/history" in aggregate["paths"]
    assert "AnnotationApprovedV1" in aggregate["components"]["schemas"]
    assert "bearerAuth" in aggregate["components"]["securitySchemes"]
    assert set(aggregate["paths"]["/api/v1/projects/{project_id}/annotation-tasks"]) == {
        "get",
        "parameters",
    }


def test_postgresql_migration_covers_all_records_cas_rls_and_append_only_history() -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    for relation in (
        "annotation_tasks",
        "annotation_revisions",
        "annotation_operations",
        "annotation_reviews",
        "annotation_mutations",
    ):
        assert f"CREATE TABLE IF NOT EXISTS annotation.{relation}" in sql
        assert f"ALTER TABLE annotation.{relation} ENABLE ROW LEVEL SECURITY" in sql
    assert "CREATE OR REPLACE VIEW annotation.annotation_drafts" in sql
    assert "CREATE OR REPLACE VIEW annotation.annotation_current" in sql
    assert "state_version bigint NOT NULL" in sql
    assert "PRIMARY KEY (task_id, client_mutation_id)" in sql
    assert "CHECK (end_step > start_step)" in sql
    assert "modality_scope = 'ALL_MODALITIES'" in sql
    assert "approved_review_id" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert sql.count("EXECUTE FUNCTION annotation.reject_immutable_change()") == 4
    assert "vlm" not in sql.lower()
    assert "auto_annotation" not in sql.lower()


def test_tag_schema_migration_covers_reviewable_versions_and_legacy_compatibility() -> None:
    sql = TAG_SCHEMA_MIGRATION_PATH.read_text(encoding="utf-8")
    for relation in (
        "tag_schema_versions",
        "annotation_submissions",
        "annotation_submission_mutations",
        "legacy_cleaning_migrations",
    ):
        assert f"CREATE TABLE IF NOT EXISTS annotation.{relation}" in sql
        assert f"ALTER TABLE annotation.{relation} ENABLE ROW LEVEL SECURITY" in sql
    assert "base_lance_version" in sql
    assert "base_step_count" in sql
    assert "tag_schema_id" in sql
    assert "current_submission_id" in sql
    assert "published Tag Schema versions are immutable" in sql
    assert "annotation_revisions_append_only" in sql
    assert "source_payload jsonb NOT NULL" in sql
    assert "hierarchy-depth" in sql
    assert "self-review" in sql
    assert "CREATE TABLE IF NOT EXISTS annotation.cleaning" not in sql


def test_automatic_task_migration_has_exact_unique_target_and_region_rls() -> None:
    sql = AUTOMATIC_TASK_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS annotation.tag_schema_bindings" in sql
    assert "CREATE TABLE IF NOT EXISTS annotation.annotation_task_triggers" in sql
    assert "annotation_tasks_automatic_target_uidx" in sql
    assert "BLOCKED_RETRYABLE" in sql
    assert "ANNOTATION_SCHEMA_BINDING_MISSING" in sql
    assert "DROP POLICY IF EXISTS annotation_task_project_scope" in sql
    assert "core.scope_matches(task.project_id, task.region_code)" in sql


def test_base_step_count_backfill_is_forward_only_and_lineage_bounded() -> None:
    historical = TAG_SCHEMA_MIGRATION_PATH.read_text(encoding="utf-8")
    forward = BASE_STEP_BACKFILL_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "SET base_step_count = lineage.step_count" not in historical
    assert "SET base_step_count = lineage.step_count" in forward
    assert "task.base_step_count IS NULL" in forward
    for identity in ("project_id", "dataset_id", "rollout_id"):
        assert f"lineage.{identity} = task.{identity}" in forward

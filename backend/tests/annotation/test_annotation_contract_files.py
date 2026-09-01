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
REVISION_THREAD_INDEX_MIGRATION_PATH = (
    BACKEND_ROOT / "migrations" / "annotation" / "0005_revision_thread_index.sql"
)
AUTO_JOB_MIGRATION_PATH = (
    BACKEND_ROOT / "migrations" / "annotation" / "0006_auto_annotation_jobs.sql"
)
RESTORE_ORIGIN_MIGRATION_PATH = (
    BACKEND_ROOT / "migrations" / "annotation" / "0007_annotation_restore_origin.sql"
)
LEGACY_CLEANING_IMPORT_MIGRATION_PATH = (
    BACKEND_ROOT / "migrations" / "annotation" / "0008_scoped_legacy_cleaning_import.sql"
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
    aggregate = aggregate_fragments(BACKEND_ROOT / "openapi")
    app = FastAPI()
    app.include_router(router)
    generated = app.openapi()

    assert _operations(fragment) == _operations(generated)
    for ref in _local_refs(fragment):
        for document in (fragment, aggregate):
            current: object = document
            for part in ref.removeprefix("#/").split("/"):
                if not isinstance(current, dict) or part not in current:
                    break
                current = current[part]
            else:
                break
        else:
            raise AssertionError(f"unresolved OpenAPI reference: {ref}")

    save = fragment["components"]["schemas"]["SaveDraftRequest"]
    assert {"expected_revision", "client_mutation_id", "operations"} <= set(save["required"])
    capability = fragment["components"]["schemas"]["AutoAnnotationCapability"]
    assert capability["properties"]["enabled"]["type"] == "boolean"
    assert {"providers", "max_jobs_per_hour", "daily_cost_limit_micros"} <= set(
        capability["properties"]
    )
    thread_page = fragment["components"]["schemas"]["AnnotationRevisionThreadPage"]
    assert {"items", "page_info", "snapshot_at"} == set(thread_page["required"])
    revision_parameters = fragment["paths"]["/api/v1/annotations/revisions"]["get"]["parameters"]
    assert "legacy_draft_id" not in {parameter["name"] for parameter in revision_parameters}
    exclusion = fragment["components"]["schemas"]["ExclusionRange"]
    assert exclusion["properties"]["modality_scope"]["const"] == "ALL_MODALITIES"


def test_annotation_fragment_aggregates_without_contract_collision() -> None:
    aggregate = aggregate_fragments(BACKEND_ROOT / "openapi")
    assert "/api/v1/annotation-tasks/{task_id}/history" in aggregate["paths"]
    assert "/api/v1/annotations/revisions" in aggregate["paths"]
    assert (
        "/api/v1/projects/{project_id}/regions/{region_code}/annotation-tasks/"
        "{task_id}/manifest-discovery"
    ) in aggregate["paths"]
    assert "AnnotationApprovedV1" in aggregate["components"]["schemas"]
    assert "bearerAuth" in aggregate["components"]["securitySchemes"]
    assert set(aggregate["paths"]["/api/v1/projects/{project_id}/annotation-tasks"]) == {"get"}


def test_revision_thread_index_migration_is_scoped_and_keyset_ordered() -> None:
    sql = REVISION_THREAD_INDEX_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "annotation_tasks_revision_thread_page_idx" in sql
    assert "project_id, region_code, updated_at DESC, task_id DESC" in sql
    assert "WHERE region_code IS NOT NULL" in sql


def test_auto_annotation_job_migration_is_durable_scoped_and_quota_indexed() -> None:
    sql = AUTO_JOB_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "annotation.auto_annotation_jobs" in sql
    assert "result_tags jsonb" in sql
    assert "estimated_cost_micros" in sql
    assert "AUTO_ANNOTATION" not in sql  # No mutation of immutable revision-origin contracts.
    assert "core.apply_project_rls('annotation.auto_annotation_jobs'::regclass)" in sql


def test_restore_origin_migration_preserves_append_only_legacy_invariants() -> None:
    sql = RESTORE_ORIGIN_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "ANNOTATION_RESTORE" in sql
    assert "annotation_revisions_origin_check" in sql
    assert "annotation_revision_legacy_audit_pair" in sql


def test_scoped_legacy_cleaning_import_is_fail_closed_and_preserves_nanosecond_edl() -> None:
    sql = LEGACY_CLEANING_IMPORT_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "hc_pending_legacy_cleaning_revisions" in sql
    assert "one exact selected-stream Lance lineage" in sql
    assert "PRESERVED_NANOSECOND_EDL" in sql
    assert "organization_id, project_id, region_code, source_draft_id, source_revision" in sql
    assert "annotation.legacy_cleaning.imported" in sql
    assert "INSERT INTO annotation.annotation_operations" not in sql
    assert "DELETE" not in sql


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

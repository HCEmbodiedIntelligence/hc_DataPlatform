from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI

from hc_data_platform.collection_tasks.router import router
from hc_data_platform.core.migrations import load_migrations
from hc_data_platform.core.openapi import aggregate_fragments

BACKEND = Path(__file__).resolve().parents[2]
OPENAPI = BACKEND / "openapi" / "collection_tasks.yaml"
MIGRATION = BACKEND / "migrations" / "collection_tasks" / "0001_collection_tasks.sql"
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def operations(document: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (path, method)
        for path, path_item in document["paths"].items()
        for method in path_item
        if method in HTTP_METHODS
    }


def local_refs(value: object) -> set[str]:
    if isinstance(value, dict):
        refs = {
            item
            for key, item in value.items()
            if key == "$ref" and isinstance(item, str) and item.startswith("#/")
        }
        for item in value.values():
            refs.update(local_refs(item))
        return refs
    if isinstance(value, list):
        refs: set[str] = set()
        for item in value:
            refs.update(local_refs(item))
        return refs
    return set()


def property_names(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(str(name).lower() for name in properties)
        for item in value.values():
            names.update(property_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(property_names(item))
    return names


def test_formal_fragment_matches_runtime_router_and_resolves_local_refs() -> None:
    fragment = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    app = FastAPI()
    app.include_router(router)
    generated = app.openapi()
    assert operations(fragment) == operations(generated)
    assert len(operations(fragment)) == 6

    for ref in local_refs(fragment):
        current: object = fragment
        for part in ref.removeprefix("#/").split("/"):
            assert isinstance(current, dict), ref
            assert part in current, ref
            current = current[part]

    aggregate = aggregate_fragments(BACKEND / "openapi")
    assert operations(fragment) <= operations(aggregate)


def test_formal_schema_contains_only_confirmed_task_fields() -> None:
    fragment = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    names = property_names(fragment["components"]["schemas"])
    forbidden = {
        "assignment",
        "assignee",
        "person",
        "pico",
        "robot",
        "device",
        "start_at",
        "end_at",
        "effective_period",
        "pause",
        "resume",
        "continue",
        "required_modalities",
        "required_topics",
        "topics",
    }
    assert not names.intersection(forbidden)

    task = fragment["components"]["schemas"]["CollectionTask"]
    assert task["additionalProperties"] is False
    assert set(task["properties"]) == {
        "schema_version",
        "collection_task_id",
        "project_id",
        "task_code",
        "name",
        "type",
        "scenario",
        "description",
        "target",
        "quality_threshold",
        "status",
    }
    assert fragment["components"]["schemas"]["CollectionTaskStatus"]["enum"] == [
        "ACTIVE",
        "CLOSED",
    ]
    assert "default" not in task["properties"]["quality_threshold"]
    target = fragment["components"]["schemas"]["CollectionTarget"]
    assert "required" not in target


def test_migration_has_scope_cas_audit_and_close_association_lock() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    table = sql.split("CREATE TABLE IF NOT EXISTS", maxsplit=1)[1].split(
        "CREATE INDEX", maxsplit=1
    )[0]
    for forbidden in (
        "assignment",
        "assignee",
        "person",
        "pico",
        "robot_id",
        "device_id",
        "start_at",
        "end_at",
        "effective_period",
        "pause",
        "resume",
        "continue",
        "required_modalities",
        "required_topics",
        "topics",
    ):
        assert forbidden not in table.lower()
    assert "CHECK (status IN ('ACTIVE', 'CLOSED'))" in sql
    assert "SELECT core.apply_project_rls" in sql
    assert "BEFORE INSERT ON ingest.rollouts" in sql
    assert "FOR SHARE OF task" in sql
    assert "COLLECTION_TASK_CLOSED" in sql
    assert "existing rollout is an idempotent retry" in sql.lower()
    assert "object.data_package_id" in (
        BACKEND / "src" / "hc_data_platform" / "collection_tasks" / "postgres.py"
    ).read_text(encoding="utf-8")

    migrations = load_migrations()
    versions = [migration.version for migration in migrations]
    assert "collection_tasks/0001_collection_tasks.sql" in versions
    assert versions.index("collection_tasks/0001_collection_tasks.sql") > versions.index(
        "quality/0002_tenant_report_identity.sql"
    )


def test_no_reopen_or_task_upload_lifecycle_operations_exist() -> None:
    fragment = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    operation_ids = {
        operation["operationId"]
        for path_item in fragment["paths"].values()
        for method, operation in path_item.items()
        if method in HTTP_METHODS
    }
    assert operation_ids == {
        "createCollectionTask",
        "listCollectionTasks",
        "getCollectionTask",
        "updateCollectionTask",
        "closeCollectionTask",
        "getCollectionTaskProgress",
    }

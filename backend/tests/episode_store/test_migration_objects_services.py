"""Read actual isolated MinIO bytes; forged, damaged and partial inventories fail."""

import copy
from uuid import uuid4

import pytest

from hc_data_platform.episode_store.migration_inventory import FORMAT, InventoryLimit
from hc_data_platform.episode_store.migration_objects import InventoryObjectVerifier
from hc_data_platform.episode_store.models import PackageInput, canonical, digest
from hc_data_platform.episode_store.postgres import PackageConflict
from hc_data_platform.lerobot_imports.service import _raw_content_digest
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_writer import identity

pytestmark = pytest.mark.integration


@pytest.fixture
def objects(services):
    _, storage = services
    ident = identity().model_copy(update={"organization_id": "migration-object-" + uuid4().hex})
    source = PackageInput(
        identity=ident,
        source_sha256="a" * 64,
        processor_version="fixture/1",
        configuration_sha256="b" * 64,
        schema_snapshot_id="schema-1",
        required_features=("observation.state",),
    )
    prefix = "migration-object-fixture/" + ident.organization_id
    files = []
    for index in range(2):
        content = {"index": index, "synthetic": True}
        relative = f"original-{index}.json"
        storage.put_json(prefix + "/source/" + relative, content, if_none_match=True)
        files.append({"path": relative, "size": len(canonical(content)), "sha256": digest(content)})
    raw = {
        "raw_source_id": "raw-fixture",
        "upload_id": "upload-fixture",
        **{
            key: getattr(ident, key)
            for key in ("organization_id", "project_id", "region_code", "dataset_id")
        },
        "collection_task_id": "task-fixture",
        "robot_id": "robot-fixture",
        "source_format": "LEROBOT_V3",
        "source_format_version": "v3.0",
        "manifest_key": prefix + "/manifest.json",
        "storage_prefix": prefix + "/source",
        "content_hash": _raw_content_digest(files),
        "file_count": 2,
        "total_bytes": sum(f["size"] for f in files),
        "raw_status": "COMMITTED",
    }
    manifest = {
        **raw,
        "schema_version": "raw-upload-manifest/v1",
        "source_prefix": raw["storage_prefix"],
        "files": files,
    }
    storage.put_json(raw["manifest_key"], manifest, if_none_match=True)
    tables = {
        key: []
        for key in (
            "ingest.raw_sources",
            "ingest.rollout_objects",
            "ingest.recording_upload_assets",
            "ingest.robot_ingest_assets",
            "aligned_media.artifacts",
            "episode_store.versions",
            "episode_store.collections",
        )
    }
    tables["ingest.raw_sources"] = [{"row_sha256": digest(raw), "document": raw}]
    # The same original referenced by recording and Raw must not be double-counted.
    asset = {
        "asset_id": "original",
        "upload_id": "recording-fixture",
        "status": "COMMITTED",
        "object_key": prefix + "/source/" + files[0]["path"],
        "expected_size": files[0]["size"],
        "expected_sha256": files[0]["sha256"],
    }
    tables["ingest.recording_upload_assets"] = [{"row_sha256": digest(asset), "document": asset}]
    payload = {
        "format": FORMAT,
        "scope": {
            key: raw[key] for key in ("organization_id", "project_id", "region_code", "dataset_id")
        },
        "tables": tables,
    }
    return source, storage, {"inventory_sha256": digest(payload), "inventory": payload}, manifest


def test_manifest_expansion_stream_hashes_and_deduplication(objects):
    source, storage, inventory, _ = objects
    with scoped(source):
        result = InventoryObjectVerifier(storage).verify(inventory)["verification"]
    assert result["status"] == "EXPANDED_OBJECTS_VERIFIED" and result["unique_objects"] == 3
    assert result["unique_declared_bytes"] == sum(r["size"] for r in result["objects"])
    assert result["reserved_read_bytes"] == result["unique_declared_bytes"]
    assert (
        len(
            next(r for r in result["objects"] if r["key"].endswith("original-0.json"))["references"]
        )
        == 2
    )
    assert all(r["verified"] for r in result["objects"])
    assert result["raw_manifests"][0]["files"] == 2
    assert not result["visible_pointers_changed"] and result["remaining_checks"]


@pytest.mark.parametrize(
    "damage", ["same_size", "larger", "missing", "manifest_scope", "file_set_hash"]
)
def test_damaged_or_forged_objects_are_not_verified(objects, damage):
    source, storage, inventory, manifest = objects
    raw = inventory["inventory"]["tables"]["ingest.raw_sources"][0]["document"]
    key = raw["storage_prefix"] + "/original-0.json"
    if damage in {"same_size", "larger"}:
        storage.put_json(
            key,
            {"index": 9 if damage == "same_size" else 999, "synthetic": True},
            if_none_match=False,
        )
    elif damage == "missing":
        # Refer to a never-created fixture key; do not delete any stored object.
        asset = inventory["inventory"]["tables"]["ingest.recording_upload_assets"][0]["document"]
        asset["object_key"] += "-missing"
        inventory["inventory_sha256"] = digest(inventory["inventory"])
    elif damage == "manifest_scope":
        storage.put_json(
            raw["manifest_key"], {**manifest, "project_id": "foreign"}, if_none_match=False
        )
    else:
        changed = copy.deepcopy(manifest)
        changed["files"][0]["sha256"] = "f" * 64
        storage.put_json(raw["manifest_key"], changed, if_none_match=False)
    with scoped(source):
        result = InventoryObjectVerifier(storage).verify(inventory)["verification"]
    assert result["status"] == "INCOMPLETE" and result["issues"]
    assert result["issues"][0]["code"] in {
        "OBJECT_HASH_CHANGED",
        "OBJECT_MISSING_OR_SIZE_CHANGED",
        "REFERENCE_OR_MANIFEST_INVALID",
    }


@pytest.mark.parametrize(
    "limit", [{"max_objects": 1}, {"max_total_bytes": 1}, {"max_manifest_bytes": 1}]
)
def test_limits_abort_without_a_partial_success(objects, limit):
    source, storage, inventory, _ = objects
    with scoped(source), pytest.raises(InventoryLimit):
        InventoryObjectVerifier(storage, **limit).verify(inventory)


@pytest.mark.parametrize("field", ["organization_id", "project_id", "region_code"])
def test_foreign_scope_or_nested_row_rejected(objects, field):
    source, storage, inventory, _ = objects
    foreign = source.model_copy(
        update={"identity": source.identity.model_copy(update={field: "foreign"})}
    )
    with scoped(foreign), pytest.raises(PermissionError):
        InventoryObjectVerifier(storage).verify(inventory)
    inventory["inventory"]["tables"]["ingest.raw_sources"][0]["document"][field] = "foreign"
    inventory["inventory_sha256"] = digest(inventory["inventory"])
    with scoped(source), pytest.raises(PermissionError):
        InventoryObjectVerifier(storage).verify(inventory)


def test_invalid_inventory_hash_rejected_before_object_access(objects):
    source, storage, inventory, _ = objects
    with scoped(source), pytest.raises(PackageConflict, match="hash/format"):
        InventoryObjectVerifier(storage).verify({**inventory, "inventory_sha256": "0" * 64})


def test_ready_media_without_primary_object_is_reported_incomplete(objects):
    source, storage, inventory, _ = objects
    media = {
        "artifact_id": "missing-media",
        "status": "READY",
        "object_manifest": [],
        "timeline_json": None,
        "media_object_key": "not-stored",
        "content_sha256": "c" * 64,
    }
    inventory["inventory"]["tables"]["aligned_media.artifacts"] = [
        {"row_sha256": digest(media), "document": media}
    ]
    inventory["inventory_sha256"] = digest(inventory["inventory"])
    with scoped(source):
        report = InventoryObjectVerifier(storage).verify(inventory)["verification"]
    assert report["status"] == "INCOMPLETE"
    assert report["issues"] == [
        {
            "kind": "aligned_media",
            "identity": "missing-media",
            "code": "MEDIA_PRIMARY_REFERENCE_MISSING",
        }
    ]

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import TEST_ENGINE, TEST_SESSIONS

from app.core.db import Base
from app.domains.storage import models

PROJECT = "prj_fx_01"
REGION = "cn-shanghai-1"
ROOT = f"/projects/{PROJECT}/storage"
SCOPE_QUERY = f"region_code={REGION}"
STORAGE_TABLES = [
    models.StorageAreaModel.__table__,
    models.StorageObjectModel.__table__,
    models.StorageReferenceModel.__table__,
    models.ObjectProtectionModel.__table__,
    models.InventorySnapshotModel.__table__,
    models.CostSnapshotModel.__table__,
    models.LifecyclePolicySetModel.__table__,
    models.LifecyclePolicyVersionModel.__table__,
    models.StorageJobModel.__table__,
    models.LifecycleSimulationModel.__table__,
    models.LifecycleExecutionModel.__table__,
    models.RestorePreflightModel.__table__,
    models.RestoreTaskModel.__table__,
    models.MultipartUploadProjectionModel.__table__,
    models.MultipartAbortPlanModel.__table__,
    models.MultipartAbortModel.__table__,
    models.StorageCommandIntentModel.__table__,
]


def headers(*capabilities: str) -> dict[str, str]:
    caps = capabilities or (
        "storage.overview.read",
        "storage.object.read",
        "storage.cost.read",
        "storage.inventory.refresh",
        "storage.lifecycle.read",
        "storage.lifecycle.manage",
        "storage.lifecycle.simulate",
        "storage.restore.read",
        "storage.restore.request",
        "storage.multipart.read",
        "storage.multipart.abort",
    )
    return {
        "Authorization": f"Bearer test:storage_admin:{','.join(caps)}",
        "X-Organization-Id": "org_fx_01",
        "X-Project-Id": PROJECT,
        "X-Region-Code": REGION,
        "X-Client-Version": "test-1",
    }


def mutation_headers(number: int, *capabilities: str, etag: str | None = None):
    result = {**headers(*capabilities), "Idempotency-Key": f"storage-idem-{number:04d}"}
    if etag is not None:
        result["If-Match"] = etag
    return result


async def prepare_storage() -> None:
    async with TEST_ENGINE.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=STORAGE_TABLES))
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=STORAGE_TABLES)
        )
    now = datetime.now(UTC)
    async with TEST_SESSIONS.begin() as session:
        session.add(
            models.StorageAreaModel(
                area_id="area_fx_archive",
                organization_id="org_fx_01",
                project_id=PROJECT,
                region_code=REGION,
                area_key="archive",
                display_name="Archive",
            )
        )
        session.add(
            models.InventorySnapshotModel(
                snapshot_id="snap_fx_current",
                organization_id="org_fx_01",
                project_id=PROJECT,
                region_code=REGION,
                current=True,
                as_of=now,
                freshness="CURRENT",
                process_status="FRESH",
                formula_version="storage-formula-v1",
                data_completeness="COMPLETE",
                watermarks={"evaluated_at": now.isoformat(), "stale_after_seconds": 1800},
                reconciliation={"status": "MATCHED"},
                overview_projection={
                    "totals": {"actual_oss_physical_bytes": {"state": "KNOWN", "value": "2097152"}},
                    "roles": [],
                    "growth": [],
                    "storage_classes": [],
                    "alerts": [],
                    "allowed_actions": ["REFRESH_INVENTORY"],
                    "partial_errors": [],
                },
                refresh_job_id=None,
            )
        )
        session.add(
            models.CostSnapshotModel(
                cost_snapshot_id="cost_fx_01",
                organization_id="org_fx_01",
                project_id=PROJECT,
                region_code=REGION,
                billing_period="2026-08",
                currency="CNY",
                source_revision="billing-rev-1",
                formula_version="cost-v3",
                as_of=now,
                projection={
                    "availability": "KNOWN",
                    "amount_minor": "300",
                    "currency": "CNY",
                    "billing_period": "2026-08",
                    "source_revision": "billing-rev-1",
                    "formula_version": "cost-v3",
                },
            )
        )
        session.add(
            models.StorageObjectModel(
                object_id="obj_fx_archive_01",
                organization_id="org_fx_01",
                project_id=PROJECT,
                region_code=REGION,
                area_id="area_fx_archive",
                provider_bucket="private-bucket",
                provider_object_key="private/object/key",
                provider_version_id="provider-v1",
                object_version="v8",
                object_role="DERIVED",
                classification_status="CLASSIFIED",
                display_key="archived-derived",
                physical_bytes=2_097_152,
                storage_class="ARCHIVE",
                media_type="application/octet-stream",
                status="AVAILABLE",
                content_sha256="a" * 64,
                created_at=now - timedelta(days=30),
                last_accessed_at=now - timedelta(days=20),
                expires_at=None,
                resource_version=8,
                etag='"rv-8"',
            )
        )
        session.add(
            models.StorageReferenceModel(
                reference_id="ref_fx_01",
                object_id="obj_fx_archive_01",
                resource_type="DATASET_VERSION",
                resource_id="version_fx_01",
                resource_version="v1",
                relation="DERIVED_OF",
                active=True,
                created_at=now,
            )
        )
        session.add(
            models.ObjectProtectionModel(
                protection_id="protection_fx_01",
                object_id="obj_fx_archive_01",
                protection_version="v1",
                current=True,
                reference_state="REFERENCED",
                reference_count=1,
                retention_state="ELAPSED",
                retain_until=None,
                legal_hold_state="NONE",
                legal_hold_count=0,
                provider_lock_state="UNLOCKED",
                evaluated_at=now,
                evidence_digest="sha256:protection",
            )
        )
        session.add(
            models.MultipartUploadProjectionModel(
                upload_id="upload_fx_01",
                organization_id="org_fx_01",
                project_id=PROJECT,
                region_code=REGION,
                upload_version="3",
                etag='"rv-3"',
                provider_bucket="private-bucket",
                provider_object_key="private/multipart/key",
                provider_etag="provider-etag-v3",
                status="INACTIVE",
                uploaded_bytes=1_048_576,
                expected_bytes=2_097_152,
                uploaded_part_count=2,
                last_activity_at=now - timedelta(days=5),
                protection_state="UNPROTECTED",
                protected_reasons=[],
                projection={"display_name": "multipart-fixture"},
            )
        )
        session.add(
            models.LifecycleExecutionModel(
                execution_id="execution_fx_01",
                organization_id="org_fx_01",
                project_id=PROJECT,
                region_code=REGION,
                status="PARTIAL",
                started_at=now - timedelta(hours=1),
                finished_at=now,
                projection={
                    "id": "execution_fx_01",
                    "status": "PARTIAL",
                    "started_at": (now - timedelta(hours=1)).isoformat(),
                    "finished_at": now.isoformat(),
                    "counts": {"total": "1", "succeeded": "0", "protected": "1"},
                },
            )
        )


def policy_input() -> dict:
    return {
        "name": "Archive derived objects",
        "description": "Transition old derived objects.",
        "target": {
            "target_kind": "STORAGE_OBJECTS",
            "scope_id": "area_fx_archive",
            "object_role": "DERIVED",
            "schedule_window_id": "window_fx_01",
        },
        "age_basis": "LAST_ACCESSED_AT",
        "actions": [{"type": "TRANSITION", "after_days": 30, "target_class": "ARCHIVE"}],
        "exclusion_scope_ids": [],
        "schedule_window_id": "window_fx_01",
    }


@pytest.mark.asyncio
async def test_all_22_storage_operations_happy_path(client) -> None:
    await prepare_storage()

    overview = await client.get(f"{ROOT}/overview?{SCOPE_QUERY}", headers=headers())
    assert overview.status_code == 200, overview.text
    objects = await client.get(f"{ROOT}/objects?{SCOPE_QUERY}", headers=headers())
    assert objects.status_code == 200 and len(objects.json()["items"]) == 1
    assert "provider_object_key" not in objects.text
    detail = await client.get(
        f"{ROOT}/objects/obj_fx_archive_01?{SCOPE_QUERY}&snapshot_id=snap_fx_current",
        headers=headers(),
    )
    assert detail.status_code == 200 and "private/object/key" not in detail.text
    multipart = await client.get(f"{ROOT}/multipart?{SCOPE_QUERY}", headers=headers())
    assert multipart.status_code == 200 and "private/multipart/key" not in multipart.text
    cost = await client.get(
        f"{ROOT}/cost-breakdown?{SCOPE_QUERY}&billing_period=2026-08&currency=CNY",
        headers=headers(),
    )
    assert cost.status_code == 200
    inventory = await client.post(
        f"{ROOT}/inventory-jobs?{SCOPE_QUERY}",
        headers=mutation_headers(1),
        json={"expected_snapshot_id": "snap_fx_current", "reason": "USER_REFRESH"},
    )
    assert inventory.status_code == 202, inventory.text

    lifecycle_page = await client.get(f"{ROOT}/lifecycle-page?{SCOPE_QUERY}", headers=headers())
    assert lifecycle_page.status_code == 200
    assert lifecycle_page.json()["data"]["policy_set_etag"] == '"rv-0"'
    policies = await client.get(f"{ROOT}/lifecycle-policies?{SCOPE_QUERY}", headers=headers())
    assert policies.status_code == 200

    created = await client.post(
        f"{ROOT}/lifecycle-policies?{SCOPE_QUERY}",
        headers=mutation_headers(2, etag='"rv-0"'),
        json={"policy": policy_input(), "expected_policy_set_version": "0"},
    )
    assert created.status_code == 201, created.text
    created_body = created.json()
    policy_id = created_body["policy"]["id"]
    assert (
        await client.get(f"{ROOT}/lifecycle-policies/{policy_id}?{SCOPE_QUERY}", headers=headers())
    ).status_code == 200

    updated = await client.patch(
        f"{ROOT}/lifecycle-policies/{policy_id}?{SCOPE_QUERY}",
        headers=mutation_headers(3, etag=created_body["policy"]["etag"]),
        json={
            "policy": {**policy_input(), "name": "Archive derived objects v2"},
            "expected_policy_version": created_body["policy"]["version"],
            "expected_policy_set_version": created_body["policy_set_version"],
        },
    )
    assert updated.status_code == 200, updated.text
    updated_body = updated.json()

    simulation = await client.post(
        f"{ROOT}/lifecycle-simulations?{SCOPE_QUERY}",
        headers=mutation_headers(4),
        json={
            "mode": "SAVED_POLICIES",
            "selection": {"kind": "POLICY_ID", "policy_id": policy_id},
            "policy_set_version": updated_body["policy_set_version"],
            "policy_versions": [
                {"policy_id": policy_id, "version": updated_body["policy"]["version"]}
            ],
            "snapshot_id": "snap_fx_current",
            "input_hash": updated_body["policy"]["simulation_input_hash"],
        },
    )
    assert simulation.status_code == 202, simulation.text
    simulation_body = simulation.json()["simulation"]
    assert (
        await client.get(
            f"{ROOT}/lifecycle-simulations/{simulation_body['id']}?{SCOPE_QUERY}",
            headers=headers(),
        )
    ).status_code == 200

    enabled = await client.post(
        f"{ROOT}/lifecycle-policies/{policy_id}:enable?{SCOPE_QUERY}",
        headers=mutation_headers(5, etag=updated_body["policy"]["etag"]),
        json={
            "policy_version": updated_body["policy"]["version"],
            "simulation_id": simulation_body["id"],
            "input_hash": simulation_body["input_hash"],
            "snapshot_id": simulation_body["snapshot_id"],
            "policy_set_version": simulation_body["policy_set_version"],
            "policy_versions": simulation_body["policy_versions"],
            "confirmation": {
                "impact_digest": simulation_body["impact_digest"],
                "acknowledged_risks": ["TRANSITION_COST", "RESTORE_DELAY"],
            },
        },
    )
    assert enabled.status_code == 200, enabled.text
    enabled_body = enabled.json()
    paused = await client.post(
        f"{ROOT}/lifecycle-policies/{policy_id}:pause?{SCOPE_QUERY}",
        headers=mutation_headers(6, etag=enabled_body["policy"]["etag"]),
        json={
            "policy_version": enabled_body["policy"]["version"],
            "expected_policy_set_version": enabled_body["policy_set_version"],
            "reason_code": "OPERATOR_REQUEST",
            "reason_text": "fixture pause",
        },
    )
    assert paused.status_code == 200, paused.text

    execution_page = await client.get(
        f"{ROOT}/lifecycle-executions?{SCOPE_QUERY}", headers=headers()
    )
    assert execution_page.status_code == 200 and len(execution_page.json()["items"]) == 1
    execution = await client.get(
        f"{ROOT}/lifecycle-executions/execution_fx_01?{SCOPE_QUERY}", headers=headers()
    )
    assert execution.status_code == 200

    restores = await client.get(f"{ROOT}/restore-tasks?{SCOPE_QUERY}", headers=headers())
    assert restores.status_code == 200
    restore_preflight = await client.post(
        f"{ROOT}/restore-tasks?{SCOPE_QUERY}",
        headers=mutation_headers(7),
        json={
            "mode": "PREFLIGHT",
            "target": {"resource_type": "STORAGE_OBJECT", "resource_id": "obj_fx_archive_01"},
        },
    )
    assert restore_preflight.status_code == 200, restore_preflight.text
    quote = restore_preflight.json()
    restore_commit = await client.post(
        f"{ROOT}/restore-tasks?{SCOPE_QUERY}",
        headers=mutation_headers(8),
        json={
            "mode": "COMMIT",
            "preflight_token": quote["preflight_token"],
            "quote_id": quote["quote_id"],
            "restore_method": "STANDARD",
            "expected_target_version": "v8",
            "confirmation": {"cost_and_expiry_acknowledged": True},
        },
    )
    assert restore_commit.status_code == 202, restore_commit.text

    lifecycle_multipart = await client.get(
        f"{ROOT}/multipart-lifecycle?{SCOPE_QUERY}", headers=headers()
    )
    assert lifecycle_multipart.status_code == 200
    upload = lifecycle_multipart.json()["items"][0]
    plan = await client.post(
        f"{ROOT}/multipart/upload_fx_01:plan-abort?{SCOPE_QUERY}",
        headers=mutation_headers(9, etag=upload["etag"]),
        json={
            "upload_version": upload["upload_version"],
            "observed_last_activity_at": upload["last_activity_at"],
            "confirmation_context": "LIFECYCLE_GOVERNANCE",
        },
    )
    assert plan.status_code == 200, plan.text
    plan_body = plan.json()
    aborted = await client.post(
        f"{ROOT}/multipart/upload_fx_01:abort?{SCOPE_QUERY}",
        headers=mutation_headers(10, etag=upload["etag"]),
        json={
            "plan_id": plan_body["plan_id"],
            "confirmation_token": plan_body["confirmation_token"],
            "upload_version": upload["upload_version"],
            "observed_last_activity_at": upload["last_activity_at"],
            "confirmation": {"uploaded_parts_will_be_unrecoverable": True},
        },
    )
    assert aborted.status_code == 202, aborted.text


@pytest.mark.asyncio
async def test_storage_fail_closed_errors_and_idempotency(client) -> None:
    await prepare_storage()
    denied = await client.get(
        f"{ROOT}/overview?{SCOPE_QUERY}", headers=headers("storage.object.read")
    )
    assert denied.status_code == 403
    missing = await client.get(
        f"{ROOT}/objects/missing?{SCOPE_QUERY}&snapshot_id=snap_fx_current", headers=headers()
    )
    assert missing.status_code == 404
    stale = await client.post(
        f"{ROOT}/inventory-jobs?{SCOPE_QUERY}",
        headers=mutation_headers(100),
        json={"expected_snapshot_id": "stale", "reason": "USER_REFRESH"},
    )
    assert stale.status_code == 409
    invalid = await client.post(
        f"{ROOT}/lifecycle-policies?{SCOPE_QUERY}",
        headers=mutation_headers(101, etag='"rv-0"'),
        json={
            "policy": {
                **policy_input(),
                "target": {
                    "target_kind": "INCOMPLETE_MULTIPART",
                    "scope_id": "area_fx_archive",
                    "object_role": None,
                    "schedule_window_id": "window_fx_01",
                },
            },
            "expected_policy_set_version": "0",
        },
    )
    assert invalid.status_code == 422


def test_storage_router_matches_frozen_operation_set() -> None:
    from app.domains.storage.router import router

    operation_ids = {route.operation_id for route in router.routes}
    assert len(operation_ids) == 22
    assert "getStorageOverview" in operation_ids
    assert "abortMultipart" in operation_ids
    assert all("download" not in route.path.lower() for route in router.routes)
    assert all("lifecycle:execute" not in route.path.lower() for route in router.routes)

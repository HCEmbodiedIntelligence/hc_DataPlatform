from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import TEST_ENGINE, TEST_SESSIONS
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from app.core.db import Base
from app.domains.access import models
from app.domains.access.schemas import AuditEventInput
from app.domains.access.service import AuditService

PROJECT = "prj_fx_01"
ROOT = f"/projects/{PROJECT}"
ACCESS_TABLES = [
    models.CapabilityCatalogModel.__table__,
    models.RoleVersionModel.__table__,
    models.MemberModel.__table__,
    models.RoleAssignmentModel.__table__,
    models.ScopeGrantModel.__table__,
    models.AuthorizationSnapshotModel.__table__,
    models.InvitationModel.__table__,
    models.AccessPreflightModel.__table__,
    models.AuditOutboxModel.__table__,
    models.AuditEventModel.__table__,
    models.AuditReadProjectionModel.__table__,
    models.AuditExportJobModel.__table__,
    models.AuditRetentionPolicyModel.__table__,
    models.AuditLegalHoldModel.__table__,
    models.AuditIntegrityCheckpointModel.__table__,
]


def idem(number: int) -> str:
    return f"access-idempotency-{number:04d}"


def headers(*capabilities: str) -> dict[str, str]:
    caps = capabilities or ("access.read", "access.manage", "audit.read", "audit.export")
    return {
        "Authorization": f"Bearer test:admin_fx:{','.join(caps)}",
        "X-Organization-Id": "org_fx_01",
        "X-Project-Id": PROJECT,
        "X-Region-Code": "cn-shanghai",
        "X-Request-ID": "request-access-fixture",
        "X-Client-Version": "test-1",
    }


async def prepare_database() -> None:
    async with TEST_ENGINE.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=ACCESS_TABLES))
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=ACCESS_TABLES))
    now = datetime.now(UTC)
    async with TEST_SESSIONS.begin() as db_session:
        db_session.add(
            models.MemberModel(
                member_id="membership_fx_admin",
                organization_id="org_fx_01",
                project_id=PROJECT,
                principal_id="admin_fx",
                principal_snapshot={
                    "principal_id": "admin_fx",
                    "type": "HUMAN",
                    "display_name": "Admin Fixture",
                    "secondary_display": None,
                    "identity_status": "ACTIVE",
                },
                status="ACTIVE",
                role_id="PROJECT_ADMIN",
                role_version="roles-v2-conditional",
                joined_at=now - timedelta(days=1),
                last_active_at=now,
                resource_version=1,
                etag='"rv-1"',
            )
        )
        db_session.add(
            models.RoleAssignmentModel(
                role_assignment_id="assignment_fx_admin",
                member_id="membership_fx_admin",
                subject={
                    "type": "PROJECT_MEMBERSHIP",
                    "principal_id": "admin_fx",
                    "project_membership_id": "membership_fx_admin",
                    "organization_id": "org_fx_01",
                    "project_id": PROJECT,
                },
                role_id="PROJECT_ADMIN",
                role_version="roles-v2-conditional",
                scope_key=f"project:org_fx_01:{PROJECT}",
                scope={"type": "PROJECT", "organization_id": "org_fx_01", "project_id": PROJECT},
                inherit=True,
                valid_from=now - timedelta(days=1),
                valid_to=None,
                status="ACTIVE",
                etag='"rv-1"',
            )
        )
        db_session.add(
            models.AuditRetentionPolicyModel(
                policy_version="audit-retention-v1-conditional",
                classes=[{"class": "STANDARD", "automatic_purge": False}],
                writes_enabled=False,
                effective_at=now,
            )
        )
        event_input = AuditEventInput.model_validate(
            {
                "schema_version": 1,
                "event_name": "access.invitation.created",
                "occurred_at": now,
                "actor": {
                    "type": "HUMAN",
                    "principal_id": "admin_fx",
                    "display_name": "Admin Fixture",
                    "role_ids": ["PROJECT_ADMIN"],
                },
                "scope": {
                    "organization_id": "org_fx_01",
                    "project_id": PROJECT,
                    "region_code": "cn-shanghai",
                },
                "resource": {
                    "type": "access.invitation",
                    "id": "invitation_seed",
                    "display_name": "seed@example.test",
                    "parent_refs": [],
                },
                "request": {
                    "request_id": "request_seed",
                    "job_id": None,
                    "client_type": "TEST",
                    "ip_address": None,
                    "device_summary": None,
                },
                "outcome": {"status": "SUCCEEDED", "reason_code": None, "http_status": 201},
                "risk_signal_codes": [],
                "change": None,
                "relationships": {
                    "parent_event_id": None,
                    "related_event_ids": [],
                    "resource_refs": [],
                },
                "producer": {
                    "service": "access-fixture",
                    "producer_event_id": "producer_event_seed",
                    "contract_version": "1",
                },
            }
        )
        await AuditService(db_session).append_event(
            event_input, aggregate_type="access.invitation", aggregate_id="invitation_seed"
        )
        await db_session.flush()


async def post(client, url: str, number: int, json: dict, *, extra: dict | None = None):
    return await client.post(
        url,
        headers={**headers(), "Idempotency-Key": idem(number), **(extra or {})},
        json=json,
    )


@pytest.mark.asyncio
async def test_all_26_operations_happy_path(client) -> None:
    await prepare_database()
    policy_etag = '"rv-access-policy-v1-conditional"'

    bootstrap = await client.get(f"{ROOT}/access/bootstrap", headers=headers())
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.headers["etag"] == policy_etag
    assert (
        await client.get(
            f"{ROOT}/access/bootstrap",
            headers={**headers(), "If-None-Match": bootstrap.headers["etag"]},
        )
    ).status_code == 304
    assert (await client.get(f"{ROOT}/members", headers=headers())).status_code == 200
    assert (
        await client.get(f"{ROOT}/members/membership_fx_admin", headers=headers())
    ).status_code == 200
    assert (
        await client.get(f"{ROOT}/members/membership_fx_admin/effective-access", headers=headers())
    ).status_code == 200
    roles = await client.get(f"{ROOT}/roles", headers=headers())
    assert roles.status_code == 200 and len(roles.json()["items"]) == 3
    catalog = await client.get("/authorization/capabilities", headers=headers())
    assert catalog.status_code == 200 and len(catalog.json()["data"]["capability_keys"]) == 76
    assert (await client.get(f"{ROOT}/role-assignments", headers=headers())).status_code == 200
    assert (await client.get(f"{ROOT}/access-grants", headers=headers())).status_code == 200
    assert (await client.get(f"{ROOT}/invitations", headers=headers())).status_code == 200

    invitation_preflight_body = {
        "operation": {
            "type": "CREATE_INVITATION",
            "target": {"type": "EMAIL", "value": "user@example.test"},
            "role_id": "PROJECT_DEVELOPER",
            "role_version": "roles-v2-conditional",
            "intended_scope": {
                "type": "PROJECT",
                "organization_id": "org_fx_01",
                "project_id": PROJECT,
            },
            "invitation_expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
        "project_policy_revision": "access-policy-v1-conditional",
        "project_policy_etag": policy_etag,
        "reason": "onboard developer",
    }
    preflight = await post(
        client,
        f"{ROOT}/invitations:preflight",
        1,
        invitation_preflight_body,
        extra={"If-Match": policy_etag},
    )
    assert preflight.status_code == 200, preflight.text
    created = await post(
        client,
        f"{ROOT}/invitations",
        2,
        {"preflight_token": preflight.json()["data"]["preflight_token"]},
        extra={"If-Match": policy_etag},
    )
    assert created.status_code == 201, created.text
    invitation = created.json()["data"]["invitation"]
    invitation_id = invitation["invitation_id"]
    fetched = await client.get(f"{ROOT}/invitations/{invitation_id}", headers=headers())
    assert fetched.status_code == 200
    assert (
        await client.get(
            f"{ROOT}/invitations/{invitation_id}",
            headers={**headers(), "If-None-Match": fetched.headers["etag"]},
        )
    ).status_code == 304

    resend_preflight = await post(
        client,
        f"{ROOT}/invitations:preflight",
        3,
        {
            "operation": {
                "type": "RESEND_INVITATION",
                "invitation_id": invitation_id,
                "invitation_etag": invitation["etag"],
            },
            "project_policy_revision": "access-policy-v1-conditional",
            "project_policy_etag": policy_etag,
            "reason": "replacement",
        },
        extra={"If-Match": policy_etag},
    )
    assert resend_preflight.status_code == 200, resend_preflight.text
    resent = await post(
        client,
        f"{ROOT}/invitations/{invitation_id}:resend",
        4,
        {"preflight_token": resend_preflight.json()["data"]["preflight_token"]},
        extra={"If-Match": invitation["etag"]},
    )
    assert resent.status_code == 201, resent.text
    replacement = resent.json()["data"]["invitation"]

    revoke_preflight = await post(
        client,
        f"{ROOT}/invitations:preflight",
        5,
        {
            "operation": {
                "type": "REVOKE_INVITATION",
                "invitation_id": replacement["invitation_id"],
                "invitation_etag": replacement["etag"],
            },
            "project_policy_revision": "access-policy-v1-conditional",
            "project_policy_etag": policy_etag,
            "reason": "cancel onboarding",
        },
        extra={"If-Match": policy_etag},
    )
    revoked = await post(
        client,
        f"{ROOT}/invitations/{replacement['invitation_id']}:revoke",
        6,
        {"preflight_token": revoke_preflight.json()["data"]["preflight_token"]},
        extra={"If-Match": replacement["etag"]},
    )
    assert revoked.status_code == 200, revoked.text

    access_preflight = await post(
        client,
        f"{ROOT}/access-changes:preflight",
        7,
        {
            "operations": [
                {
                    "type": "ADD_SCOPE_GRANT",
                    "membership_id": "membership_fx_admin",
                    "membership_etag": '"rv-1"',
                    "current_role_assignment_id": "assignment_fx_admin",
                    "current_role_assignment_etag": '"rv-1"',
                    "role_version": "roles-v2-conditional",
                    "grant": {
                        "scope": {
                            "type": "PROJECT",
                            "organization_id": "org_fx_01",
                            "project_id": PROJECT,
                        },
                        "effect": "DENY",
                        "capability_keys": ["storage.lifecycle.execute"],
                        "inherit": True,
                    },
                }
            ],
            "project_policy_revision": "access-policy-v1-conditional",
            "project_policy_etag": policy_etag,
            "reason": "temporarily narrow lifecycle access",
        },
        extra={"If-Match": policy_etag},
    )
    assert access_preflight.status_code == 200, access_preflight.text
    committed = await post(
        client,
        f"{ROOT}/access-changes",
        8,
        {"preflight_token": access_preflight.json()["data"]["preflight_token"]},
        extra={"If-Match": policy_etag},
    )
    assert committed.status_code == 200, committed.text

    audit_bootstrap = await client.get(f"{ROOT}/audit/bootstrap", headers=headers())
    assert audit_bootstrap.status_code == 200, audit_bootstrap.text
    assert (await client.get(f"{ROOT}/audit/events/facets", headers=headers())).status_code == 200
    event_page = await client.get(f"{ROOT}/audit/events", headers=headers())
    assert event_page.status_code == 200, event_page.text
    event_id = event_page.json()["items"][0]["event_id"]
    assert (
        await client.get(f"{ROOT}/audit/events/{event_id}", headers=headers())
    ).status_code == 200
    assert (
        await client.get(f"{ROOT}/audit/events/{event_id}/related", headers=headers())
    ).status_code == 200
    assert (
        await client.get(f"{ROOT}/audit/retention-policy", headers=headers())
    ).status_code == 200

    filters = {
        "occurred_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "occurred_to": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "actor_id": [],
        "event_name": [],
        "resource_type": [],
        "resource_id": None,
        "outcome": [],
        "risk_level": [],
        "request_id": None,
        "region_code": ["cn-shanghai"],
        "sort": "occurred_at:desc,event_id:desc",
    }
    export_preflight = await post(
        client,
        f"{ROOT}/audit-exports:preflight",
        9,
        {"filters": filters, "format": "JSONL", "field_profile": "STANDARD", "reason": "review"},
    )
    assert export_preflight.status_code == 200, export_preflight.text
    export_data = export_preflight.json()["data"]
    export_created = await post(
        client,
        f"{ROOT}/audit-exports",
        10,
        {
            "normalized_filters": export_data["normalized_filters"],
            "snapshot_at": export_data["snapshot_at"],
            "format": "JSONL",
            "field_profile": "STANDARD",
            "reason": "review",
            "preflight_token": export_data["preflight_token"],
        },
    )
    assert export_created.status_code == 202, export_created.text
    export_id = export_created.json()["audit_export"]["export_id"]
    assert (
        await client.get(f"{ROOT}/audit-exports/{export_id}", headers=headers())
    ).status_code == 200
    async with TEST_SESSIONS.begin() as db_session:
        result = await db_session.scalar(
            select(models.AuditExportJobModel).where(
                models.AuditExportJobModel.export_id == export_id
            )
        )
        assert result is not None
        result.status = "SUCCEEDED"
        result.artifact_ref = "private://audit-export/artifact"
        result.artifact_expires_at = datetime.now(UTC) + timedelta(minutes=10)
        result.artifact_sha256 = "a" * 64
    authorized = await client.post(
        f"{ROOT}/audit-exports/{export_id}:authorize-download",
        headers={**headers(), "Idempotency-Key": idem(11)},
    )
    assert authorized.status_code == 200, authorized.text
    assert "signed_url" not in authorized.text


@pytest.mark.asyncio
async def test_fail_closed_errors_and_cursor_validation(client) -> None:
    await prepare_database()
    denied = await client.get(f"{ROOT}/members", headers=headers("audit.read"))
    assert denied.status_code == 403
    missing = await client.get(f"{ROOT}/members/missing", headers=headers())
    assert missing.status_code == 404
    cursor_conflict = await client.get(f"{ROOT}/members?after=abc&before=def", headers=headers())
    assert cursor_conflict.status_code == 422
    overflow = await post(
        client,
        f"{ROOT}/access-changes:preflight",
        20,
        {
            "operations": [
                {
                    "type": "ADD_SCOPE_GRANT",
                    "membership_id": "membership_fx_admin",
                    "membership_etag": '"rv-1"',
                    "current_role_assignment_id": "assignment_fx_admin",
                    "current_role_assignment_etag": '"rv-1"',
                    "role_version": "roles-v2-conditional",
                    "grant": {
                        "scope": {
                            "type": "PROJECT",
                            "organization_id": "org_fx_01",
                            "project_id": PROJECT,
                        },
                        "effect": "DENY",
                        "capability_keys": ["dataset.delete"],
                        "inherit": True,
                    },
                }
            ],
            "project_policy_revision": "access-policy-v1-conditional",
            "project_policy_etag": '"rv-access-policy-v1-conditional"',
            "reason": "invalid reserved capability",
        },
        extra={"If-Match": '"rv-access-policy-v1-conditional"'},
    )
    # Pydantic rejects reserved/unknown capability at the request boundary or the service does.
    assert overflow.status_code == 422


@pytest.mark.asyncio
async def test_audit_event_id_is_unique_in_storage() -> None:
    await prepare_database()
    columns = [column.name for column in models.AuditEventModel.__table__.columns]
    duplicate = insert(models.AuditEventModel).from_select(
        columns,
        select(*(getattr(models.AuditEventModel, name) for name in columns)).where(
            models.AuditEventModel.event_id.is_not(None)
        ),
    )
    with pytest.raises(IntegrityError):
        async with TEST_SESSIONS.begin() as session:
            await session.execute(duplicate)


@pytest.mark.asyncio
async def test_all_26_access_operations_fail_closed_without_capability(client) -> None:
    await prepare_database()
    cases = (
        ("GET", f"{ROOT}/access/bootstrap"),
        ("GET", f"{ROOT}/members"),
        ("GET", f"{ROOT}/members/missing"),
        ("GET", f"{ROOT}/members/missing/effective-access"),
        ("GET", f"{ROOT}/roles"),
        ("GET", "/authorization/capabilities"),
        ("GET", f"{ROOT}/role-assignments"),
        ("GET", f"{ROOT}/access-grants"),
        ("GET", f"{ROOT}/invitations"),
        ("POST", f"{ROOT}/invitations:preflight"),
        ("POST", f"{ROOT}/invitations"),
        ("GET", f"{ROOT}/invitations/missing"),
        ("POST", f"{ROOT}/invitations/missing:resend"),
        ("POST", f"{ROOT}/invitations/missing:revoke"),
        ("POST", f"{ROOT}/access-changes:preflight"),
        ("POST", f"{ROOT}/access-changes"),
        ("GET", f"{ROOT}/audit/bootstrap"),
        ("GET", f"{ROOT}/audit/events/facets"),
        ("GET", f"{ROOT}/audit/events"),
        ("GET", f"{ROOT}/audit/events/missing"),
        ("GET", f"{ROOT}/audit/events/missing/related"),
        ("GET", f"{ROOT}/audit/retention-policy"),
        ("POST", f"{ROOT}/audit-exports:preflight"),
        ("POST", f"{ROOT}/audit-exports"),
        ("GET", f"{ROOT}/audit-exports/missing"),
        ("POST", f"{ROOT}/audit-exports/missing:authorize-download"),
    )
    denied_headers = {
        **headers(),
        "Authorization": "Bearer test:denied_actor:",
        "Idempotency-Key": "access-denied-idempotency",
        "If-Match": '"rv-access-policy-v1-conditional"',
    }
    for method, path in cases:
        response = await client.request(
            method,
            path,
            headers=denied_headers,
            json={} if method == "POST" else None,
        )
        assert response.status_code == 403, (method, path, response.text)


@pytest.mark.asyncio
async def test_access_resource_404_conflict_and_strict_422_matrix(client) -> None:
    await prepare_database()
    for path in (
        f"{ROOT}/members/missing",
        f"{ROOT}/members/missing/effective-access",
        f"{ROOT}/invitations/missing",
        f"{ROOT}/audit/events/missing",
        f"{ROOT}/audit/events/missing/related",
        f"{ROOT}/audit-exports/missing",
    ):
        response = await client.get(path, headers=headers())
        assert response.status_code == 404, (path, response.text)

    queued_at = datetime.now(UTC)
    async with TEST_SESSIONS.begin() as session:
        session.add(
            models.AuditExportJobModel(
                export_id="export_not_ready_fx",
                scope_key=(
                    f"organization:org_fx_01:project:{PROJECT}:region:cn-shanghai"
                ),
                scope={
                    "organization_id": "org_fx_01",
                    "project_id": PROJECT,
                    "region_code": "cn-shanghai",
                },
                requester_principal_id="admin_fx",
                snapshot_at=queued_at,
                normalized_filters={},
                normalized_filter_hash="a" * 64,
                format="JSONL",
                field_profile="STANDARD",
                job_id="job_export_not_ready_fx",
                status="QUEUED",
                succeeded_count=0,
                failed_count=0,
                omitted_count=0,
                artifact_ref=None,
                artifact_expires_at=None,
                artifact_sha256=None,
                create_authorization_policy_version="access-policy-v1-conditional",
                created_at=queued_at,
            )
        )
    conflict = await client.post(
        f"{ROOT}/audit-exports/export_not_ready_fx:authorize-download",
        headers={**headers(), "Idempotency-Key": idem(90)},
    )
    assert conflict.status_code == 409

    strict = await client.post(
        f"{ROOT}/invitations:preflight",
        headers={
            **headers(),
            "Idempotency-Key": idem(91),
            "If-Match": '"rv-access-policy-v1-conditional"',
        },
        json={"unknown": "field"},
    )
    assert strict.status_code == 422

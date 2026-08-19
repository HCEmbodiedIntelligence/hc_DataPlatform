from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.auth import AuthContext, Role
from hc_data_platform.security.scope import ScopeGuard
from hc_data_platform.storage.models import (
    BusinessCapacityCategory,
    CapacityInventoryFact,
    InventoryDisposition,
    ObjectRole,
)
from hc_data_platform.storage.repository import InMemoryStorageRepository
from hc_data_platform.storage.service import StorageGovernanceService


class ProjectAdminVerifier:
    def verify(self, token: str) -> AuthContext:
        project_id = {"external-admin-a": "project-a", "external-admin-b": "project-b"}.get(token)
        if project_id is None:
            raise AssertionError("unexpected external test token")
        return AuthContext(
            subject_id=token,
            project_ids=frozenset({project_id}),
            region_codes=frozenset(),
            roles=frozenset({Role.ADMIN.value}),
            scope_pairs=frozenset({(project_id, None)}),
        )


def _headers(token: str, idempotency_key: str | None = None) -> dict[str, str]:
    result = {"Authorization": f"Bearer {token}"}
    if idempotency_key is not None:
        result["Idempotency-Key"] = idempotency_key
    return result


def _register(client: TestClient, username: str) -> str:
    password = f"{username}-be23-password"
    registered = client.post(
        "/api/v1/auth/registrations",
        json={"username": username, "password": password},
    )
    assert registered.status_code == 201
    session = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": password},
    )
    assert session.status_code == 201
    return str(session.json()["access_token"])


def _request_membership(client: TestClient, token: str, suffix: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/projects/project-a/membership-requests",
        json={"reason": "join"},
        headers=_headers(token, f"membership-{suffix}"),
    )
    assert response.status_code == 201
    return response.json()


def _approve_membership(client: TestClient, request_id: str, suffix: str) -> None:
    response = client.post(
        f"/api/v1/projects/project-a/membership-requests/{request_id}:approve",
        json={"reason": "approved"},
        headers=_headers("external-admin-a", f"approve-membership-{suffix}"),
    )
    assert response.status_code == 200


def _grant_capability(
    client: TestClient,
    *,
    token: str,
    capability: str,
    suffix: str,
) -> dict[str, Any]:
    requested = client.post(
        "/api/v1/projects/project-a/capability-requests",
        json={"capability_keys": [capability], "reason": "least privilege"},
        headers=_headers(token, f"capability-{suffix}"),
    )
    assert requested.status_code == 201
    approved = client.post(
        f"/api/v1/projects/project-a/capability-requests/{requested.json()['request_id']}:approve",
        json={"reason": "approved"},
        headers=_headers("external-admin-a", f"approve-capability-{suffix}"),
    )
    assert approved.status_code == 200
    return approved.json()


def test_same_project_capability_pair_cross_project_idor_and_old_session_revocation() -> None:
    repository = InMemoryAccessRepository()
    service = AccessService(repository)
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None),
        jwt_verifier=ProjectAdminVerifier(),  # type: ignore[arg-type]
        access_service=service,
    )

    with TestClient(app) as client:
        delegate_token = _register(client, "be23-delegate")
        reader_token = _register(client, "be23-reader")
        target_one_token = _register(client, "be23-target-one")
        target_two_token = _register(client, "be23-target-two")

        delegate_membership = _request_membership(client, delegate_token, "delegate")
        reader_membership = _request_membership(client, reader_token, "reader")
        target_one = _request_membership(client, target_one_token, "target-one")
        target_two = _request_membership(client, target_two_token, "target-two")
        _approve_membership(client, delegate_membership["request_id"], "delegate")
        _approve_membership(client, reader_membership["request_id"], "reader")

        delegate_grant = _grant_capability(
            client,
            token=delegate_token,
            capability="project.access.manage",
            suffix="delegate",
        )
        _grant_capability(
            client,
            token=reader_token,
            capability="datasets.read",
            suffix="reader",
        )

        # Same project and same resource, but a read-only capability cannot approve access.
        reader_denied = client.post(
            f"/api/v1/projects/project-a/membership-requests/{target_one['request_id']}:approve",
            json={"reason": "must be denied"},
            headers=_headers(reader_token, "reader-must-not-approve"),
        )
        assert reader_denied.status_code == 403
        assert reader_denied.json()["code"] == "ACCESS_MANAGEMENT_REQUIRED"

        delegate_allowed = client.post(
            f"/api/v1/projects/project-a/membership-requests/{target_one['request_id']}:approve",
            json={"reason": "delegated approval"},
            headers=_headers(delegate_token, "delegate-approves"),
        )
        assert delegate_allowed.status_code == 200

        # Reusing the exact resource ID under another project is hidden, even from that
        # project's administrator.
        cross_project = client.get(
            "/api/v1/projects/project-b/membership-requests/" + target_two["request_id"],
            headers=_headers("external-admin-b"),
        )
        assert cross_project.status_code == 404
        assert cross_project.json()["code"] == "MEMBERSHIP_REQUEST_NOT_FOUND"

        before = client.get(
            "/api/v1/auth/session/bootstrap", headers=_headers(delegate_token)
        ).json()
        assert before["available_scopes"][0]["capabilities"] == ["project.access.manage"]

        revoked = client.post(
            f"/api/v1/projects/project-a/capability-requests/{delegate_grant['request_id']}:revoke",
            json={"reason": "remove delegated approval"},
            headers=_headers("external-admin-a", "revoke-delegate"),
        )
        assert revoked.status_code == 200
        after = client.get(
            "/api/v1/auth/session/bootstrap", headers=_headers(delegate_token)
        ).json()
        assert after["available_scopes"][0]["capabilities"] == []
        assert after["capability_revision"] > before["capability_revision"]

        # The already-issued session is resolved on every request and loses authority
        # immediately; no token-expiry wait or re-login is needed.
        revoked_session_denied = client.post(
            f"/api/v1/projects/project-a/membership-requests/{target_two['request_id']}:approve",
            json={"reason": "stale session must fail"},
            headers=_headers(delegate_token, "stale-session-approval"),
        )
        assert revoked_session_denied.status_code == 403
        assert revoked_session_denied.json()["code"] == "ACCESS_MANAGEMENT_REQUIRED"


def _inventory_fact(project_id: str, physical_id: str) -> CapacityInventoryFact:
    return CapacityInventoryFact(
        snapshot_id="same-snapshot-id",
        project_id=project_id,
        physical_instance_id=physical_id,
        logical_object_id=f"logical-{physical_id}",
        physical_bytes="10",
        disposition=InventoryDisposition.PRIMARY,
        business_category=BusinessCapacityCategory.RAW,
        object_role=ObjectRole.RAW,
        observed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )


def _reader(project_id: str) -> AuthContext:
    return AuthContext(
        subject_id=f"reader-{project_id}",
        project_ids=frozenset({project_id}),
        region_codes=frozenset(),
        roles=frozenset({Role.UPLOADER.value}),
        scope_pairs=frozenset({(project_id, None)}),
    )


def test_pagination_cursor_cannot_be_reused_with_the_same_snapshot_id_in_another_scope() -> None:
    service = StorageGovernanceService(
        InMemoryStorageRepository(), cursor_secret="be23-cursor-test-secret"
    )
    for project_id in ("project-a", "project-b"):
        service.record_inventory_snapshot(
            project_id=project_id,
            snapshot_id="same-snapshot-id",
            facts=(
                _inventory_fact(project_id, "physical-1"),
                _inventory_fact(project_id, "physical-2"),
            ),
        )

    first = service.inventory_page(project_id="project-a", actor=_reader("project-a"), limit=1)
    assert first.page_info.end_cursor is not None
    with pytest.raises(ProblemException) as denied:
        service.inventory_page(
            project_id="project-b",
            actor=_reader("project-b"),
            snapshot_id="same-snapshot-id",
            cursor=first.page_info.end_cursor,
            limit=1,
        )
    assert denied.value.problem.code == "CURSOR_SCOPE_MISMATCH"


def test_legacy_multi_project_region_arrays_fail_closed_without_explicit_pairs() -> None:
    ambiguous = AuthContext(
        subject_id="legacy-jwt-user",
        project_ids=frozenset({"project-a", "project-b"}),
        region_codes=frozenset({"cn-hz", "us-west"}),
        roles=frozenset({Role.UPLOADER.value}),
    )
    with pytest.raises(ProblemException) as denied:
        ScopeGuard.require(ambiguous, "project-a", "us-west")
    assert denied.value.problem.code == "REGION_SCOPE_AMBIGUOUS"

    paired = ambiguous.__class__(
        subject_id=ambiguous.subject_id,
        project_ids=ambiguous.project_ids,
        region_codes=ambiguous.region_codes,
        roles=ambiguous.roles,
        scope_pairs=frozenset({("project-a", "cn-hz"), ("project-b", "us-west")}),
    )
    ScopeGuard.require(paired, "project-a", "cn-hz")
    with pytest.raises(ProblemException) as cross_pair:
        ScopeGuard.require(paired, "project-a", "us-west")
    assert cross_pair.value.problem.code == "REGION_SCOPE_DENIED"

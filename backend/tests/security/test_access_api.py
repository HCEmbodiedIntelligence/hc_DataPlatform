from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.core.openapi import aggregate_fragments
from hc_data_platform.security.access_models import (
    AccessDecisionCommand,
    AccessRequestStatus,
    MembershipRequestCreate,
    RegistrationCommand,
)
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.auth import AuthContext, Role


class AdminVerifier:
    def verify(self, token: str) -> AuthContext:
        project = {"admin-a": "project-a", "admin-b": "project-b"}.get(token)
        if project is None:
            raise AssertionError("unexpected external test token")
        return AuthContext(
            subject_id=token,
            project_ids=frozenset({project}),
            region_codes=frozenset(),
            roles=frozenset({Role.ADMIN.value}),
            scope_pairs=frozenset({(project, None)}),
        )


class AlwaysLimited:
    def check(self, *, operation: str, subject_hint: str | None) -> None:
        del operation, subject_hint
        raise problem(
            status=429,
            code="ABUSE_POLICY_LIMITED",
            title="Request rate limited",
            detail="The externally configured abuse policy denied this request.",
            retryable=True,
        )


def _settings() -> Settings:
    return Settings(environment="test", runtime_backend="memory", _env_file=None)


def _client(
    repository: InMemoryAccessRepository | None = None,
    *,
    service: AccessService | None = None,
) -> TestClient:
    access = service or AccessService(repository or InMemoryAccessRepository())
    return TestClient(
        create_app(
            settings=_settings(),
            jwt_verifier=AdminVerifier(),  # type: ignore[arg-type]
            access_service=access,
        )
    )


def _register_and_login(client: TestClient, username: str) -> tuple[str, dict[str, Any]]:
    password = f"{username}-test-password"
    registered = client.post(
        "/api/v1/auth/registrations",
        json={"username": username, "password": password},
    )
    assert registered.status_code == 201, registered.text
    assert "PENDING" not in registered.text
    assert "project" not in registered.text.lower()
    logged_in = client.post(
        "/api/v1/auth/sessions",
        json={"username": username, "password": password},
    )
    assert logged_in.status_code == 201, logged_in.text
    return logged_in.json()["access_token"], registered.json()["principal"]


def _headers(token: str, key: str | None = None) -> dict[str, str]:
    result = {"Authorization": f"Bearer {token}"}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def _resolve_local_reference(document: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in value:
        resolved: Any = document
        for part in value["$ref"].removeprefix("#/").split("/"):
            resolved = resolved[part]
        value = resolved
    return value


def test_access_runtime_openapi_matches_the_canonical_fragment() -> None:
    formal = aggregate_fragments(Path(__file__).parents[2] / "openapi")
    runtime = create_app(settings=_settings()).openapi()

    def access_paths(document: dict[str, Any]) -> set[str]:
        return {
            path
            for path in document["paths"]
            if path.startswith("/api/v1/auth/")
            or "membership-requests" in path
            or "capability-requests" in path
            or path.endswith("/access-audit-events")
        }

    assert access_paths(formal) == access_paths(runtime)
    for path in access_paths(formal):
        formal_item = formal["paths"][path]
        runtime_item = runtime["paths"][path]
        formal_methods = {key for key in formal_item if key in {"get", "post"}}
        runtime_methods = {key for key in runtime_item if key in {"get", "post"}}
        assert formal_methods == runtime_methods
        for method in formal_methods:
            formal_operation = formal_item[method]
            runtime_operation = runtime_item[method]
            assert formal_operation["operationId"] == runtime_operation["operationId"]
            assert formal_operation.get("security", []) == runtime_operation.get("security", [])
            assert set(formal_operation["responses"]) == set(runtime_operation["responses"])
            for code in {"401", "403", "409", "422", "429"} & set(formal_operation["responses"]):
                formal_response = _resolve_local_reference(
                    formal, formal_operation["responses"][code]
                )
                runtime_response = runtime_operation["responses"][code]
                assert set(formal_response["content"]) == {"application/problem+json"}
                assert set(runtime_response["content"]) == {"application/problem+json"}
                for response in (formal_response, runtime_response):
                    assert (
                        response["content"]["application/problem+json"]["schema"]["$ref"]
                        == "#/components/schemas/ProblemDetails"
                    )

    registration_contract = formal["paths"]["/api/v1/auth/registrations"]["post"]
    registration_example = registration_contract["responses"]["201"]["content"]["application/json"][
        "example"
    ]
    assert registration_example["principal"]["status"] == "ACTIVE"
    assert set(registration_example) == {"principal"}
    assert "PENDING" not in json.dumps(registration_example)
    assert not any("recover" in path or "reset" in path for path in access_paths(formal))


def test_registration_creates_loginable_empty_account_and_problem_details_are_redacted() -> None:
    with _client() as client:
        token, principal = _register_and_login(client, "empty-user")
        assert principal["status"] == "ACTIVE"

        bootstrap = client.get("/api/v1/auth/session/bootstrap", headers=_headers(token))
        assert bootstrap.status_code == 200
        assert bootstrap.json()["principal"] == principal
        assert bootstrap.json()["available_scopes"] == []
        assert bootstrap.json()["capability_revision"] == 0

        secret = "must-never-be-echoed"
        invalid = client.post(
            "/api/v1/auth/registrations",
            json={"username": "bad-user", "password": "", "unexpected": secret},
        )
        assert invalid.status_code == 422
        assert invalid.headers["content-type"].startswith("application/problem+json")
        assert invalid.json()["code"] == "REQUEST_VALIDATION_FAILED"
        assert secret not in invalid.text
        assert '"input"' not in invalid.text

        unauthorized = client.get("/api/v1/auth/session/bootstrap")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["content-type"].startswith("application/problem+json")
        assert unauthorized.json()["request_id"]

        invalid_identifier = client.get(
            "/api/v1/projects/project-a/membership-requests/not-a-uuid",
            headers=_headers(token),
        )
        assert invalid_identifier.status_code == 422
        assert invalid_identifier.headers["content-type"].startswith("application/problem+json")


def test_externally_configured_rate_limit_uses_problem_details_without_fixed_threshold() -> None:
    service = AccessService(InMemoryAccessRepository(), abuse_protection=AlwaysLimited())
    with _client(service=service) as client:
        response = client.post(
            "/api/v1/auth/registrations",
            json={"username": "limited-user", "password": "not-empty"},
        )
    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "ABUSE_POLICY_LIMITED"


def test_two_users_are_repository_scoped_and_revocation_is_immediate() -> None:
    repository = InMemoryAccessRepository()
    with _client(repository) as client:
        alice_token, alice = _register_and_login(client, "alice")
        bob_token, _ = _register_and_login(client, "bob")

        alice_membership = client.post(
            "/api/v1/projects/project-a/membership-requests",
            json={"reason": "alice joins a"},
            headers=_headers(alice_token, "alice-membership"),
        )
        bob_membership = client.post(
            "/api/v1/projects/project-b/membership-requests",
            json={"reason": "bob joins b"},
            headers=_headers(bob_token, "bob-membership"),
        )
        assert alice_membership.status_code == bob_membership.status_code == 201

        # Alice cannot use a guessed request id to read Bob's project request.
        guessed = client.get(
            "/api/v1/projects/project-b/membership-requests/" + bob_membership.json()["request_id"],
            headers=_headers(alice_token),
        )
        assert guessed.status_code == 404

        # An administrator is also confined to the exact project in the verified token.
        cross_admin = client.get(
            "/api/v1/projects/project-b/membership-requests/" + bob_membership.json()["request_id"],
            headers=_headers("admin-a"),
        )
        assert cross_admin.status_code == 404

        cross_project_approval = client.post(
            "/api/v1/projects/project-b/membership-requests/"
            + bob_membership.json()["request_id"]
            + ":approve",
            json={"reason": "must be denied"},
            headers=_headers("admin-a", "cross-project-approval"),
        )
        assert cross_project_approval.status_code == 403
        assert cross_project_approval.headers["content-type"].startswith("application/problem+json")

        approve_path = (
            "/api/v1/projects/project-a/membership-requests/"
            + alice_membership.json()["request_id"]
            + ":approve"
        )
        approved = client.post(
            approve_path,
            json={"reason": "approved"},
            headers=_headers("admin-a", "approve-membership-1"),
        )
        repeated = client.post(
            approve_path,
            json={"reason": "approved"},
            headers=_headers("admin-a", "approve-membership-2"),
        )
        assert approved.status_code == repeated.status_code == 200
        assert approved.json() == repeated.json()

        conflicting = client.post(
            approve_path.removesuffix(":approve") + ":reject",
            json={"reason": "too late"},
            headers=_headers("admin-a", "reject-approved-membership"),
        )
        assert conflicting.status_code == 409
        assert conflicting.headers["content-type"].startswith("application/problem+json")

        sensitive_reason = "private-business-justification-must-not-enter-audit"
        capability = client.post(
            "/api/v1/projects/project-a/capability-requests",
            json={"capability_keys": ["datasets.read"], "reason": sensitive_reason},
            headers=_headers(alice_token, "alice-capability"),
        )
        assert capability.status_code == 201
        capability_path = (
            "/api/v1/projects/project-a/capability-requests/" + capability.json()["request_id"]
        )
        approved_capability = client.post(
            capability_path + ":approve",
            json={"reason": "approved"},
            headers=_headers("admin-a", "approve-capability"),
        )
        assert approved_capability.status_code == 200

        before_revoke = client.get(
            "/api/v1/auth/session/bootstrap", headers=_headers(alice_token)
        ).json()
        assert before_revoke["available_scopes"] == [
            {
                "project_id": "project-a",
                "region_codes": [],
                "project_wide": True,
                "capabilities": ["datasets.read"],
            }
        ]

        revoked = client.post(
            capability_path + ":revoke",
            json={"reason": "no longer needed"},
            headers=_headers("admin-a", "revoke-capability"),
        )
        assert revoked.status_code == 200
        after_revoke = client.get(
            "/api/v1/auth/session/bootstrap", headers=_headers(alice_token)
        ).json()
        assert after_revoke["available_scopes"][0]["capabilities"] == []
        assert after_revoke["capability_revision"] > before_revoke["capability_revision"]

        revoked_membership = client.post(
            approve_path.removesuffix(":approve") + ":revoke",
            json={"reason": "project access removed"},
            headers=_headers("admin-a", "revoke-membership"),
        )
        assert revoked_membership.status_code == 200
        assert (
            client.get("/api/v1/auth/session/bootstrap", headers=_headers(alice_token)).json()[
                "available_scopes"
            ]
            == []
        )

        audit = client.get(
            "/api/v1/projects/project-a/access-audit-events",
            headers=_headers("admin-a"),
        )
        assert audit.status_code == 200
        actions = {event["action"] for event in audit.json()["items"]}
        assert {
            "access.membership.requested",
            "access.membership.approved",
            "access.capability.approved",
            "access.capability.revoked",
            "access.membership.revoked",
        }.issubset(actions)
        assert "alice-test-password" not in audit.text
        assert sensitive_reason not in audit.text
        assert alice["principal_id"] in audit.text


def test_concurrent_duplicate_approval_has_one_effect_and_conflicting_decision_is_409() -> None:
    repository = InMemoryAccessRepository()
    service = AccessService(repository)
    principal = service.register(
        RegistrationCommand(username="concurrent-user", password="safe-password"),
        request_id="register",
    ).principal
    requester = AuthContext(
        subject_id=principal.principal_id,
        project_ids=frozenset(),
        region_codes=frozenset(),
        roles=frozenset(),
    )
    administrator = AuthContext(
        subject_id="admin-a",
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset(),
        roles=frozenset({Role.ADMIN.value}),
        scope_pairs=frozenset({("project-a", None)}),
    )
    requested = service.create_membership_request(
        auth=requester,
        project_id="project-a",
        command=MembershipRequestCreate(reason="join"),
        idempotency_key="create",
        request_id="create",
    )

    def approve(index: int) -> str:
        return service.decide_membership_request(
            auth=administrator,
            project_id="project-a",
            access_request_id=requested.request_id,
            target_status=AccessRequestStatus.APPROVED,
            command=AccessDecisionCommand(reason="approved"),
            idempotency_key=f"approve-{index}",
            request_id=f"approve-{index}",
        ).status.value

    with ThreadPoolExecutor(max_workers=16) as executor:
        assert set(executor.map(approve, range(64))) == {"APPROVED"}

    with pytest.raises(ProblemException) as conflict:
        service.decide_membership_request(
            auth=administrator,
            project_id="project-a",
            access_request_id=requested.request_id,
            target_status=AccessRequestStatus.REJECTED,
            command=AccessDecisionCommand(reason="too late"),
            idempotency_key="reject-after-approve",
            request_id="reject-after-approve",
        )
    assert conflict.value.problem.status == 409
    assert conflict.value.problem.code == "ACCESS_REQUEST_STATE_CONFLICT"


def test_membership_and_capability_requests_support_reject_and_withdraw() -> None:
    with _client() as client:
        token, _ = _register_and_login(client, "request-state-user")

        withdrawn_membership = client.post(
            "/api/v1/projects/project-a/membership-requests",
            json={"reason": "temporary"},
            headers=_headers(token, "membership-to-withdraw"),
        ).json()
        response = client.post(
            "/api/v1/projects/project-a/membership-requests/"
            + withdrawn_membership["request_id"]
            + ":withdraw",
            json={"reason": "changed mind"},
            headers=_headers(token, "withdraw-membership"),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "WITHDRAWN"

        rejected_membership = client.post(
            "/api/v1/projects/project-a/membership-requests",
            json={"reason": "new request"},
            headers=_headers(token, "membership-to-reject"),
        ).json()
        response = client.post(
            "/api/v1/projects/project-a/membership-requests/"
            + rejected_membership["request_id"]
            + ":reject",
            json={"reason": "not approved"},
            headers=_headers("admin-a", "reject-membership"),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "REJECTED"

        withdrawn_capability = client.post(
            "/api/v1/projects/project-a/capability-requests",
            json={"capability_keys": ["datasets.read"]},
            headers=_headers(token, "capability-to-withdraw"),
        ).json()
        response = client.post(
            "/api/v1/projects/project-a/capability-requests/"
            + withdrawn_capability["request_id"]
            + ":withdraw",
            json={"reason": "not needed"},
            headers=_headers(token, "withdraw-capability"),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "WITHDRAWN"

        rejected_capability = client.post(
            "/api/v1/projects/project-a/capability-requests",
            json={"capability_keys": ["datasets.write"]},
            headers=_headers(token, "capability-to-reject"),
        ).json()
        response = client.post(
            "/api/v1/projects/project-a/capability-requests/"
            + rejected_capability["request_id"]
            + ":reject",
            json={"reason": "not approved"},
            headers=_headers("admin-a", "reject-capability"),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "REJECTED"

        forbidden = client.post(
            "/api/v1/projects/project-a/capability-requests/"
            + rejected_capability["request_id"]
            + ":approve",
            json={"reason": "wrong project manager"},
            headers=_headers("admin-b", "cross-project-approve"),
        )
        assert forbidden.status_code == 403
        assert forbidden.headers["content-type"].startswith("application/problem+json")

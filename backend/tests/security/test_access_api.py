from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.core.openapi import aggregate_fragments
from hc_data_platform.security.abuse import (
    AuthAbusePolicy,
    InMemoryAbuseProtection,
    PublicAuthAttempt,
    RateLimit,
)
from hc_data_platform.security.access_models import (
    AccessDecisionCommand,
    AccessRequestStatus,
    AvailableScope,
    MembershipRequestCreate,
    PublicAuthChallengeConfiguration,
    PublicAuthChallengeProvider,
    RegistrationCommand,
)
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.auth import AuthContext, Role
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE,
    CAPABILITY_PLATFORM_ADMIN,
)
from hc_data_platform.security.passwords import PasswordPolicy


class AdminVerifier:
    def verify(self, token: str) -> AuthContext:
        if token == "security-admin":
            return AuthContext(
                subject_id="security-admin",
                project_ids=frozenset(),
                region_codes=frozenset(),
                roles=frozenset(),
                capabilities=frozenset({CAPABILITY_PLATFORM_ACCOUNT_SECURITY_MANAGE}),
            )
        scope = {
            "admin-a": ("org-a", "project-a"),
            "admin-b": ("org-b", "project-b"),
        }.get(token)
        if scope is None:
            raise AssertionError("unexpected external test token")
        organization_id, project_id = scope
        return AuthContext(
            subject_id=token,
            organization_ids=frozenset({organization_id}),
            project_ids=frozenset({project_id}),
            region_codes=frozenset(),
            roles=frozenset({Role.ADMIN.value}),
            scope_pairs=frozenset({(project_id, None)}),
            organization_scope_triples=frozenset({(organization_id, project_id, None)}),
        )


class AlwaysLimited:
    def check(self, attempt: PublicAuthAttempt, *, request_id: str) -> bool:
        del attempt, request_id
        raise problem(
            status=429,
            code="ABUSE_POLICY_LIMITED",
            title="Request rate limited",
            detail="The externally configured abuse policy denied this request.",
            retryable=True,
            retry_after_seconds=30,
        )

    def record_login_failure(
        self,
        attempt: PublicAuthAttempt,
        *,
        principal_id: str | None,
        request_id: str,
    ) -> None:
        del attempt, principal_id, request_id

    def record_login_success(self, attempt: PublicAuthAttempt, *, request_id: str) -> None:
        del attempt, request_id

    def record_challenge_denied(
        self,
        attempt: PublicAuthAttempt,
        *,
        reason: str,
        request_id: str,
    ) -> None:
        del attempt, reason, request_id

    def check_authenticated(self, *, operation: str, subject_hint: str | None) -> None:
        del operation, subject_hint

    def unlock_account(
        self,
        *,
        principal_id: str,
        actor_id: str,
        request_id: str,
    ) -> bool:
        del principal_id, actor_id, request_id
        return False


class AcceptingChallengeVerifier:
    configuration = PublicAuthChallengeConfiguration(
        provider=PublicAuthChallengeProvider.TURNSTILE,
        site_key="test-turnstile-site-key",
    )

    def __init__(self) -> None:
        self.responses: list[str | None] = []

    def verify(self, *, response_token: str | None, operation: str) -> None:
        self.responses.append(response_token)
        assert operation == "LOGIN"
        if response_token is None:
            raise problem(
                status=403,
                code="AUTH_CHALLENGE_REQUIRED",
                title="Authentication challenge required",
                detail="Complete the authentication challenge before trying again.",
            )
        if response_token != "passed-challenge-token":
            raise problem(
                status=403,
                code="AUTH_CHALLENGE_INVALID",
                title="Authentication challenge rejected",
                detail="Complete a new authentication challenge before trying again.",
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
    password = "Access API passphrase 2026!"
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


def test_zero_scope_account_can_join_organization_without_reauthentication() -> None:
    repository = InMemoryAccessRepository(organization_projects=(("org-a", "project-a"),))
    service = AccessService(repository)
    with _client(service=service) as client:
        token, principal = _register_and_login(client, "personal-mode-user")

        initial = client.get("/api/v1/auth/session/bootstrap", headers=_headers(token))
        assert initial.status_code == 200
        assert initial.json()["available_organizations"] == []
        assert initial.json()["available_scopes"] == []

        requested = client.post(
            "/api/v1/account/organization-membership-requests",
            headers=_headers(token, "join-org-a"),
            json={
                "organization_id_or_join_code": "org-a",
                "reason": "需要参与组织内的数据质量工作",
            },
        )
        assert requested.status_code == 201, requested.text
        assert requested.json()["kind"] == "ORGANIZATION"
        assert requested.json()["status"] == "PENDING"

        platform_admin = AuthContext(
            subject_id="platform-admin",
            project_ids=frozenset(),
            region_codes=frozenset(),
            roles=frozenset(),
            capabilities=frozenset({CAPABILITY_PLATFORM_ADMIN}),
        )
        service.decide_organization_membership_request(
            auth=platform_admin,
            access_request_id=requested.json()["request_id"],
            target_status=AccessRequestStatus.APPROVED,
            command=AccessDecisionCommand(reason="approved"),
            idempotency_key="approve-org-a",
            request_id="approve-org-a",
        )

        refreshed = client.get("/api/v1/auth/session/bootstrap", headers=_headers(token))
        assert refreshed.status_code == 200
        assert refreshed.json()["principal"]["principal_id"] == principal["principal_id"]
        assert refreshed.json()["available_organizations"] == [
            {
                "organization_id": "org-a",
                "organization_name": "org-a",
                "member_status": "ACTIVE",
            }
        ]
        assert refreshed.json()["available_scopes"] == []

        overview = client.get("/api/v1/account/access-overview", headers=_headers(token))
        assert overview.status_code == 200
        assert overview.json()["pending_request_count"] == 0
        assert overview.json()["projects"] == []

        service.decide_organization_membership_request(
            auth=platform_admin,
            access_request_id=requested.json()["request_id"],
            target_status=AccessRequestStatus.REVOKED,
            command=AccessDecisionCommand(reason="access no longer required"),
            idempotency_key="revoke-org-a",
            request_id="revoke-org-a",
        )
        after_revoke = client.get("/api/v1/auth/session/bootstrap", headers=_headers(token))
        assert after_revoke.status_code == 200
        assert after_revoke.json()["principal"]["principal_id"] == principal["principal_id"]
        assert after_revoke.json()["available_organizations"] == []
        assert after_revoke.json()["available_scopes"] == []


def _resolve_local_reference(document: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in value:
        resolved: Any = document
        for part in value["$ref"].removeprefix("#/").split("/"):
            resolved = resolved[part]
        value = resolved
    return value


def _canonical_wire_schema(value: Any) -> Any:
    """Remove presentation noise and normalize equivalent nullable/numeric forms."""

    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        normalized = [_canonical_wire_schema(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
    if not isinstance(value, dict):
        return value

    normalized = {
        key: _canonical_wire_schema(item)
        for key, item in value.items()
        if key not in {"title", "description"}
    }
    type_value = normalized.get("type")
    if isinstance(type_value, list) and "null" in type_value:
        non_null_types = [item for item in type_value if item != "null"]
        if len(non_null_types) == 1:
            non_null_schema = {
                **{key: item for key, item in normalized.items() if key != "type"},
                "type": non_null_types[0],
            }
            return {"nullable": _canonical_wire_schema(non_null_schema)}

    any_of = normalized.get("anyOf")
    if set(normalized) == {"anyOf"} and isinstance(any_of, list):
        non_null_schemas = [item for item in any_of if item != {"type": "null"}]
        if len(non_null_schemas) == 1 and len(any_of) == 2:
            return {"nullable": non_null_schemas[0]}
    return normalized


def test_access_runtime_openapi_matches_the_canonical_fragment() -> None:
    formal = aggregate_fragments(Path(__file__).parents[2] / "openapi")
    runtime = create_app(settings=_settings()).openapi()

    def access_paths(document: dict[str, Any]) -> set[str]:
        return {
            path
            for path in document["paths"]
            if path.startswith("/api/v1/auth/")
            or path.startswith("/api/v1/account/")
            or path.startswith("/api/v1/platform/accounts/")
            or "membership-requests" in path
            or "capability-requests" in path
            or path.endswith("/access-audit-events")
        }

    assert access_paths(formal) == access_paths(runtime)
    operation_methods = {"get", "put", "post", "delete", "head", "patch"}
    for path in access_paths(formal):
        formal_item = formal["paths"][path]
        runtime_item = runtime["paths"][path]
        formal_methods = {key for key in formal_item if key in operation_methods}
        runtime_methods = {key for key in runtime_item if key in operation_methods}
        assert formal_methods == runtime_methods
        for method in formal_methods:
            formal_operation = formal_item[method]
            runtime_operation = runtime_item[method]
            assert formal_operation["operationId"] == runtime_operation["operationId"]
            assert formal_operation.get("security", []) == runtime_operation.get("security", [])
            assert set(formal_operation["responses"]) == set(runtime_operation["responses"])
            for code in formal_operation["responses"]:
                formal_response = _resolve_local_reference(
                    formal, formal_operation["responses"][code]
                )
                runtime_response = _resolve_local_reference(
                    runtime, runtime_operation["responses"][code]
                )
                formal_headers = formal_response.get("headers", {})
                runtime_headers = runtime_response.get("headers", {})
                assert set(runtime_headers) == set(formal_headers), (path, method, code)
                for header_name in formal_headers:
                    formal_header = _resolve_local_reference(formal, formal_headers[header_name])
                    runtime_header = _resolve_local_reference(runtime, runtime_headers[header_name])
                    assert _canonical_wire_schema(
                        runtime_header["schema"]
                    ) == _canonical_wire_schema(formal_header["schema"]), (
                        path,
                        method,
                        code,
                        header_name,
                    )
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
                if code == "429":
                    formal_retry_after = _resolve_local_reference(
                        formal,
                        formal_response["headers"]["Retry-After"],
                    )
                    runtime_retry_after = runtime_response["headers"]["Retry-After"]
                    for retry_after in (formal_retry_after, runtime_retry_after):
                        assert retry_after["schema"] == {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 86_400,
                        }

    registration_contract = formal["paths"]["/api/v1/auth/registrations"]["post"]
    registration_example = registration_contract["responses"]["201"]["content"]["application/json"][
        "example"
    ]
    assert registration_example["principal"]["status"] == "ACTIVE"
    assert set(registration_example) == {"principal"}
    assert "PENDING" not in json.dumps(registration_example)
    assert {
        "/api/v1/auth/password-recovery-requests",
        "/api/v1/auth/password-recovery-confirmations",
        "/api/v1/account/recovery-email-verifications",
        "/api/v1/account/recovery-email:confirm",
    } <= access_paths(formal)
    config_contract = formal["paths"]["/api/v1/auth/config"]["get"]
    runtime_config_contract = runtime["paths"]["/api/v1/auth/config"]["get"]
    assert config_contract["security"] == []
    assert runtime_config_contract["security"] == config_contract["security"]
    assert set(runtime_config_contract["responses"]) == set(config_contract["responses"])
    assert (
        config_contract["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/PublicAuthConfiguration"
    )
    assert (
        runtime_config_contract["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == config_contract["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    )
    assert "Cache-Control" in config_contract["responses"]["200"]["headers"]
    assert "Cache-Control" in runtime_config_contract["responses"]["200"]["headers"]
    assert formal["components"]["responses"]["AccessTooManyRequests"]["headers"] == {
        "Retry-After": {"$ref": "#/components/headers/RetryAfter"}
    }
    formal_session_operation = formal["paths"]["/api/v1/auth/sessions"]["post"]
    assert "SESSION_LIMIT_REACHED" in formal_session_operation["description"]
    formal_too_many = formal["components"]["responses"]["AccessTooManyRequests"]
    runtime_too_many = runtime["paths"]["/api/v1/auth/sessions"]["post"]["responses"]["429"]
    assert formal_too_many["description"] == runtime_too_many["description"]
    assert "session-admission" in formal_too_many["description"]


def test_access_runtime_openapi_matches_resolved_wire_schema_semantics() -> None:
    formal = aggregate_fragments(Path(__file__).parents[2] / "openapi")
    runtime = create_app(settings=_settings()).openapi()
    schema_names = {
        "ProblemDetails",
        "AccountStatus",
        "AccountPrincipal",
        "PasswordPolicyView",
        "PublicAuthChallengeProvider",
        "PublicAuthChallengeConfiguration",
        "PublicAuthConfiguration",
        "PasswordRecoveryRequest",
        "PasswordRecoveryAccepted",
        "PasswordRecoveryConfirmation",
        "PasswordRecoveryCompleted",
        "RecoveryEmailVerificationRequest",
        "RecoveryEmailConfirmation",
        "RecoveryEmailConfigured",
        "RegistrationCommand",
        "RegistrationResult",
        "LoginCommand",
        "SessionCreated",
        "AvailableScope",
        "SessionBootstrap",
        "AccountNotificationKind",
        "AccountNotificationResourceType",
        "AccountNotificationState",
        "AccountNotification",
        "AccountNotificationPage",
        "AccountNotificationUnreadCount",
    }

    for schema_name in schema_names:
        formal_schema = formal["components"]["schemas"][schema_name]
        runtime_schema = runtime["components"]["schemas"][schema_name]
        assert _canonical_wire_schema(runtime_schema) == _canonical_wire_schema(formal_schema), (
            schema_name
        )

    response_schemas = (
        ("/api/v1/auth/config", "get", "200", "application/json"),
        ("/api/v1/auth/sessions", "post", "201", "application/json"),
        ("/api/v1/auth/sessions", "post", "429", "application/problem+json"),
        ("/api/v1/auth/password-recovery-requests", "post", "202", "application/json"),
        ("/api/v1/auth/password-recovery-confirmations", "post", "200", "application/json"),
        (
            "/api/v1/account/recovery-email-verifications",
            "post",
            "202",
            "application/json",
        ),
        ("/api/v1/account/recovery-email:confirm", "post", "200", "application/json"),
    )
    for path, method, status, media_type in response_schemas:
        formal_response = _resolve_local_reference(
            formal, formal["paths"][path][method]["responses"][status]
        )
        runtime_response = _resolve_local_reference(
            runtime, runtime["paths"][path][method]["responses"][status]
        )
        formal_schema = _resolve_local_reference(
            formal,
            formal_response["content"][media_type]["schema"],
        )
        runtime_schema = _resolve_local_reference(
            runtime,
            runtime_response["content"][media_type]["schema"],
        )
        assert _canonical_wire_schema(runtime_schema) == _canonical_wire_schema(formal_schema), (
            path,
            method,
            status,
        )


def test_security_component_required_and_closed_object_semantics_match_runtime() -> None:
    fragment = yaml.safe_load(
        (Path(__file__).parents[2] / "openapi/security.yaml").read_text(encoding="utf-8")
    )
    runtime = create_app(settings=_settings()).openapi()
    formal_schemas = fragment["components"]["schemas"]
    runtime_schemas = runtime["components"]["schemas"]

    for schema_name in sorted(set(formal_schemas) & set(runtime_schemas)):
        formal_schema = formal_schemas[schema_name]
        runtime_schema = runtime_schemas[schema_name]
        assert set(runtime_schema.get("required", [])) == set(formal_schema.get("required", [])), (
            schema_name
        )
        assert runtime_schema.get("additionalProperties", True) == formal_schema.get(
            "additionalProperties", True
        ), schema_name


def test_available_scope_requires_explicit_unique_nonempty_values() -> None:
    scope = AvailableScope(
        organization_id="organization-a",
        project_id="project-a",
        region_codes=(),
        project_wide=True,
        capabilities=(),
    )
    assert scope.region_codes == ()
    assert scope.capabilities == ()

    with pytest.raises(ValidationError):
        AvailableScope(project_id="project-a")
    with pytest.raises(ValidationError):
        AvailableScope(
            organization_id="organization-a",
            project_id="project-a",
            region_codes=("region-a", "region-a"),
            project_wide=True,
            capabilities=(),
        )
    with pytest.raises(ValidationError):
        AvailableScope(
            organization_id="organization-a",
            project_id="project-a",
            region_codes=(),
            project_wide=True,
            capabilities=("",),
        )


def test_generated_frontend_operation_gate_exactly_matches_runtime_openapi() -> None:
    runtime = create_app(settings=_settings()).openapi()
    methods = {"get", "put", "post", "delete", "head", "patch"}
    expected = {
        (method.upper(), path.removeprefix("/api/v1"))
        for path, path_item in runtime["paths"].items()
        if path.startswith("/api/v1/")
        for method in path_item
        if method in methods
    }
    operation_map = (
        Path(__file__).parents[3] / "frontend/src/shared/api/generated/platform-operations.ts"
    ).read_text(encoding="utf-8")
    generated = re.findall(
        r'^  \{ method: "([A-Z]+)", path: "([^"]+)" \},$',
        operation_map,
        flags=re.MULTILINE,
    )

    assert len(generated) == len(set(generated)), "generated operation gate contains duplicates"
    assert set(generated) == expected, {
        "unexpected": sorted(set(generated) - expected),
        "missing": sorted(expected - set(generated)),
    }


def test_public_auth_config_is_anonymous_no_store_and_centrally_configured() -> None:
    settings = Settings(
        environment="test",
        runtime_backend="memory",
        password_min_length=17,
        password_max_length=64,
        _env_file=None,
    )
    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/api/v1/auth/config")
        stale_bearer_response = client.get(
            "/api/v1/auth/config",
            headers={"Authorization": "Bearer stale-public-route-token"},
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert stale_bearer_response.status_code == 200
    assert stale_bearer_response.json() == response.json()
    assert response.json() == {
        "password_policy": {
            "min_length": 17,
            "max_length": 64,
            "disallow_username": True,
            "blocked_password_count": len(PasswordPolicy().blocked_passwords),
        },
        "challenge": None,
    }
    assert (
        create_app(settings=settings).openapi()["paths"]["/api/v1/auth/config"]["get"]["security"]
        == []
    )


def test_public_login_challenge_header_is_required_only_after_the_failure_threshold() -> None:
    policy = InMemoryAbuseProtection(
        hmac_secret="access-api-challenge-test-secret-at-least-32",
        policy=AuthAbusePolicy(
            login_source=RateLimit(100, 60),
            login_subject=RateLimit(100, 900),
            register_source=RateLimit(100, 600),
            register_subject=RateLimit(100, 3600),
            global_limit=RateLimit(100, 60),
            failure_window_seconds=900,
            challenge_after_failures=3,
            delay_after_failures=10,
            delay_initial_seconds=30,
            delay_max_seconds=600,
            lock_after_failures=20,
            lock_seconds=3600,
        ),
    )
    verifier = AcceptingChallengeVerifier()
    service = AccessService(
        InMemoryAccessRepository(),
        abuse_protection=policy,
        challenge_verifier=verifier,
    )
    settings = Settings(
        environment="test",
        runtime_backend="memory",
        auth_abuse_enabled=True,
        auth_abuse_hmac_secret="access-api-network-attribution-secret-at-least-32",
        _env_file=None,
    )
    app = create_app(settings=settings, access_service=service)
    password = "Challenge-protected HTTP passphrase 2026!"
    with TestClient(app, client=("198.51.100.25", 50_000)) as client:
        registered = client.post(
            "/api/v1/auth/registrations",
            json={"username": "challenge-http-user", "password": password},
        )
        assert registered.status_code == 201, registered.text
        for _ in range(3):
            rejected = client.post(
                "/api/v1/auth/sessions",
                json={"username": "challenge-http-user", "password": "wrong passphrase"},
            )
            assert rejected.status_code == 401
        missing = client.post(
            "/api/v1/auth/sessions",
            json={"username": "challenge-http-user", "password": password},
        )
        passed = client.post(
            "/api/v1/auth/sessions",
            json={"username": "challenge-http-user", "password": password},
            headers={"X-Auth-Challenge-Response": "passed-challenge-token"},
        )

    assert missing.status_code == 403
    assert missing.json()["code"] == "AUTH_CHALLENGE_REQUIRED"
    assert "passed-challenge-token" not in missing.text
    assert passed.status_code == 201, passed.text
    assert verifier.responses == [None, "passed-challenge-token"]


def test_platform_security_capability_unlocks_only_an_active_lock_once() -> None:
    policy = InMemoryAbuseProtection(
        hmac_secret="platform-unlock-api-secret-at-least-32",
        policy=AuthAbusePolicy(
            login_source=RateLimit(100, 60),
            login_subject=RateLimit(100, 900),
            register_source=RateLimit(100, 600),
            register_subject=RateLimit(100, 3600),
            global_limit=RateLimit(100, 60),
            failure_window_seconds=900,
            challenge_after_failures=3,
            delay_after_failures=3,
            delay_initial_seconds=30,
            delay_max_seconds=600,
            lock_after_failures=3,
            lock_seconds=3600,
        ),
    )
    target_principal_id = str(uuid4())
    attempt = PublicAuthAttempt(
        operation="LOGIN",
        source_network="198.51.100.0/24",
        subject_hint="locked-api-user",
    )
    for index in range(3):
        policy.record_login_failure(
            attempt,
            principal_id=target_principal_id,
            request_id=f"lock-{index}",
        )
    service = AccessService(InMemoryAccessRepository(), abuse_protection=policy)
    path = f"/api/v1/platform/accounts/{target_principal_id}:unlock"
    with _client(service=service) as client:
        unauthenticated = client.post(path)
        project_admin = client.post(path, headers=_headers("admin-a"))
        unlocked = client.post(path, headers=_headers("security-admin"))
        replay = client.post(path, headers=_headers("security-admin"))

    assert unauthenticated.status_code == 401
    assert project_admin.status_code == 403
    assert unlocked.status_code == 204
    assert unlocked.headers["Cache-Control"] == "private, no-store"
    assert replay.status_code == 204
    assert [action for action, _ in policy.events].count("auth.login.unlocked") == 1
    policy.check(attempt, request_id="after-unlock")


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
            "/api/v1/organizations/org-a/projects/project-a/membership-requests/not-a-uuid",
            headers=_headers(token),
        )
        assert invalid_identifier.status_code == 422
        assert invalid_identifier.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    ("path", "body", "sentinel"),
    [
        (
            "/api/v1/auth/registrations",
            b'{"username":"unicode-user","password":"surrogate-secret-marker\\ud800"}',
            "surrogate-secret-marker",
        ),
        (
            "/api/v1/auth/sessions",
            b'{"username":"unicode-user","password":"login-secret-marker\\ud800"}',
            "login-secret-marker",
        ),
        (
            "/api/v1/auth/registrations",
            b'{"username":"surrogate-user-marker\\ud800",'
            b'"password":"Independent passphrase 2026!"}',
            "surrogate-user-marker",
        ),
        (
            "/api/v1/auth/sessions",
            b'{"username":"nul-user-marker\\u0000","password":"Independent passphrase 2026!"}',
            "nul-user-marker",
        ),
    ],
)
def test_public_auth_rejects_non_scalar_or_control_input_without_echo(
    path: str,
    body: bytes,
    sentinel: str,
) -> None:
    with _client() as client:
        response = client.post(
            path,
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"
    assert sentinel not in response.text
    assert "ud800" not in response.text.lower()


def test_valid_unicode_password_round_trips_and_password_change_rejects_surrogates() -> None:
    unicode_password = "独立安全密码短语🔐-二〇二六-OnlyHere!"
    with _client() as client:
        registered = client.post(
            "/api/v1/auth/registrations",
            json={"username": "unicode-user", "password": unicode_password},
        )
        assert registered.status_code == 201, registered.text
        logged_in = client.post(
            "/api/v1/auth/sessions",
            json={"username": "unicode-user", "password": unicode_password},
        )
        assert logged_in.status_code == 201, logged_in.text
        token = logged_in.json()["access_token"]
        profile = client.get("/api/v1/account/profile", headers=_headers(token))
        assert profile.status_code == 200, profile.text

        headers = _headers(token, "unicode-password-change")
        headers.update(
            {
                "Content-Type": "application/json",
                "If-Match": profile.headers["ETag"],
            }
        )
        change = client.post(
            "/api/v1/account/password:change",
            headers=headers,
            content=json.dumps(
                {
                    "current_password": unicode_password,
                    "new_password": "change-secret-marker\ud800",
                },
                ensure_ascii=True,
            ).encode("ascii"),
        )

    assert change.status_code == 422
    assert change.headers["content-type"].startswith("application/problem+json")
    assert change.json()["code"] == "REQUEST_VALIDATION_FAILED"
    assert "change-secret-marker" not in change.text
    assert "ud800" not in change.text.lower()


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
    assert response.headers["Retry-After"] == "30"
    assert response.json()["retry_after_seconds"] == 30


def test_session_limit_rejects_new_login_without_revoking_the_existing_session() -> None:
    repository = InMemoryAccessRepository(max_active_sessions=1)
    with _client(repository) as client:
        session_b, _ = _register_and_login(client, "session-cap-user")
        late_session_a = client.post(
            "/api/v1/auth/sessions",
            json={
                "username": "session-cap-user",
                "password": "Access API passphrase 2026!",
            },
        )

        assert late_session_a.status_code == 429
        assert late_session_a.headers["content-type"].startswith("application/problem+json")
        assert "no-store" in late_session_a.headers["Cache-Control"]
        problem_body = late_session_a.json()
        assert problem_body["code"] == "SESSION_LIMIT_REACHED"
        assert problem_body["retryable"] is True
        assert 1 <= problem_body["retry_after_seconds"] <= 86_400
        assert late_session_a.headers["Retry-After"] == str(problem_body["retry_after_seconds"])
        assert "access_token" not in problem_body
        assert (
            client.get(
                "/api/v1/auth/session/bootstrap",
                headers=_headers(session_b),
            ).status_code
            == 200
        )

    assert len(repository._sessions) == 1
    assert all(session.revoked_at is None for session in repository._sessions.values())
    assert sum(event.action == "auth.session.admission.denied" for event in repository._audit) == 1
    assert sum(event.action == "auth.login.succeeded" for event in repository._audit) == 2
    assert not any(event.action == "auth.login.failed" for event in repository._audit)
    assert not any(
        event.safe_details.get("reason") == "SESSION_LIMIT" for event in repository._audit
    )
    safe_audit = json.dumps(
        [event.model_dump(mode="json") for event in repository._audit],
        ensure_ascii=False,
    )
    assert session_b not in safe_audit
    assert "Access API passphrase 2026!" not in safe_audit


def test_logout_revokes_only_the_presented_session_and_is_concurrently_idempotent() -> None:
    repository = InMemoryAccessRepository()
    with _client(repository) as client:
        token, _ = _register_and_login(client, "logout-user")
        second_session = client.post(
            "/api/v1/auth/sessions",
            json={"username": "logout-user", "password": "Access API passphrase 2026!"},
        )
        assert second_session.status_code == 201
        second_token = second_session.json()["access_token"]

        missing = client.post("/api/v1/auth/session:logout")
        assert missing.status_code == 401
        assert missing.json()["code"] == "AUTHENTICATION_REQUIRED"

        def revoke(_: int) -> int:
            return client.post(
                "/api/v1/auth/session:logout",
                headers=_headers(token),
            ).status_code

        with ThreadPoolExecutor(max_workers=8) as executor:
            assert set(executor.map(revoke, range(32))) == {204}

        revoked_bootstrap = client.get(
            "/api/v1/auth/session/bootstrap",
            headers=_headers(token),
        )
        assert revoked_bootstrap.status_code == 401
        assert revoked_bootstrap.json()["code"] == "SESSION_INVALID"
        assert (
            client.get(
                "/api/v1/auth/session/bootstrap",
                headers=_headers(second_token),
            ).status_code
            == 200
        )

        unknown = client.post(
            "/api/v1/auth/session:logout",
            headers=_headers("hcs_unknown-session-token"),
        )
        assert unknown.status_code == 204

    revoke_events = [event for event in repository._audit if event.action == "auth.session.revoked"]
    assert len(revoke_events) == 1
    safe_audit = json.dumps(
        [event.model_dump(mode="json") for event in revoke_events],
        ensure_ascii=False,
    )
    assert token not in safe_audit
    assert second_token not in safe_audit
    assert "logout-user-test-password" not in safe_audit


def test_two_users_are_repository_scoped_and_revocation_is_immediate() -> None:
    repository = InMemoryAccessRepository()
    with _client(repository) as client:
        alice_token, alice = _register_and_login(client, "alice")
        bob_token, _ = _register_and_login(client, "bob")

        alice_membership = client.post(
            "/api/v1/organizations/org-a/projects/project-a/membership-requests",
            json={"reason": "alice joins a"},
            headers=_headers(alice_token, "alice-membership"),
        )
        bob_membership = client.post(
            "/api/v1/organizations/org-b/projects/project-b/membership-requests",
            json={"reason": "bob joins b"},
            headers=_headers(bob_token, "bob-membership"),
        )
        assert alice_membership.status_code == bob_membership.status_code == 201

        # Alice cannot use a guessed request id to read Bob's project request.
        guessed = client.get(
            "/api/v1/organizations/org-b/projects/project-b/membership-requests/"
            + bob_membership.json()["request_id"],
            headers=_headers(alice_token),
        )
        assert guessed.status_code == 404

        # An administrator is also confined to the exact project in the verified token.
        cross_admin = client.get(
            "/api/v1/organizations/org-b/projects/project-b/membership-requests/"
            + bob_membership.json()["request_id"],
            headers=_headers("admin-a"),
        )
        assert cross_admin.status_code == 404

        cross_project_approval = client.post(
            "/api/v1/organizations/org-b/projects/project-b/membership-requests/"
            + bob_membership.json()["request_id"]
            + ":approve",
            json={"reason": "must be denied"},
            headers=_headers("admin-a", "cross-project-approval"),
        )
        assert cross_project_approval.status_code == 403
        assert cross_project_approval.headers["content-type"].startswith("application/problem+json")

        approve_path = (
            "/api/v1/organizations/org-a/projects/project-a/membership-requests/"
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
            "/api/v1/organizations/org-a/projects/project-a/capability-requests",
            json={"capability_keys": ["datasets.read"], "reason": sensitive_reason},
            headers=_headers(alice_token, "alice-capability"),
        )
        assert capability.status_code == 201
        capability_path = (
            "/api/v1/organizations/org-a/projects/project-a/capability-requests/"
            + capability.json()["request_id"]
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
                "organization_id": "org-a",
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
            "/api/v1/organizations/org-a/projects/project-a/access-audit-events",
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


def test_account_notification_inbox_is_recipient_scoped_and_read_is_idempotent() -> None:
    repository = InMemoryAccessRepository()
    with _client(repository) as client:
        alice_token, _ = _register_and_login(client, "notification-alice")
        bob_token, _ = _register_and_login(client, "notification-bob")
        requested = client.post(
            "/api/v1/organizations/org-a/projects/project-a/membership-requests",
            json={"reason": "private reason must not reach inbox"},
            headers=_headers(alice_token, "notification-membership"),
        )
        assert requested.status_code == 201
        decision_path = (
            "/api/v1/organizations/org-a/projects/project-a/membership-requests/"
            + requested.json()["request_id"]
            + ":approve"
        )
        for key in ("notification-approve-1", "notification-approve-2"):
            approved = client.post(
                decision_path,
                json={"reason": "private manager decision"},
                headers=_headers("admin-a", key),
            )
            assert approved.status_code == 200

        unauthenticated = client.get("/api/v1/account/notifications/unread-count")
        assert unauthenticated.status_code == 401
        count = client.get(
            "/api/v1/account/notifications/unread-count", headers=_headers(alice_token)
        )
        assert count.status_code == 200
        assert count.headers["cache-control"] == "private, no-store"
        assert count.json() == {"unread_count": 1}

        listed = client.get("/api/v1/account/notifications?limit=20", headers=_headers(alice_token))
        assert listed.status_code == 200
        page = listed.json()
        assert page["next_cursor"] is None
        assert len(page["items"]) == 1
        notification = page["items"][0]
        assert notification["kind"] == "MEMBERSHIP_APPROVED"
        assert notification["resource_type"] == "ACCESS_REQUEST"
        assert notification["resource_id"] == requested.json()["request_id"]
        assert "access_request_id" not in notification
        assert notification["state"] == "UNREAD"
        assert "private" not in listed.text

        assert client.get("/api/v1/account/notifications", headers=_headers(bob_token)).json() == {
            "items": [],
            "next_cursor": None,
        }
        foreign_mark = client.post(
            f"/api/v1/account/notifications/{notification['notification_id']}:read",
            headers=_headers(bob_token),
        )
        assert foreign_mark.status_code == 404

        for _ in range(2):
            marked = client.post(
                f"/api/v1/account/notifications/{notification['notification_id']}:read",
                headers=_headers(alice_token),
            )
            assert marked.status_code == 204
            assert marked.headers["cache-control"] == "private, no-store"
        assert client.get(
            "/api/v1/account/notifications/unread-count", headers=_headers(alice_token)
        ).json() == {"unread_count": 0}
        assert sum(event.action == "account.notification.read" for event in repository._audit) == 1

        invalid_cursor = client.get(
            "/api/v1/account/notifications?cursor=this-is-not-a-cursor",
            headers=_headers(alice_token),
        )
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json()["code"] == "INVALID_NOTIFICATION_CURSOR"


def test_concurrent_duplicate_approval_has_one_effect_and_conflicting_decision_is_409() -> None:
    repository = InMemoryAccessRepository()
    service = AccessService(repository)
    principal = service.register(
        RegistrationCommand(
            username="concurrent-user", password="Concurrent safe passphrase 2026!"
        ),
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
        organization_ids=frozenset({"org-a"}),
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset(),
        roles=frozenset({Role.ADMIN.value}),
        scope_pairs=frozenset({("project-a", None)}),
        organization_scope_triples=frozenset({("org-a", "project-a", None)}),
    )
    requested = service.create_membership_request(
        auth=requester,
        organization_id="org-a",
        project_id="project-a",
        command=MembershipRequestCreate(reason="join"),
        idempotency_key="create",
        request_id="create",
    )

    def approve(index: int) -> str:
        return service.decide_membership_request(
            auth=administrator,
            organization_id="org-a",
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
            organization_id="org-a",
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
            "/api/v1/organizations/org-a/projects/project-a/membership-requests",
            json={"reason": "temporary"},
            headers=_headers(token, "membership-to-withdraw"),
        ).json()
        response = client.post(
            "/api/v1/organizations/org-a/projects/project-a/membership-requests/"
            + withdrawn_membership["request_id"]
            + ":withdraw",
            json={"reason": "changed mind"},
            headers=_headers(token, "withdraw-membership"),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "WITHDRAWN"

        rejected_membership = client.post(
            "/api/v1/organizations/org-a/projects/project-a/membership-requests",
            json={"reason": "new request"},
            headers=_headers(token, "membership-to-reject"),
        ).json()
        response = client.post(
            "/api/v1/organizations/org-a/projects/project-a/membership-requests/"
            + rejected_membership["request_id"]
            + ":reject",
            json={"reason": "not approved"},
            headers=_headers("admin-a", "reject-membership"),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "REJECTED"

        withdrawn_capability = client.post(
            "/api/v1/organizations/org-a/projects/project-a/capability-requests",
            json={"capability_keys": ["datasets.read"]},
            headers=_headers(token, "capability-to-withdraw"),
        ).json()
        response = client.post(
            "/api/v1/organizations/org-a/projects/project-a/capability-requests/"
            + withdrawn_capability["request_id"]
            + ":withdraw",
            json={"reason": "not needed"},
            headers=_headers(token, "withdraw-capability"),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "WITHDRAWN"

        rejected_capability = client.post(
            "/api/v1/organizations/org-a/projects/project-a/capability-requests",
            json={"capability_keys": ["datasets.write"]},
            headers=_headers(token, "capability-to-reject"),
        ).json()
        response = client.post(
            "/api/v1/organizations/org-a/projects/project-a/capability-requests/"
            + rejected_capability["request_id"]
            + ":reject",
            json={"reason": "not approved"},
            headers=_headers("admin-a", "reject-capability"),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "REJECTED"

        forbidden = client.post(
            "/api/v1/organizations/org-a/projects/project-a/capability-requests/"
            + rejected_capability["request_id"]
            + ":approve",
            json={"reason": "wrong project manager"},
            headers=_headers("admin-b", "cross-project-approve"),
        )
        assert forbidden.status_code == 403
        assert forbidden.headers["content-type"].startswith("application/problem+json")

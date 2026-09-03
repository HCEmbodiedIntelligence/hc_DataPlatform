from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.security.access_models import LoginCommand, RegistrationCommand
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.passwords import PasswordHasher, PasswordPolicy, ScryptParameters

_ORIGINAL_PASSWORD = "Correct horse battery 2026!"
_NEW_PASSWORD = "Different durable phrase 2027!"


def _service(repository: InMemoryAccessRepository | None = None) -> AccessService:
    return AccessService(
        repository or InMemoryAccessRepository(),
        password_hasher=PasswordHasher(ScryptParameters(n=2**10)),
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )


def _client(
    repository: InMemoryAccessRepository | None = None,
) -> tuple[TestClient, InMemoryAccessRepository]:
    resolved = repository or InMemoryAccessRepository()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory", _env_file=None),
        access_service=_service(resolved),
    )
    return TestClient(app), resolved


def _register_and_login(client: TestClient) -> tuple[str, str]:
    registration = client.post(
        "/api/v1/auth/registrations",
        json={"username": "account-owner", "password": _ORIGINAL_PASSWORD},
    )
    assert registration.status_code == 201
    first = client.post(
        "/api/v1/auth/sessions",
        json={"username": "account-owner", "password": _ORIGINAL_PASSWORD},
    )
    second = client.post(
        "/api/v1/auth/sessions",
        json={"username": "account-owner", "password": _ORIGINAL_PASSWORD},
    )
    assert first.status_code == second.status_code == 201
    return first.json()["access_token"], second.json()["access_token"]


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def test_default_password_policy_uses_six_character_minimum() -> None:
    policy = PasswordPolicy()

    assert policy.min_length == 6
    policy.require("12345678", canonical_username="hc-admin")
    with pytest.raises(ProblemException) as short:
        policy.require("12345", canonical_username="hc-admin")
    assert short.value.problem.details == {"violations": ["TOO_SHORT"]}


def test_password_policy_is_shared_by_registration_and_has_safe_boundaries() -> None:
    service = _service()
    with pytest.raises(ProblemException) as short:
        service.register(
            RegistrationCommand(username="short-policy", password="a" * 14),
            request_id="short",
        )
    assert short.value.problem.code == "PASSWORD_POLICY_VIOLATION"
    assert short.value.problem.details == {"violations": ["TOO_SHORT"]}

    accepted = service.register(
        RegistrationCommand(username="valid-policy", password="a" * 15),
        request_id="valid",
    )
    assert accepted.principal.username == "valid-policy"

    with pytest.raises(ProblemException) as blocked:
        service.register(
            RegistrationCommand(username="blocked-policy", password="123456789012345"),
            request_id="blocked",
        )
    assert blocked.value.problem.details == {"violations": ["BLOCKED_PASSWORD"]}

    with pytest.raises(ProblemException) as contextual:
        service.register(
            RegistrationCommand(
                username="context-name",
                password="safe-context-name-passphrase-2026",
            ),
            request_id="contextual",
        )
    assert contextual.value.problem.details == {"violations": ["CONTAINS_USERNAME"]}


def test_password_hash_parameters_are_bounded_and_legacy_scrypt_upgrades_once() -> None:
    repository = InMemoryAccessRepository(max_active_sessions=32)
    legacy_hasher = PasswordHasher(ScryptParameters(n=2**10))
    registration_service = AccessService(
        repository,
        password_hasher=legacy_hasher,
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )
    principal = registration_service.register(
        RegistrationCommand(username="legacy-user", password=_ORIGINAL_PASSWORD),
        request_id="legacy-register",
    ).principal
    original_hash = repository.credential_for_principal(principal.principal_id)
    assert original_hash is not None

    current_hasher = PasswordHasher(ScryptParameters(n=2**11))
    current_service = AccessService(
        repository,
        password_hasher=current_hasher,
        password_policy=PasswordPolicy(min_length=15, max_length=128),
    )

    def typed_login(index: int) -> int:
        return len(
            current_service.login(
                LoginCommand(username="legacy-user", password=_ORIGINAL_PASSWORD),
                request_id=f"legacy-login-{index}",
            ).access_token
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(executor.map(typed_login, range(16)))
    upgraded = repository.credential_for_principal(principal.principal_id)
    assert upgraded is not None
    assert upgraded.password_hash != original_hash.password_hash
    assert not current_hasher.needs_rehash(upgraded.password_hash)
    assert sum(event.action == "auth.password.rehashed" for event in repository._audit) == 1

    assert not current_hasher.verify(
        _ORIGINAL_PASSWORD,
        "scrypt$1073741824$8$1$c2FsdA==$ZGVyaXZlZA==",
    )
    assert not current_hasher.verify(_ORIGINAL_PASSWORD, "not-a-password-hash")


def test_profile_read_update_etag_idempotency_and_safe_audit() -> None:
    client, repository = _client()
    with client:
        token, _ = _register_and_login(client)
        auth = _headers(token)
        current = client.get("/api/v1/account/profile", headers=auth)
        assert current.status_code == 200
        assert current.headers["cache-control"] == "private, no-store"
        assert current.headers["etag"] == '"v1"'
        assert current.json()["profile"] == {
            "principal_id": current.json()["profile"]["principal_id"],
            "username": "account-owner",
            "display_name": "account-owner",
            "status": "ACTIVE",
            "created_at": current.json()["profile"]["created_at"],
            "updated_at": current.json()["profile"]["updated_at"],
            "password_changed_at": current.json()["profile"]["password_changed_at"],
            "revision": 1,
            "etag": '"v1"',
        }
        assert current.json()["password_policy"] == {
            "min_length": 15,
            "max_length": 128,
            "disallow_username": True,
            "blocked_password_count": 5,
        }

        mutation_headers = _headers(
            token,
            **{"If-Match": '"v1"', "Idempotency-Key": "profile-update-1"},
        )
        updated = client.patch(
            "/api/v1/account/profile",
            headers=mutation_headers,
            json={"display_name": "数据治理值班员"},
        )
        replay = client.patch(
            "/api/v1/account/profile",
            headers=mutation_headers,
            json={"display_name": "数据治理值班员"},
        )
        assert updated.status_code == replay.status_code == 200
        assert updated.json() == replay.json()
        assert updated.headers["etag"] == '"v2"'
        assert updated.json()["profile"]["username"] == "account-owner"
        assert updated.json()["profile"]["display_name"] == "数据治理值班员"

        reused = client.patch(
            "/api/v1/account/profile",
            headers=mutation_headers,
            json={"display_name": "different"},
        )
        assert reused.status_code == 409
        assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

        stale = client.patch(
            "/api/v1/account/profile",
            headers=_headers(
                token,
                **{"If-Match": '"v1"', "Idempotency-Key": "profile-update-stale"},
            ),
            json={"display_name": "stale edit"},
        )
        assert stale.status_code == 412
        assert stale.json()["code"] == "ETAG_MISMATCH"

        bootstrap = client.get("/api/v1/auth/session/bootstrap", headers=auth)
        assert bootstrap.json()["principal"]["display_name"] == "数据治理值班员"

    profile_events = [
        event for event in repository._audit if event.action == "account.profile.updated"
    ]
    assert len(profile_events) == 1
    assert profile_events[0].safe_details == {"changed_fields": ["display_name"]}
    serialized = repr(profile_events)
    assert "数据治理值班员" not in serialized
    assert _ORIGINAL_PASSWORD not in serialized


def test_password_change_is_atomic_replayable_and_revokes_other_sessions() -> None:
    client, repository = _client()
    with client:
        token, other_token = _register_and_login(client)
        current = client.get("/api/v1/account/profile", headers=_headers(token)).json()
        headers = _headers(
            token,
            **{
                "If-Match": current["profile"]["etag"],
                "Idempotency-Key": "password-change-1",
            },
        )

        wrong = client.post(
            "/api/v1/account/password:change",
            headers={**headers, "Idempotency-Key": "password-wrong"},
            json={"current_password": "Wrong current phrase 2026!", "new_password": _NEW_PASSWORD},
        )
        assert wrong.status_code == 422
        assert wrong.json()["code"] == "CURRENT_PASSWORD_INVALID"
        assert (
            client.get("/api/v1/auth/session/bootstrap", headers=_headers(other_token)).status_code
            == 200
        )

        changed = client.post(
            "/api/v1/account/password:change",
            headers=headers,
            json={"current_password": _ORIGINAL_PASSWORD, "new_password": _NEW_PASSWORD},
        )
        replay = client.post(
            "/api/v1/account/password:change",
            headers=headers,
            json={"current_password": _ORIGINAL_PASSWORD, "new_password": _NEW_PASSWORD},
        )
        assert changed.status_code == replay.status_code == 200
        assert changed.json() == replay.json()
        assert changed.json()["other_sessions_revoked"] == 1
        assert changed.headers["etag"] == '"v2"'
        assert _ORIGINAL_PASSWORD not in changed.text
        assert _NEW_PASSWORD not in changed.text

        assert (
            client.get("/api/v1/auth/session/bootstrap", headers=_headers(token)).status_code == 200
        )
        revoked = client.get("/api/v1/auth/session/bootstrap", headers=_headers(other_token))
        assert revoked.status_code == 401
        assert revoked.json()["code"] == "SESSION_INVALID"
        old_login = client.post(
            "/api/v1/auth/sessions",
            json={"username": "account-owner", "password": _ORIGINAL_PASSWORD},
        )
        new_login = client.post(
            "/api/v1/auth/sessions",
            json={"username": "account-owner", "password": _NEW_PASSWORD},
        )
        assert old_login.status_code == 401
        assert new_login.status_code == 201

    password_events = [
        event for event in repository._audit if event.action == "auth.password.changed"
    ]
    assert len(password_events) == 1
    assert password_events[0].safe_details == {"other_sessions_revoked": 1}
    serialized = repr(password_events)
    assert _ORIGINAL_PASSWORD not in serialized
    assert _NEW_PASSWORD not in serialized


def test_concurrent_profile_updates_have_one_cas_winner() -> None:
    client, _ = _client()
    with client:
        token, _ = _register_and_login(client)

        def update(index: int) -> int:
            return client.patch(
                "/api/v1/account/profile",
                headers=_headers(
                    token,
                    **{
                        "If-Match": '"v1"',
                        "Idempotency-Key": f"profile-concurrent-{index}",
                    },
                ),
                json={"display_name": f"并发资料 {index}"},
            ).status_code

        with ThreadPoolExecutor(max_workers=8) as executor:
            statuses = list(executor.map(update, range(16)))
        assert statuses.count(200) == 1
        assert statuses.count(412) == 15


def test_account_mutations_require_an_opaque_session_and_concurrency_headers() -> None:
    client, _ = _client()
    with client:
        token, _ = _register_and_login(client)
        assert client.get("/api/v1/account/profile").status_code == 401
        missing = client.patch(
            "/api/v1/account/profile",
            headers=_headers(token),
            json={"display_name": "missing headers"},
        )
        assert missing.status_code == 422
        assert missing.headers["content-type"].startswith("application/problem+json")

        malformed = client.patch(
            "/api/v1/account/profile",
            headers=_headers(
                token,
                **{"If-Match": "not-an-etag", "Idempotency-Key": "malformed-etag"},
            ),
            json={"display_name": "malformed etag"},
        )
        assert malformed.status_code == 412
        assert malformed.json()["code"] == "ETAG_MISMATCH"

        unknown_opaque = client.get(
            "/api/v1/account/profile",
            headers={"Authorization": "Bearer hcs_unknown-platform-session"},
        )
        assert unknown_opaque.status_code == 401

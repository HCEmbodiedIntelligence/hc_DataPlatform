from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from fastapi import Response
from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.security.access_models import ResolvedSession
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService


class _SessionMutationRecordingRepository(InMemoryAccessRepository):
    def __init__(self, clock: Callable[[], datetime]) -> None:
        super().__init__(
            clock=clock,
            session_touch_interval_seconds=1,
            session_idle_ttl_seconds=30,
            session_absolute_ttl_seconds=60,
        )
        self.mutation_decisions: list[bool] = []

    def resolve_session(
        self,
        token_hash: str,
        *,
        request_id: str | None = None,
        allow_session_mutation: bool = True,
    ) -> ResolvedSession | None:
        self.mutation_decisions.append(allow_session_mutation)
        return super().resolve_session(
            token_hash,
            request_id=request_id,
            allow_session_mutation=allow_session_mutation,
        )


def _settings() -> Settings:
    return Settings(
        environment="test",
        runtime_backend="memory",
        platform_environment_id="test-maintenance-http",
        _env_file=None,
    )


def test_read_only_environment_rejects_commands_but_keeps_queries_available() -> None:
    settings = _settings()
    gate = InMemoryMaintenanceWriteGate()
    gate.ensure_environment(settings.platform_environment_id)
    gate.set_mode(settings.platform_environment_id, "READ_ONLY_MAINTENANCE")
    app = create_app(settings=settings, maintenance_write_gate=gate)

    with TestClient(app) as client:
        live = client.get("/health/live")
        blocked = client.post("/api/v1/auth/registrations", json={})
        maintenance_control = client.post("/api/v1/platform/maintenance-operations/missing")

    assert live.status_code == 200
    assert blocked.status_code == 503
    assert blocked.json()["code"] == "PLATFORM_MAINTENANCE"
    assert blocked.json()["detail"] == "The environment is read-only for maintenance."
    assert blocked.headers["Retry-After"] == "10"
    assert blocked.headers["Cache-Control"] == "no-store"
    assert maintenance_control.status_code == 405
    assert maintenance_control.json()["code"] == "HTTP_405"


def test_epoch_change_during_command_replaces_success_with_maintenance_problem() -> None:
    settings = _settings()
    gate = InMemoryMaintenanceWriteGate()
    app = create_app(settings=settings, maintenance_write_gate=gate)

    @app.post("/test-only-write-race")
    def change_epoch_after_permit() -> Response:
        gate.set_mode(settings.platform_environment_id, "READ_ONLY_MAINTENANCE")
        return Response(status_code=204)

    with TestClient(app) as client:
        response = client.post("/test-only-write-race")

    assert response.status_code == 503
    assert response.json()["code"] == "PLATFORM_MAINTENANCE"
    assert response.headers["Retry-After"] == "10"


def test_read_only_opaque_auth_validates_without_session_writes() -> None:
    timestamp = [datetime(2026, 8, 28, tzinfo=timezone.utc)]
    repository = _SessionMutationRecordingRepository(lambda: timestamp[0])
    service = AccessService(repository)
    principal = repository.register_account(
        canonical_username="maintenance-reader",
        display_username="maintenance-reader",
        password_hash="test-only-hash",
        request_id="register-maintenance-reader",
    )
    credential = repository.credential_for_principal(principal.principal_id)
    assert credential is not None
    token = f"{AccessService.TOKEN_PREFIX}maintenance-reader-token"
    token_hash = service.token_hash(token)
    repository.create_session(
        principal_id=principal.principal_id,
        token_hash=token_hash,
        expected_password_hash=credential.password_hash,
        expected_credential_revision=credential.credential_revision,
        request_id="create-maintenance-reader-session",
    )
    issued_at = repository._sessions[token_hash].last_seen_at

    settings = _settings()
    gate = InMemoryMaintenanceWriteGate()
    app = create_app(
        settings=settings,
        access_service=service,
        maintenance_write_gate=gate,
    )
    timestamp[0] += timedelta(seconds=2)
    with TestClient(app) as client:
        writable = client.get(
            "/api/v1/auth/session/bootstrap",
            headers={"Authorization": f"Bearer {token}"},
        )
        touched_at = repository._sessions[token_hash].last_seen_at
        gate.set_mode(settings.platform_environment_id, "READ_ONLY_MAINTENANCE")
        timestamp[0] += timedelta(seconds=2)
        read_only = client.get(
            "/api/v1/auth/session/bootstrap",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert writable.status_code == 200
    assert touched_at == issued_at + timedelta(seconds=2)
    assert read_only.status_code == 200
    assert repository._sessions[token_hash].last_seen_at == touched_at
    assert repository.mutation_decisions == [True, True, False, False]


def test_upload_grant_retains_writer_inventory_until_its_expiry() -> None:
    timestamp = [datetime(2026, 8, 28, tzinfo=timezone.utc)]
    settings = _settings()
    gate = InMemoryMaintenanceWriteGate(clock=lambda: timestamp[0])
    storage = InMemoryObjectStorage()
    upload_id = storage.create_multipart("project-a/rollout-a/source.mcap")
    app = create_app(settings=settings, maintenance_write_gate=gate)

    @app.post("/test-only-upload-grant")
    def issue_upload_grant() -> dict[str, str]:
        return {
            "url": storage.presign_part(
                "project-a/rollout-a/source.mcap",
                upload_id,
                1,
                20,
            )
        }

    with TestClient(app) as client:
        response = client.post("/test-only-upload-grant")

    assert response.status_code == 200
    inventory = gate.writer_inventory(settings.platform_environment_id)
    assert [(item.writer_kind, item.active_count) for item in inventory] == [
        ("presigned_upload_grant", 1)
    ]

    gate.set_mode(settings.platform_environment_id, "READ_ONLY_MAINTENANCE")
    timestamp[0] += timedelta(seconds=29)
    assert gate.writer_inventory(settings.platform_environment_id)
    timestamp[0] += timedelta(seconds=1)
    assert gate.writer_inventory(settings.platform_environment_id) == ()

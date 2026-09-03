from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.platform_ops.runtime_config import (
    RUNTIME_CONFIG_ALLOWLIST,
    InMemoryRuntimeConfigRepository,
    PollingRuntimeConfigSubscriber,
    RuntimeConfigError,
    RuntimeConfigService,
    RuntimeConfigState,
    RuntimeConfigSynchronizer,
    RuntimeConfigValues,
    runtime_config_boot_values,
)
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_OPERATIONS_READ,
    CAPABILITY_PLATFORM_RELEASE_OPERATE,
)
from hc_data_platform.security.http import require_auth_context

NOW = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)


def _auth(capability: str | None) -> AuthContext:
    return AuthContext(
        subject_id="runtime-config-operator",
        project_ids=frozenset(),
        region_codes=frozenset(),
        capabilities=frozenset({capability}) if capability else frozenset(),
    )


def _settings() -> Settings:
    return Settings(
        environment="test",
        runtime_backend="memory",
        platform_environment_id="runtime-config-test",
        _env_file=None,
    )


def test_allowlist_is_exact_and_rejects_unknown_secret_release_and_invalid_values() -> None:
    assert RUNTIME_CONFIG_ALLOWLIST == (
        "scheduling.media_maintenance_interval_seconds",
        "scheduling.storage_inventory_interval_seconds",
        "ui.maintenance_banner_enabled",
    )
    repository = InMemoryRuntimeConfigRepository(clock=lambda: NOW)

    for patch, code in (
        ({"database.password": "do-not-store"}, "PLATFORM_RUNTIME_CONFIG_FORBIDDEN_KEY"),
        ({"postgres_dsn": "do-not-store"}, "PLATFORM_RUNTIME_CONFIG_FORBIDDEN_KEY"),
        ({"tls.certificate": "do-not-store"}, "PLATFORM_RUNTIME_CONFIG_FORBIDDEN_KEY"),
        ({"component.image": "do-not-store"}, "PLATFORM_RUNTIME_CONFIG_FORBIDDEN_KEY"),
        ({"feature.unknown": True}, "PLATFORM_RUNTIME_CONFIG_KEY_NOT_ALLOWLISTED"),
        (
            {"scheduling.media_maintenance_interval_seconds": True},
            "PLATFORM_RUNTIME_CONFIG_VALUE_INVALID",
        ),
        (
            {"scheduling.media_maintenance_interval_seconds": 9},
            "PLATFORM_RUNTIME_CONFIG_VALUE_INVALID",
        ),
    ):
        with pytest.raises(RuntimeConfigError) as raised:
            repository.publish(
                "production-a",
                expected_revision=0,
                schema_version="hc-runtime-config/v1",
                patch=patch,
                actor_id="operator-a",
                reason="bounded runtime update",
                request_id="request-a",
            )
        assert raised.value.code == code
    assert repository.current("production-a").revision == 0


def test_revision_zero_preserves_release_boot_intervals_without_rounding() -> None:
    values = runtime_config_boot_values(
        media_maintenance_interval_seconds=900.0,
        storage_inventory_interval_seconds=7_200.0,
    )
    repository = InMemoryRuntimeConfigRepository(baseline_values=values)
    assert repository.current("production-a").values == {
        "scheduling.media_maintenance_interval_seconds": 900,
        "scheduling.storage_inventory_interval_seconds": 7_200,
        "ui.maintenance_banner_enabled": False,
    }
    with pytest.raises(ValueError, match="whole seconds"):
        runtime_config_boot_values(
            media_maintenance_interval_seconds=10.5,
            storage_inventory_interval_seconds=3_600,
        )


def test_revision_compare_and_swap_history_and_rollback_are_monotonic() -> None:
    repository = InMemoryRuntimeConfigRepository(clock=lambda: NOW)
    first = repository.publish(
        "production-a",
        expected_revision=0,
        schema_version="hc-runtime-config/v1",
        patch={"ui.maintenance_banner_enabled": True},
        actor_id="operator-a",
        reason="enable planned maintenance banner",
        request_id="request-a",
    )
    second = repository.publish(
        "production-a",
        expected_revision=1,
        schema_version="hc-runtime-config/v1",
        patch={"scheduling.media_maintenance_interval_seconds": 600},
        actor_id="operator-b",
        reason="reduce media maintenance frequency",
        request_id="request-b",
    )
    rolled_back = repository.rollback(
        "production-a",
        target_revision=1,
        expected_revision=2,
        actor_id="operator-c",
        reason="restore prior verified runtime values",
        request_id="request-c",
    )

    assert (first.revision, second.revision, rolled_back.revision) == (1, 2, 3)
    assert rolled_back.rollback_of_revision == 1
    assert rolled_back.values == first.values
    assert rolled_back.content_sha256 == first.content_sha256
    assert repository.history("production-a", limit=10).revisions == (
        rolled_back,
        second,
        first,
    )
    with pytest.raises(RuntimeConfigError) as raised:
        repository.publish(
            "production-a",
            expected_revision=2,
            schema_version="hc-runtime-config/v1",
            patch={"ui.maintenance_banner_enabled": False},
            actor_id="operator-a",
            reason="stale conflicting runtime update",
            request_id="request-d",
        )
    assert raised.value.code == "PLATFORM_RUNTIME_CONFIG_REVISION_CONFLICT"


@pytest.mark.asyncio
async def test_lost_notification_converges_through_bounded_polling() -> None:
    repository = InMemoryRuntimeConfigRepository(clock=lambda: NOW)
    writer = RuntimeConfigService(
        repository,
        RuntimeConfigState("production-a"),
        "production-a",
    )
    follower_state = RuntimeConfigState("production-a")
    follower = RuntimeConfigService(repository, follower_state, "production-a")
    synchronizer = RuntimeConfigSynchronizer(
        follower,
        PollingRuntimeConfigSubscriber(),
        poll_interval_seconds=0.05,
    )
    await synchronizer.start()
    try:
        writer.publish(
            expected_revision=0,
            schema_version="hc-runtime-config/v1",
            patch={"scheduling.storage_inventory_interval_seconds": 1_800},
            actor_id="operator-a",
            reason="increase storage inventory observation rate",
            request_id="request-a",
        )

        async def wait_for_revision() -> None:
            while follower_state.revision != 1:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_revision(), timeout=0.5)
    finally:
        await synchronizer.stop()
    assert follower_state.value("scheduling.storage_inventory_interval_seconds") == 1_800


def test_runtime_config_api_enforces_exact_capabilities_and_audits_no_values() -> None:
    gate = InMemoryMaintenanceWriteGate()
    app = create_app(settings=_settings(), maintenance_write_gate=gate)

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/v1/platform/runtime-config").status_code == 401

        app.dependency_overrides[require_auth_context] = lambda: _auth(
            CAPABILITY_PLATFORM_OPERATIONS_READ
        )
        current = client.get("/api/v1/platform/runtime-config")
        denied = client.post(
            "/api/v1/platform/runtime-config/revisions",
            json={
                "expected_revision": 0,
                "schema_version": "hc-runtime-config/v1",
                "values": {"ui.maintenance_banner_enabled": True},
                "reason": "enable planned maintenance banner",
            },
        )

        app.dependency_overrides[require_auth_context] = lambda: _auth(
            CAPABILITY_PLATFORM_RELEASE_OPERATE
        )
        published = client.post(
            "/api/v1/platform/runtime-config/revisions",
            json={
                "expected_revision": 0,
                "schema_version": "hc-runtime-config/v1",
                "values": {"ui.maintenance_banner_enabled": True},
                "reason": "enable planned maintenance banner",
            },
        )
        rejected = client.post(
            "/api/v1/platform/runtime-config/revisions",
            json={
                "expected_revision": 1,
                "schema_version": "hc-runtime-config/v1",
                "values": {"database.password": "sensitive-sentinel"},
                "reason": "attempt prohibited database change",
            },
        )
        rolled_back = client.post(
            "/api/v1/platform/runtime-config/revisions/0:rollback",
            json={
                "expected_revision": 1,
                "reason": "restore code baseline runtime values",
            },
        )

    assert current.status_code == 200
    assert current.headers["Cache-Control"] == "private, no-store"
    assert denied.status_code == 403
    assert published.status_code == 201
    assert published.json()["revision"] == 1
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "PLATFORM_RUNTIME_CONFIG_FORBIDDEN_KEY"
    assert "sensitive-sentinel" not in rejected.text
    assert rolled_back.status_code == 201
    assert rolled_back.json()["revision"] == 2
    assert rolled_back.json()["rollback_of_revision"] == 0
    serialized_audit = " ".join(
        event.model_dump_json() for event in gate.platform_audit_events
    ).lower()
    assert "sensitive-sentinel" not in serialized_audit
    assert "database.password" not in serialized_audit
    assert any(
        event.action == "platform.runtime_config.published" and event.outcome == "SUCCEEDED"
        for event in gate.platform_audit_events
    )


def test_retired_preview_interval_is_not_part_of_runtime_config() -> None:
    with pytest.raises(ValidationError):
        RuntimeConfigValues.model_validate({"scheduling.preview_gc_interval_seconds": 450})

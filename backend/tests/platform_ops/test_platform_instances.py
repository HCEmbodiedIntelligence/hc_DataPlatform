from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.openapi import aggregate_fragments, formal_runtime_contract_issues
from hc_data_platform.platform_control.release_identity import release_identity_from_settings
from hc_data_platform.platform_ops.instances import (
    InMemoryPlatformInstanceRepository,
    InstanceReadinessSummary,
    InstanceRole,
    PlatformInstanceIdentity,
    PlatformInstanceService,
    platform_instance_identity,
)
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_ADMIN,
    CAPABILITY_PLATFORM_OPERATIONS_READ,
)
from hc_data_platform.security.http import require_auth_context

NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
DIGEST = f"sha256:{'a' * 64}"
BACKEND = Path(__file__).resolve().parents[2]


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "runtime_backend": "memory",
        "release_id": "platform-v0.1.0-test.1",
        "git_commit": "1" * 40,
        "release_manifest_digest": DIGEST,
        "migration_manifest_digest": DIGEST,
        "component_image_digest": DIGEST,
        "component_role": "api",
        "_env_file": None,
    }
    values.update(updates)
    return Settings(**values)  # type: ignore[arg-type]


def _identity(instance_id: str, *, role: InstanceRole = "api") -> PlatformInstanceIdentity:
    return PlatformInstanceIdentity(
        instance_id=UUID(instance_id),
        node_name=f"node-{role}",
        role=role,
        release_id="platform-v0.1.0-test.1",
        release_manifest_digest=DIGEST,
        component_image_digest=DIGEST,
        runtime_version="CPython 3.12.11",
        pod_name=f"pod-{role}",
        kubernetes_node_name="machine-a",
        kubernetes_zone="cn-east-1a",
    )


def _auth(*, capability: str | None) -> AuthContext:
    return AuthContext(
        subject_id="platform-operator",
        project_ids=frozenset(),
        region_codes=frozenset(),
        capabilities=(frozenset({capability}) if capability is not None else frozenset()),
    )


def test_runtime_identity_uses_pod_uid_and_generates_a_new_local_instance_per_start() -> None:
    pod_uid = UUID("00000000-0000-4000-8000-000000000001")
    configured = _settings(
        instance_id=pod_uid,
        node_name="api-pod-7",
        pod_name="api-pod-7",
        kubernetes_node_name="worker-node-2",
        kubernetes_zone="cn-east-1a",
    )
    identity = platform_instance_identity(configured, release_identity_from_settings(configured))
    assert identity.model_dump() == {
        "instance_id": pod_uid,
        "node_name": "api-pod-7",
        "role": "api",
        "release_id": "platform-v0.1.0-test.1",
        "release_manifest_digest": DIGEST,
        "component_image_digest": DIGEST,
        "runtime_version": identity.runtime_version,
        "pod_name": "api-pod-7",
        "kubernetes_node_name": "worker-node-2",
        "kubernetes_zone": "cn-east-1a",
    }

    local = _settings(instance_id=None, node_name="local-api")
    first = platform_instance_identity(local, release_identity_from_settings(local))
    second = platform_instance_identity(local, release_identity_from_settings(local))
    assert first.instance_id != second.instance_id
    assert (first.node_name, second.node_name) == ("local-api", "local-api")


def test_heartbeat_upserts_and_strictly_marks_stale_after_ninety_seconds() -> None:
    now = {"value": NOW}
    repository = InMemoryPlatformInstanceRepository(clock=lambda: now["value"])
    service = PlatformInstanceService(
        repository,
        _identity("00000000-0000-4000-8000-000000000001"),
    )
    first = service.heartbeat(InstanceReadinessSummary(status="starting"))
    now["value"] += timedelta(seconds=30)
    second = service.heartbeat(InstanceReadinessSummary(status="ready"))

    assert second.instance_id == first.instance_id
    assert second.started_at == first.started_at
    assert second.last_heartbeat_at == NOW + timedelta(seconds=30)
    assert second.readiness.status == "ready"

    now["value"] = second.last_heartbeat_at + timedelta(seconds=90)
    assert service.list_instances().instances[0].stale is False
    now["value"] += timedelta(microseconds=1)
    assert service.list_instances(stale=True).instances[0].stale is True
    assert service.list_instances(stale=False).count == 0


def test_directory_requires_global_platform_admin_and_filters_without_secret_fields() -> None:
    repository = InMemoryPlatformInstanceRepository(clock=lambda: NOW)
    service = PlatformInstanceService(
        repository,
        _identity("00000000-0000-4000-8000-000000000002", role="worker"),
    )
    service.heartbeat(InstanceReadinessSummary(status="ready"))
    app = create_app(settings=_settings())
    app.state.platform_instance_service = service

    with TestClient(app, raise_server_exceptions=False) as client:
        anonymous = client.get("/api/v1/platform/instances")
        assert anonymous.status_code == 401

        app.dependency_overrides[require_auth_context] = lambda: _auth(capability=None)
        denied = client.get("/api/v1/platform/instances")
        assert denied.status_code == 403
        assert denied.json()["code"] == "PLATFORM_CAPABILITY_REQUIRED"

        app.dependency_overrides[require_auth_context] = lambda: _auth(
            capability=CAPABILITY_PLATFORM_OPERATIONS_READ
        )
        response = client.get("/api/v1/platform/instances?stale=false&role=worker")

        app.dependency_overrides[require_auth_context] = lambda: _auth(
            capability=CAPABILITY_PLATFORM_ADMIN
        )
        admin_response = client.get("/api/v1/platform/instances?stale=false&role=worker")

    assert response.status_code == 200
    assert admin_response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    payload = response.json()
    assert payload["heartbeat_interval_seconds"] == 30
    assert payload["stale_after_seconds"] == 90
    assert payload["count"] == 1
    assert payload["instances"][0]["role"] == "worker"
    serialized = response.text.lower()
    assert "password" not in serialized
    assert "secret" not in serialized
    assert "postgres" not in serialized
    audit_sink = app.state.maintenance_write_gate
    assert isinstance(audit_sink, InMemoryMaintenanceWriteGate)
    assert [(event.action, event.outcome) for event in audit_sink.platform_audit_events] == [
        ("platform.instances.list", "DENIED"),
        ("platform.instances.listed", "SUCCEEDED"),
        ("platform.instances.listed", "SUCCEEDED"),
    ]


def test_directory_openapi_is_bearer_protected_and_formally_bounded() -> None:
    document = create_app(settings=_settings()).openapi()
    operation = document["paths"]["/api/v1/platform/instances"]["get"]
    assert operation["operationId"] == "listPlatformInstances"
    assert operation["security"] == [{"bearerAuth": []}]
    assert operation["x-hc-platform-capability-policy"] == {
        "mode": "any-exact",
        "capabilities": ["platform.admin", "platform.operations.read"],
    }
    page = document["components"]["schemas"]["PlatformInstancePage"]
    assert page["properties"]["heartbeat_interval_seconds"]["const"] == 30
    assert page["properties"]["stale_after_seconds"]["const"] == 90
    formal = aggregate_fragments(BACKEND / "openapi")
    assert formal["paths"]["/api/v1/platform/instances"]["get"]["security"] == [{"bearerAuth": []}]
    policies = {
        (path, method): operation["x-hc-platform-capability-policy"]
        for path, path_item in formal["paths"].items()
        if path.startswith("/api/v1/platform/maintenance-operations")
        for method, operation in path_item.items()
    }
    operation_policy = {
        "mode": "operation-kind-exact",
        "mapping": {
            "BACKUP": "platform.maintenance.operate",
            "OTHER": "platform.maintenance.operate",
            "MIGRATION": "platform.release.operate",
            "RELEASE": "platform.release.operate",
            "RESTORE": "platform.break_glass",
        },
    }
    assert policies[("/api/v1/platform/maintenance-operations", "post")] == operation_policy
    assert policies[
        ("/api/v1/platform/maintenance-operations/{operation_id}:takeover", "post")
    ] == {
        "mode": "one-exact",
        "capabilities": ["platform.break_glass"],
    }
    assert policies[
        ("/api/v1/platform/maintenance-operations/{operation_id}:reconcile", "post")
    ] == {
        "mode": "one-exact",
        "capabilities": ["platform.maintenance.verify"],
    }
    assert policies[
        ("/api/v1/platform/maintenance-operations/{operation_id}:transition", "post")
    ] == {
        **operation_policy,
        "break_glass_when": [
            "current_state=FAILED_READ_ONLY",
            "next_state=SUCCEEDED",
        ],
    }
    assert formal_runtime_contract_issues(formal, document) == ()

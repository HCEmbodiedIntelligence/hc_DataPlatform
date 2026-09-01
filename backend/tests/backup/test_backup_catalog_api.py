from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient

from hc_data_platform.backup.catalog import (
    BackupCatalogAuditContext,
    BackupCatalogService,
    InMemoryBackupCatalogRepository,
)
from hc_data_platform.backup.contracts import canonical_json_bytes, verify_signed_manifest
from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import CAPABILITY_PLATFORM_OPERATIONS_READ
from hc_data_platform.security.http import require_auth_context

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_MANIFEST = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"
DIGEST = f"sha256:{'a' * 64}"


def _settings() -> Settings:
    return Settings(
        environment="test",
        runtime_backend="memory",
        platform_environment_id="production-cn-east",
        release_id="platform-v0.1.0-test.1",
        git_commit="1" * 40,
        release_manifest_digest=DIGEST,
        migration_manifest_digest=DIGEST,
        component_image_digest=DIGEST,
        component_role="api",
        _env_file=None,
    )


def _auth(capability: str | None) -> AuthContext:
    return AuthContext(
        subject_id="catalog-viewer",
        project_ids=frozenset(),
        region_codes=frozenset(),
        capabilities=frozenset() if capability is None else frozenset({capability}),
    )


def _catalog() -> InMemoryBackupCatalogRepository:
    fixture = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    public_key = Ed25519PublicKey.from_public_bytes(
        base64.urlsafe_b64decode(fixture["public_key_base64url"] + "==")
    )
    verified = verify_signed_manifest(
        canonical_json_bytes(fixture["manifest"]),
        canonical_json_bytes(fixture["signature"]),
        public_key=public_key,
    )
    repository = InMemoryBackupCatalogRepository()
    BackupCatalogService(
        repository,
        trusted_signing_key_sha256=frozenset({verified.manifest.signature.public_key_sha256}),
        trusted_repository_ids=frozenset({verified.manifest.backup_repository.repository_id}),
    ).import_created_manifest(
        verified,
        creation_operation_id="backup-create-api-001",
        audit=BackupCatalogAuditContext(actor_id="backup-job", request_id="create-api-001"),
    )
    return repository


def test_backup_catalog_api_is_exact_capability_scoped_redacted_and_cursor_bounded() -> None:
    repository = _catalog()
    audit_sink = InMemoryMaintenanceWriteGate()
    app = create_app(
        settings=_settings(),
        maintenance_write_gate=audit_sink,
        backup_catalog_repository=repository,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/v1/platform/backups").status_code == 401

        app.dependency_overrides[require_auth_context] = lambda: _auth(None)
        denied = client.get("/api/v1/platform/backups")
        assert denied.status_code == 403
        assert denied.json()["code"] == "PLATFORM_CAPABILITY_REQUIRED"

        app.dependency_overrides[require_auth_context] = lambda: _auth(
            CAPABILITY_PLATFORM_OPERATIONS_READ
        )
        response = client.get("/api/v1/platform/backups?status=INTEGRITY_VERIFIED&limit=1")
        malformed = client.get("/api/v1/platform/backups?cursor=not%2Ba%2Bcursor")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["count"] == 1
    assert response.json()["items"][0]["backup_id"] == "backup-20260828-001"
    assert response.json()["items"][0]["status"] == "INTEGRITY_VERIFIED"
    lowered = response.text.lower()
    for forbidden in ("manifest_uri", "signature_sha256", "kms://", "isolated-backups"):
        assert forbidden not in lowered
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "BACKUP_CATALOG_CURSOR_INVALID"
    assert [(event.action, event.outcome) for event in audit_sink.platform_audit_events] == [
        ("platform.backups.list", "DENIED"),
        ("platform.backups.listed", "SUCCEEDED"),
    ]


def test_backup_catalog_runtime_openapi_uses_bearer_and_exact_read_capability() -> None:
    document = create_app(settings=_settings()).openapi()
    operation = document["paths"]["/api/v1/platform/backups"]["get"]
    assert operation["operationId"] == "listPlatformBackups"
    assert operation["security"] == [{"bearerAuth": []}]
    assert operation["x-hc-platform-capability-policy"] == {
        "mode": "any-exact",
        "capabilities": ["platform.admin", "platform.operations.read"],
    }
    page = document["components"]["schemas"]["BackupCatalogPage"]
    assert page["additionalProperties"] is False
    assert page["properties"]["next_cursor"]["anyOf"][0]["maxLength"] == 1024

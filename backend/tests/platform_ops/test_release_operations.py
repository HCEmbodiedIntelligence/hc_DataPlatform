from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from hc_data_platform.backup.catalog import (
    BackupCatalogListItem,
    BackupCatalogPage,
    BackupCatalogRepository,
)
from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.platform_control.release_contract import AdjacentReleaseEdgeV1
from hc_data_platform.platform_control.release_feed import (
    PlatformReleaseFeedV1,
    PlatformReleaseManifestV1,
    ReleaseFeedCandidateV1,
    ReleaseImagesV1,
    canonical_json_bytes,
    sign_release_feed,
)
from hc_data_platform.platform_ops.instances import (
    InstanceReadinessSummary,
    PlatformInstanceIdentity,
    PlatformInstanceService,
)
from hc_data_platform.platform_ops.logs import InMemoryPlatformLogRepository
from hc_data_platform.platform_ops.maintenance import InMemoryMaintenanceWriteGate
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.capabilities import (
    CAPABILITY_PLATFORM_OPERATIONS_READ,
    CAPABILITY_PLATFORM_RELEASE_OPERATE,
)
from hc_data_platform.security.http import require_auth_context

NOW = datetime(2026, 8, 29, 10, tzinfo=timezone.utc)
A = "a" * 64
B = "b" * 64
SOURCE = ReleaseImagesV1(
    frontend=f"registry/frontend@sha256:{A}",
    api=f"registry/api@sha256:{A}",
    worker=f"registry/worker@sha256:{A}",
    media_worker=f"registry/media-worker@sha256:{A}",
)
TARGET = ReleaseImagesV1(
    frontend=f"registry/frontend@sha256:{B}",
    api=f"registry/api@sha256:{B}",
    worker=f"registry/worker@sha256:{B}",
    media_worker=f"registry/media-worker@sha256:{B}",
)


class BackupRepository:
    def list_page(self, **_: object) -> BackupCatalogPage:
        return BackupCatalogPage(
            observed_at=NOW,
            count=1,
            items=(
                BackupCatalogListItem(
                    backup_id="release-backup-001",
                    format_version="hc-platform-backup/v1",
                    mode="portable",
                    status="RESTORE_VERIFIED",
                    source_environment_id="test-environment",
                    repository_id="safe-repository",
                    release_manifest_sha256=A,
                    backup_created_at=NOW - timedelta(hours=2),
                    backup_completed_at=NOW - timedelta(hours=1),
                    status_occurred_at=NOW - timedelta(minutes=30),
                ),
            ),
            next_cursor=None,
        )


def _edge() -> AdjacentReleaseEdgeV1:
    return AdjacentReleaseEdgeV1.model_validate(
        {
            "edge_id": "0.1.0-to-0.1.1",
            "source_version": "0.1.0",
            "target_version": "0.1.1",
            "release_type": "patch",
            "status": "READY_FOR_CANARY",
            "target_artifacts_built_and_signed": True,
            "database": {
                "change_mode": "expand_only",
                "source_migration_count": 108,
                "source_manifest_sha256": A,
                "target_migration_count": 109,
                "target_manifest_sha256": B,
                "expand_migrations": ["platform/008-release.sql"],
                "data_migration": "none",
                "contract_migrations": [],
                "old_app_on_target_schema": "PASS",
                "new_app_on_source_schema": "PASS",
                "database_downgrade_allowed": False,
            },
            "temporal": {
                "source_patch_ids": [],
                "target_patch_ids": [],
                "added_patch_ids": [],
                "removed_patch_ids": [],
                "existing_history_replay": "PASS",
                "source_worker_on_target_history": "PASS",
                "target_worker_on_source_history": "PASS",
                "worker_build_id_routing": "ENABLED",
                "production_server_version_range": ">=1.25,<2",
            },
            "api": {
                "source_openapi_sha256": A,
                "target_openapi_sha256": B,
                "old_client_on_target_server": "PASS",
                "target_client_on_source_server": "PASS",
                "removed_operations": [],
                "removed_schema_fields": [],
                "generated_client_drift_check": "required",
            },
            "configuration": {
                "source_values_sha256": A,
                "target_values_sha256": B,
                "added_required_keys_without_defaults": [],
                "removed_keys": [],
                "old_app_with_target_config": "PASS",
                "target_app_with_source_config": "PASS",
                "secret_key_removal_allowed": False,
            },
            "rollback": {
                "before_contract_application": "application_digest_rollback_allowed",
                "after_contract_application": "not_applicable_no_contract_migration",
                "exact_source_artifacts_required": True,
                "database_down_migration_allowed": False,
                "temporal_source_build_must_remain_available": True,
            },
            "release_blockers": [],
        }
    )


def _signed_feed(private_key: Ed25519PrivateKey):
    manifest = PlatformReleaseManifestV1(
        release_id="platform-v0.1.1",
        semantic_version="0.1.1",
        git_commit="1" * 40,
        chart_version="0.1.1",
        images=TARGET,
        database={
            "migration_count": 109,
            "migration_manifest_sha256": B,
            "expand_migrations": ["platform/008-release.sql"],
            "contract_migrations": [],
        },
        openapi_sha256=B,
        generated_client_sha256=B,
        configuration_sha256=B,
        temporal={
            "server_version_range": ">=1.25,<2",
            "task_queues": ["hc-data-pipeline", "hc-media-pipeline"],
            "source_build_id": "platform-v0.1.0",
            "target_build_id": "platform-v0.1.1",
            "patch_set_sha256": B,
        },
        supply_chain={
            "sbom_sha256": B,
            "provenance_sha256": B,
            "chart_package_sha256": B,
            "dr_evidence_bundle_sha256": B,
        },
        minimum_source_version="0.1.0",
        published_at=NOW,
    )
    candidate = ReleaseFeedCandidateV1(
        manifest_sha256=hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        manifest=manifest,
        compatibility=_edge(),
    )
    return sign_release_feed(
        PlatformReleaseFeedV1(sequence=4, generated_at=NOW, candidates=(candidate,)),
        private_key=private_key,
    )


def _auth(actor_id: str) -> AuthContext:
    return AuthContext(
        subject_id=actor_id,
        project_ids=frozenset(),
        region_codes=frozenset(),
        capabilities=frozenset(
            {CAPABILITY_PLATFORM_RELEASE_OPERATE, CAPABILITY_PLATFORM_OPERATIONS_READ}
        ),
    )


def _app(private_key: Ed25519PrivateKey):
    encoded_key = (
        base64.urlsafe_b64encode(private_key.public_key().public_bytes_raw()).decode().rstrip("=")
    )
    settings = Settings(
        environment="test",
        runtime_backend="memory",
        platform_environment_id="test-environment",
        release_id="platform-v0.1.0",
        git_commit="1" * 40,
        release_manifest_digest=f"sha256:{A}",
        migration_manifest_digest=f"sha256:{A}",
        component_image_digest=f"sha256:{A}",
        release_feed_trusted_public_keys=(encoded_key,),
        cursor_secret="release-operation-test-secret",
        _env_file=None,
    )
    app = create_app(
        settings=settings,
        backup_catalog_repository=cast(BackupCatalogRepository, BackupRepository()),
        platform_log_repository=InMemoryPlatformLogRepository(),
        maintenance_write_gate=InMemoryMaintenanceWriteGate(),
    )
    instance_service = cast(PlatformInstanceService, app.state.platform_instance_service)
    repository = instance_service.repository
    for index, role in enumerate(("frontend", "api", "worker", "media-worker"), 1):
        identity = PlatformInstanceIdentity(
            instance_id=UUID(int=index),
            node_name=f"node-{index}",
            role=role,
            release_id="platform-v0.1.0",
            release_manifest_digest=f"sha256:{A}",
            component_image_digest=f"sha256:{A}",
            runtime_version="test",
        )
        PlatformInstanceService(repository, identity).heartbeat(
            InstanceReadinessSummary(status="ready")
        )
    return app


def test_signed_preflight_requires_distinct_manual_approval_and_redacts_history() -> None:
    key = Ed25519PrivateKey.generate()
    app = _app(key)
    actor: dict[str, str] = {"id": "release-requester"}
    app.dependency_overrides[require_auth_context] = lambda: _auth(actor["id"])
    body: dict[str, Any] = {
        "target_release_id": "platform-v0.1.1",
        "minimum_feed_sequence": 4,
        "source_images": SOURCE.model_dump(),
        "signed_feed": _signed_feed(key).model_dump(mode="json"),
    }
    with TestClient(app) as client:
        app.state.platform_instance_service.heartbeat(InstanceReadinessSummary(status="ready"))
        preflight = client.post("/api/v1/platform/releases:preflight", json=body)
        self_approval = client.post(
            "/api/v1/platform/releases/platform-v0.1.1:approve",
            json={"expected_state_version": 1, "reason": "approve tested release"},
        )
        actor["id"] = "distinct-release-approver"
        approval = client.post(
            "/api/v1/platform/releases/platform-v0.1.1:approve",
            json={"expected_state_version": 1, "reason": "approve tested release"},
        )
        transitions = []
        for expected, next_state, reason_code in (
            (2, "EXPAND", "EXPAND_MIGRATIONS_APPLIED"),
            (3, "CANARY", "CANARY_STARTED"),
            (4, "ROLLOUT", "CANARY_SLO_PASSED"),
            (5, "COMPLETED", "ROLLOUT_CONVERGED"),
        ):
            transitions.append(
                client.post(
                    "/api/v1/platform/releases/platform-v0.1.1:transition",
                    json={
                        "expected_state_version": expected,
                        "next_state": next_state,
                        "reason_code": reason_code,
                    },
                )
            )
        history = client.get("/api/v1/platform/releases")

    assert preflight.status_code == 200, preflight.text
    assert preflight.json()["state"] == "AWAITING_APPROVAL"
    assert self_approval.status_code == 409
    assert self_approval.json()["code"] == "RELEASE_DISTINCT_APPROVER_REQUIRED"
    assert approval.status_code == 200
    assert approval.json()["state"] == "APPROVED"
    assert [response.status_code for response in transitions] == [200, 200, 200, 200]
    assert transitions[-1].json()["state"] == "COMPLETED"
    assert [event["event_kind"] for event in history.json()["items"][0]["events"]] == [
        "PREFLIGHT_PASSED",
        "MANUAL_APPROVAL_GRANTED",
        "CONTROLLER_STATE_RECORDED",
        "CONTROLLER_STATE_RECORDED",
        "CONTROLLER_STATE_RECORDED",
        "CONTROLLER_STATE_RECORDED",
    ]
    serialized = history.text.lower()
    assert "release-requester" not in serialized
    assert "distinct-release-approver" not in serialized
    assert "kubeconfig" not in serialized


def test_forged_release_feed_is_rejected_before_history_is_created() -> None:
    trusted = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    app = _app(trusted)
    app.dependency_overrides[require_auth_context] = lambda: _auth("release-requester")
    body = {
        "target_release_id": "platform-v0.1.1",
        "minimum_feed_sequence": 4,
        "source_images": SOURCE.model_dump(),
        "signed_feed": _signed_feed(attacker).model_dump(mode="json"),
    }
    with TestClient(app) as client:
        app.state.platform_instance_service.heartbeat(InstanceReadinessSummary(status="ready"))
        response = client.post("/api/v1/platform/releases:preflight", json=body)
        history = client.get("/api/v1/platform/releases")
    assert response.status_code == 409
    assert response.json()["code"] == "RELEASE_FEED_SIGNER_UNTRUSTED"
    assert history.json()["count"] == 0

from __future__ import annotations

import base64
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hc_data_platform.backup.contracts import (
    BackupManifestV1,
    RestoreTargetV1,
    canonical_json_bytes,
    verify_signed_manifest,
)
from hc_data_platform.backup.postgresql import PostgresEndpoint
from hc_data_platform.backup.repository import LockedRepositoryConfig, S3LockedBackupRepository
from hc_data_platform.backup.restore import (
    ObjectStoreTargetInspectionV1,
    PostgreSQLRestoreTargetAdapter,
    PostgreSQLTargetInspectionV1,
    RestorePlanError,
    RestorePlanRequestV1,
    RestoreReadinessEvidenceV1,
    RestoreTargetInspectionV1,
    RestoreTemporalReadinessV1,
    S3RestoreTargetAdapter,
    StagingTargetInspectionV1,
    create_restore_plan,
)

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"
NOW = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)


def _authenticated() -> tuple[Any, BackupManifestV1, Ed25519PublicKey, bytes, bytes]:
    fixture = json.loads(GOLDEN.read_text(encoding="utf-8"))
    manifest_bytes = canonical_json_bytes(fixture["manifest"])
    signature_bytes = canonical_json_bytes(fixture["signature"])
    public_key = Ed25519PublicKey.from_public_bytes(
        base64.urlsafe_b64decode(fixture["public_key_base64url"] + "==")
    )
    verified = verify_signed_manifest(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
    )
    return verified, verified.manifest, public_key, manifest_bytes, signature_bytes


def _request(manifest: BackupManifestV1) -> RestorePlanRequestV1:
    dependencies = tuple(
        sorted(
            manifest.secrets_and_kms.dependencies,
            key=lambda item: (
                item.environment_variable,
                item.secret_reference,
                item.secret_key_name,
                item.version,
            ),
        )
    )
    target = RestoreTargetV1(
        operation_id="restore-operation-001",
        expected_backup_id=manifest.backup_id,
        expected_source_platform_id=manifest.source_platform_id,
        expected_source_environment_id=manifest.source_environment_id,
        target_instance_id="isolated-target-001",
        target_environment_id="rehearsal-20260829",
        purpose="rehearsal",
        target_is_empty=True,
        requested_release=manifest.release,
        postgresql_major=manifest.postgresql.major_version,
        trusted_signing_key_sha256={manifest.signature.public_key_sha256},
        available_kms_key_references={
            manifest.secrets_and_kms.repository_key_reference,
            manifest.secrets_and_kms.signing_key_reference,
            *manifest.secrets_and_kms.portable_recipient_key_references,
        },
    )
    readiness = RestoreReadinessEvidenceV1(
        target_instance_id=target.target_instance_id,
        target_environment_id=target.target_environment_id,
        observed_at=NOW - timedelta(minutes=5),
        valid_until=NOW + timedelta(hours=1),
        evidence_reference="provider://restore-readiness/rehearsal-20260829",
        evidence_sha256="d" * 64,
        postgresql_available_bytes=64 * 1024 * 1024,
        object_store_available_bytes=64 * 1024 * 1024,
        deployed_release=manifest.release,
        available_kms_key_references=target.available_kms_key_references,
        temporal=RestoreTemporalReadinessV1(
            cluster_reference=manifest.temporal.cluster_reference,
            namespace=manifest.temporal.namespace,
            service_version=manifest.temporal.service_version,
            ready=True,
            evidence_sha256="e" * 64,
        ),
        external_dependencies=dependencies,
        external_dependencies_ready=True,
        api_writes_disabled=True,
        worker_replicas_zero=True,
        domain_names_ready=True,
        certificates_ready=True,
    )
    return RestorePlanRequestV1(
        request_id="restore-request-001",
        requested_at=NOW,
        target=target,
        target_postgresql_database="hc_restore_empty_20260829",
        object_restore_mode="copy_referenced",
        target_object_store_bucket_reference="restore-target-bucket",
        target_object_store_prefix="rehearsal/restore-operation-001",
        readiness=readiness,
    )


class _Inspector:
    def __init__(self, request: RestorePlanRequestV1) -> None:
        self.calls = 0
        self.result = RestoreTargetInspectionV1(
            postgresql=PostgreSQLTargetInspectionV1(
                database_name=request.target_postgresql_database,
                server_major=request.target.postgresql_major,
                user_object_count=0,
                inspection_sha256="1" * 64,
            ),
            object_store=ObjectStoreTargetInspectionV1(
                bucket_reference=request.target_object_store_bucket_reference,
                prefix=request.target_object_store_prefix,
                versioning_enabled=True,
                object_count=0,
                inspection_sha256="2" * 64,
            ),
            staging=StagingTargetInspectionV1(
                available_bytes=64 * 1024 * 1024,
                inspection_sha256="3" * 64,
            ),
        )

    def inspect(self, request: RestorePlanRequestV1) -> RestoreTargetInspectionV1:
        del request
        self.calls += 1
        return self.result


def test_restore_plan_is_deterministic_read_only_and_has_exact_capacity_headroom() -> None:
    authenticated, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest)
    inspector = _Inspector(request)

    first = create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)
    second = create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)

    assert first == second
    assert first.status == "PREFLIGHT_PASSED"
    assert first.dry_run is True
    assert first.writes_enabled is False
    assert first.payloads_downloaded is False
    assert first.plan_id == "restore-plan-" + first.checkpoint_sha256[:24]
    assert [item.domain for item in first.capacities] == [
        "postgresql",
        "object_store",
        "staging",
    ]
    assert first.capacities[0].source_bytes == manifest.postgresql.database_size_bytes
    assert first.capacities[0].required_bytes == 21_810_381
    assert len(first.checks) == 12
    assert inspector.calls == 2


def test_restore_plan_reuses_exact_external_object_location_without_copy_capacity() -> None:
    authenticated, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest).model_copy(
        update={
            "object_restore_mode": "reuse_external",
            "target_object_store_bucket_reference": manifest.object_store.bucket_reference,
            "target_object_store_prefix": manifest.object_store.prefix.strip("/"),
        }
    )
    inspector = _Inspector(request)
    inspector.result = inspector.result.model_copy(
        update={
            "object_store": inspector.result.object_store.model_copy(
                update={"object_count": manifest.object_store.object_count}
            )
        }
    )

    plan = create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)

    assert plan.object_restore_mode == "reuse_external"
    assert plan.target_object_store_bucket_reference == manifest.object_store.bucket_reference
    assert plan.target_object_store_prefix == manifest.object_store.prefix.strip("/")
    assert plan.capacities[1].domain == "object_store"
    assert plan.capacities[1].source_bytes == 0
    assert plan.capacities[1].required_bytes == 0


def test_restore_plan_rejects_reuse_external_location_drift_before_inspection() -> None:
    authenticated, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest).model_copy(update={"object_restore_mode": "reuse_external"})
    inspector = _Inspector(request)

    with pytest.raises(RestorePlanError) as captured:
        create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)

    assert captured.value.code == "RESTORE_EXTERNAL_OBJECT_IDENTITY_MISMATCH"
    assert inspector.calls == 0


@pytest.mark.parametrize("failure", ["release", "kms", "declared_nonempty"])
def test_restore_plan_binding_failures_do_not_even_inspect_target(failure: str) -> None:
    authenticated, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest)
    if failure == "release":
        release = request.target.requested_release.model_copy(update={"chart_version": "9.9.9"})
        target = request.target.model_copy(update={"requested_release": release})
    elif failure == "kms":
        target = request.target.model_copy(
            update={
                "available_kms_key_references": frozenset(
                    {manifest.secrets_and_kms.signing_key_reference}
                )
            }
        )
    else:
        target = request.target.model_copy(
            update={
                "target_is_empty": False,
                "temporary_restore_authorization_id": "temporary-authorization-001",
            }
        )
    request = request.model_copy(update={"target": target})
    inspector = _Inspector(request)

    with pytest.raises(RestorePlanError) as captured:
        create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)

    assert captured.value.code in {
        "BACKUP_RELEASE_MISMATCH",
        "BACKUP_KMS_KEY_UNAVAILABLE",
        "RESTORE_TARGET_NOT_EMPTY",
    }
    assert inspector.calls == 0


@pytest.mark.parametrize(
    ("readiness_update", "expected_code"),
    [
        ({"api_writes_disabled": False}, "RESTORE_TARGET_FENCE_NOT_READY"),
        ({"worker_replicas_zero": False}, "RESTORE_TARGET_FENCE_NOT_READY"),
    ],
)
def test_restore_plan_requires_observed_target_write_fence_before_inspection(
    readiness_update: dict[str, object],
    expected_code: str,
) -> None:
    authenticated, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest)
    request = request.model_copy(
        update={"readiness": request.readiness.model_copy(update=readiness_update)}
    )
    inspector = _Inspector(request)

    with pytest.raises(RestorePlanError) as captured:
        create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)

    assert captured.value.code == expected_code
    assert inspector.calls == 0


def test_restore_plan_rejects_observed_release_drift_before_target_inspection() -> None:
    authenticated, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest)
    deployed = manifest.release.model_copy(update={"chart_version": "9.9.9"})
    request = request.model_copy(
        update={"readiness": request.readiness.model_copy(update={"deployed_release": deployed})}
    )
    inspector = _Inspector(request)

    with pytest.raises(RestorePlanError) as captured:
        create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)

    assert captured.value.code == "BACKUP_RELEASE_MISMATCH"
    assert inspector.calls == 0


@pytest.mark.parametrize("domain", ["object_store"])
def test_restore_plan_rejects_exact_source_target_collisions_before_inspection(
    domain: str,
) -> None:
    authenticated, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest)
    assert domain == "object_store"
    request = request.model_copy(
        update={
            "object_restore_mode": "copy_referenced",
            "target_object_store_bucket_reference": manifest.object_store.bucket_reference,
            "target_object_store_prefix": manifest.object_store.prefix.strip("/"),
        }
    )
    inspector = _Inspector(request)

    with pytest.raises(RestorePlanError) as captured:
        create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)

    assert captured.value.code == "RESTORE_OBJECT_STORE_TARGET_SOURCE_COLLISION"
    assert inspector.calls == 0


def test_restore_plan_allows_same_database_name_on_distinct_target_instance() -> None:
    authenticated, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    assert manifest.postgresql.database_name is not None
    request = _request(manifest).model_copy(
        update={"target_postgresql_database": manifest.postgresql.database_name}
    )
    inspector = _Inspector(request)

    plan = create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)

    assert plan.target_postgresql_database == manifest.postgresql.database_name
    assert plan.target_instance_id != manifest.source_platform_id


def test_restore_plan_rejects_actual_nonempty_targets_and_capacity_without_writes() -> None:
    authenticated, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest)
    inspector = _Inspector(request)
    inspector.result = inspector.result.model_copy(
        update={
            "postgresql": inspector.result.postgresql.model_copy(update={"user_object_count": 1})
        }
    )
    with pytest.raises(RestorePlanError) as nonempty:
        create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)
    assert nonempty.value.code == "RESTORE_POSTGRES_TARGET_NOT_EMPTY"

    inspector = _Inspector(request)
    readiness = request.readiness.model_copy(update={"postgresql_available_bytes": 1})
    request = request.model_copy(update={"readiness": readiness})
    with pytest.raises(RestorePlanError) as capacity:
        create_restore_plan(authenticated, request, inspector=inspector, clock=lambda: NOW)
    assert capacity.value.code == "RESTORE_CAPACITY_INSUFFICIENT"


def test_restore_plan_never_guesses_capacity_for_legacy_v1_manifest() -> None:
    authenticated, manifest, _key, _manifest_bytes, _signature_bytes = _authenticated()
    request = _request(manifest)
    legacy_manifest = manifest.model_copy(
        update={"postgresql": manifest.postgresql.model_copy(update={"database_size_bytes": None})}
    )
    legacy = authenticated.__class__(
        manifest=legacy_manifest,
        manifest_sha256=authenticated.manifest_sha256,
        signature_sha256=authenticated.signature_sha256,
    )
    inspector = _Inspector(request)

    with pytest.raises(RestorePlanError) as captured:
        create_restore_plan(legacy, request, inspector=inspector, clock=lambda: NOW)

    assert captured.value.code == "RESTORE_CAPACITY_EVIDENCE_MISSING"
    assert inspector.calls == 0


class _Rows:
    def __init__(self, value: tuple[object, ...] | None) -> None:
        self.value = value

    def fetchone(self) -> tuple[object, ...] | None:
        return self.value


class _ReadOnlyConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self) -> _ReadOnlyConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str) -> _Rows:
        self.statements.append(statement)
        normalized = " ".join(statement.split()).lower()
        if normalized.startswith("set transaction"):
            return _Rows(None)
        if "current_database()" in normalized:
            return _Rows(("hc_restore_empty_20260829", 160010))
        return _Rows((0,))


class _ReadOnlyS3:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_bucket_versioning(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("versioning", kwargs))
        return {"Status": "Enabled"}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("list", kwargs))
        return {"Contents": []}


def test_live_target_adapters_use_only_bounded_read_operations() -> None:
    connection = _ReadOnlyConnection()
    postgres = PostgreSQLRestoreTargetAdapter(
        PostgresEndpoint(
            host="postgres.internal",
            database="hc_restore_empty_20260829",
            username="restore_reader",
            password="not-logged",
            sslmode="verify-full",
        ),
        connection_factory=lambda _: connection,
    )
    inspected_postgres = postgres.inspect("hc_restore_empty_20260829")
    assert inspected_postgres.user_object_count == 0
    assert all(
        "insert " not in statement.lower()
        and "update " not in statement.lower()
        and "delete " not in statement.lower()
        and "create " not in statement.lower()
        for statement in connection.statements
    )
    assert connection.statements[0].startswith("SET TRANSACTION")

    client = _ReadOnlyS3()
    objects = S3RestoreTargetAdapter(
        client,
        bucket="physical-restore-bucket",
        bucket_reference="restore-target-bucket",
        prefix="rehearsal/restore-operation-001",
    ).inspect()
    assert objects.object_count == 0
    assert [item[0] for item in client.calls] == ["versioning", "list"]
    assert client.calls[1][1]["MaxKeys"] == 1
    assert client.calls[1][1]["Prefix"] == "rehearsal/restore-operation-001/"


class _ManifestOnlyS3:
    def __init__(self, manifest_bytes: bytes, signature_bytes: bytes, retention: datetime) -> None:
        self.payloads = {
            "manifest.json": manifest_bytes,
            "signature.sig": signature_bytes,
        }
        self.retention = retention
        self.reads: list[str] = []

    def get_bucket_versioning(self, **_: object) -> dict[str, object]:
        return {"Status": "Enabled"}

    def get_object_lock_configuration(self, **_: object) -> dict[str, object]:
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def get_bucket_replication(self, **_: object) -> dict[str, object]:
        return {
            "ReplicationConfiguration": {
                "Rules": [
                    {
                        "Status": "Enabled",
                        "Destination": {"Bucket": "replica-provider-id"},
                    }
                ]
            }
        }

    def get_object(self, **kwargs: object) -> dict[str, object]:
        logical = str(kwargs["Key"]).rsplit("/", 1)[-1]
        self.reads.append(logical)
        payload = self.payloads[logical]
        digest = hashlib.sha256(payload).hexdigest()
        return {
            "Body": io.BytesIO(payload),
            "ContentLength": len(payload),
            "Metadata": {"hc-sha256": digest},
            "VersionId": f"version-{logical}",
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": "provider-kms-id",
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": self.retention,
        }


def test_repository_manifest_preflight_reads_no_backup_payload() -> None:
    _verified, manifest, public_key, manifest_bytes, signature_bytes = _authenticated()
    client = _ManifestOnlyS3(
        manifest_bytes,
        signature_bytes,
        manifest.backup_repository.retention_until,
    )
    repository = S3LockedBackupRepository(
        client,  # type: ignore[arg-type]
        LockedRepositoryConfig(
            repository_id=manifest.backup_repository.repository_id,
            provider=manifest.backup_repository.provider,
            bucket="physical-backup-bucket",
            bucket_reference=manifest.backup_repository.bucket_reference,
            kms_key_reference=manifest.backup_repository.kms_key_reference,
            provider_kms_key_id="provider-kms-id",
            provider_kms_observed_key_id="provider-kms-id",
            provider_replica_destination="replica-provider-id",
            retention_until=manifest.backup_repository.retention_until,
            cross_site_replica_reference=(manifest.backup_repository.cross_site_replica_reference),
        ),
        clock=lambda: NOW,
    )

    result = repository.verify_manifest(
        manifest.backup_id,
        public_keys_by_sha256={manifest.signature.public_key_sha256: public_key},
    )

    assert result.verified_manifest.manifest == manifest
    assert client.reads == ["manifest.json", "signature.sig"]

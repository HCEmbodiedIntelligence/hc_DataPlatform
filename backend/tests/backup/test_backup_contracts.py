from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import ValidationError

from hc_data_platform.backup.contracts import (
    BackupContractError,
    BackupManifestV1,
    RestoreTargetV1,
    SignatureEnvelopeV1,
    canonical_json_bytes,
    find_plaintext_secret_material,
    sign_backup_manifest,
    verify_backup_for_target,
    verify_signed_manifest,
)
from hc_data_platform.backup.restore import RestorePlanRequestV1, RestorePlanV1
from hc_data_platform.backup.restore_execution import (
    RestoreApprovalV1,
    RestoreExecutionCheckpointV1,
)
from hc_data_platform.backup.whole import WholeBackupChecksumsV1, WholeBackupPlanV1

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIRECTORY = ROOT / "docs/architecture/contracts"
GOLDEN_MANIFEST = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _artifact(
    path: str,
    role: str,
    seed: str,
    encryption: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "role": role,
        "media_type": "application/octet-stream",
        "size_bytes": 1024,
        "sha256": _sha256(seed),
        "client_side_encryption": encryption,
    }


def _domain(
    disposition: str,
    status: str,
    owner: str,
    evidence: str,
) -> dict[str, Any]:
    return {
        "disposition": disposition,
        "verification_status": status,
        "owner_role": owner,
        "evidence_artifacts": [evidence],
    }


def _public_key_sha256(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def _manifest(public_key_sha256: str) -> BackupManifestV1:
    migration_sha256 = _sha256("migration-manifest")
    release = {
        "git_commit": "1" * 40,
        "chart_name": "hc-data-platform",
        "chart_version": "0.1.0",
        "images": {
            "api": f"sha256:{'2' * 64}",
            "worker": f"sha256:{'3' * 64}",
            "frontend": f"sha256:{'4' * 64}",
        },
        "migration_manifest_sha256": migration_sha256,
        "release_manifest_sha256": _sha256("release-manifest"),
    }
    artifacts = [
        _artifact("db/platform.dump", "postgresql_dump", "database", "age_x25519_v1"),
        _artifact(
            "objects/inventory.jsonl.zst",
            "object_inventory",
            "inventory",
            "age_x25519_v1",
        ),
        _artifact(
            "objects/parts/part-000001.tar.zst.age",
            "object_part",
            "object-part",
            "age_x25519_v1",
        ),
        _artifact("temporal/policy.json", "temporal_policy", "temporal", "age_x25519_v1"),
        _artifact(
            "config/public.yaml",
            "public_config",
            "public-config",
            "public_integrity_metadata",
        ),
        _artifact(
            "secrets/envelope.json",
            "secrets_envelope",
            "secret-envelope",
            "age_x25519_v1",
        ),
        _artifact(
            "checksums.sha256",
            "checksums",
            "checksums",
            "public_integrity_metadata",
        ),
    ]
    return BackupManifestV1.model_validate(
        {
            "format_version": "hc-platform-backup/v1",
            "backup_id": "backup-20260828-001",
            "mode": "portable",
            "status": "INTEGRITY_VERIFIED",
            "created_at": "2026-08-28T06:00:00Z",
            "completed_at": "2026-08-28T06:10:00Z",
            "source_platform_id": "platform-cn-prod",
            "source_environment_id": "production-cn-east",
            "release": release,
            "backup_tool": {
                "name": "hc-platform-backup",
                "version": "0.1.0",
                "git_commit": "1" * 40,
                "image_digest": f"sha256:{'5' * 64}",
            },
            "postgresql": {
                "major_version": 16,
                "server_version": "16.10",
                "database_size_bytes": 16777216,
                "database_name": "hc_data",
                "dump_path": "db/platform.dump",
                "dump_sha256": _sha256("database"),
                "dump_format": "postgresql_custom",
                "migration_count": 97,
                "migration_manifest_sha256": migration_sha256,
                "content_evidence": {
                    "schema_object_count": 1,
                    "schema_sha256": "1" * 64,
                    "constraint_count": 1,
                    "constraint_sha256": "2" * 64,
                    "sequence_count": 1,
                    "sequence_sha256": "3" * 64,
                    "tables": [],
                    "aggregate_sha256": "4" * 64,
                },
                "physical_recovery": "baseline_plus_wal_pitr",
                "pitr_coordinate": "timeline=3 lsn=0/5A000000",
            },
            "object_store": {
                "provider": "s3_compatible",
                "bucket_reference": "production-data",
                "prefix": "platform-cn-prod/",
                "versioning_status": "enabled",
                "consistency_coordinate": "inventory-20260828T060000Z",
                "inventory_path": "objects/inventory.jsonl.zst",
                "inventory_sha256": _sha256("inventory"),
                "object_count": 1,
                "total_bytes": 1024,
                "portable_part_paths": ["objects/parts/part-000001.tar.zst.age"],
            },
            "temporal": {
                "deployment_mode": "external_managed",
                "cluster_reference": "temporal-production-cn",
                "namespace": "hc-production-cn-east",
                "service_version": "1.28.1",
                "recovery_policy": "provider_supported_ha_backup",
                "policy_path": "temporal/policy.json",
                "policy_sha256": _sha256("temporal"),
                "schedule_inventory_sha256": _sha256("temporal-schedules"),
                "open_workflow_inventory_sha256": _sha256("temporal-workflows"),
                "internal_database_in_business_dump": False,
            },
            "deployment_configuration": {
                "gitops_repository_reference": "gitops/hc-data-platform@" + "1" * 40,
                "public_config_path": "config/public.yaml",
                "public_config_sha256": _sha256("public-config"),
                "endpoint_remapping_policy": "explicit_only",
            },
            "secrets_and_kms": {
                "envelope_path": "secrets/envelope.json",
                "envelope_sha256": _sha256("secret-envelope"),
                "dependencies": [
                    {
                        "environment_variable": "HC_POSTGRES_DSN",
                        "secret_reference": "platform-production",
                        "secret_key_name": "postgres-dsn",
                        "version": "7",
                        "fingerprint_sha256": _sha256("postgres-dsn-version-7"),
                    },
                    {
                        "environment_variable": "HC_DATA_SOURCE_CREDENTIAL_KEY",
                        "secret_reference": "platform-production",
                        "secret_key_name": "credential-master-key",
                        "version": "4",
                        "fingerprint_sha256": _sha256("credential-master-key-version-4"),
                    },
                ],
                "repository_key_reference": "kms://security/repository/versions/v7",
                "portable_recipient_key_references": ["kms://security/portable-age/versions/v4"],
                "signing_key_reference": "kms://security/signing-ed25519/versions/v3",
                "plaintext_export_allowed": False,
            },
            "backup_repository": {
                "repository_id": "backup-repository-cn",
                "provider": "s3_compatible",
                "bucket_reference": "isolated-backups",
                "server_side_encryption": "kms_aes256_gcm",
                "kms_key_reference": "kms://security/repository/versions/v7",
                "object_lock": "compliance",
                "retention_until": "2027-08-28T00:00:00Z",
                "cross_site_replica_reference": "backup-replica-cn-west",
                "catalog_is_only_copy": False,
            },
            "state_domains": {
                "postgresql_business": _domain(
                    "included", "verified", "database_operations", "db/platform.dump"
                ),
                "object_store": _domain(
                    "included",
                    "verified",
                    "object_storage_operations",
                    "objects/inventory.jsonl.zst",
                ),
                "temporal_workflows": _domain(
                    "external", "external_verified", "temporal_operations", "temporal/policy.json"
                ),
                "deployment_configuration": _domain(
                    "included", "verified", "release_engineering", "config/public.yaml"
                ),
                "secrets_and_kms": _domain(
                    "external",
                    "external_verified",
                    "security_kms_operations",
                    "secrets/envelope.json",
                ),
                "release_artifacts": _domain(
                    "included", "verified", "release_engineering", "checksums.sha256"
                ),
                "backup_repository": _domain(
                    "included", "verified", "platform_sre", "checksums.sha256"
                ),
                "runtime_logs": _domain(
                    "not_applicable",
                    "not_applicable",
                    "observability_operations",
                    "checksums.sha256",
                ),
                "external_dependencies": _domain(
                    "external",
                    "external_verified",
                    "identity_operations",
                    "config/public.yaml",
                ),
            },
            "artifacts": artifacts,
            "verification_facts": [
                {
                    "operation_id": "integrity-verification-001",
                    "kind": "integrity",
                    "result": "passed",
                    "verified_at": "2026-08-28T06:10:00Z",
                    "verifier_identity": "isolated-verifier/build-001",
                    "evidence_artifacts": ["checksums.sha256"],
                }
            ],
            "compatibility": {
                "schema_version": 1,
                "minimum_backup_tool_version": "0.1.0",
                "maximum_backup_tool_major": 1,
                "postgresql_major": 16,
                "exact_recorded_release_required": True,
                "forward_upgrade_before_restore_verification_allowed": False,
            },
            "signature": {
                "format_version": "hc-platform-signature/v1",
                "algorithm": "Ed25519",
                "canonicalization": "hc-json-c14n/v1",
                "key_reference": "kms://security/signing-ed25519/versions/v3",
                "public_key_sha256": public_key_sha256,
                "signature_path": "signature.sig",
            },
        }
    )


def _target(manifest: BackupManifestV1) -> RestoreTargetV1:
    return RestoreTargetV1.model_validate(
        {
            "format_version": "hc-platform-restore-target/v1",
            "operation_id": "restore-operation-001",
            "expected_backup_id": manifest.backup_id,
            "expected_source_platform_id": manifest.source_platform_id,
            "expected_source_environment_id": manifest.source_environment_id,
            "target_instance_id": "isolated-dr-host-001",
            "target_environment_id": "dr-rehearsal-20260828",
            "purpose": "rehearsal",
            "target_is_empty": True,
            "temporary_restore_authorization_id": None,
            "writes_enabled": False,
            "supported_backup_formats": ["hc-platform-backup/v1"],
            "requested_release": manifest.release.model_dump(mode="json"),
            "postgresql_major": manifest.postgresql.major_version,
            "object_store_versioning_enabled": True,
            "trusted_signing_key_sha256": [manifest.signature.public_key_sha256],
            "available_kms_key_references": [
                "kms://security/repository/versions/v7",
                "kms://security/portable-age/versions/v4",
                "kms://security/signing-ed25519/versions/v3",
            ],
            "temporal_namespace_ready": True,
            "required_external_dependencies_ready": True,
        }
    )


def _signed_backup() -> tuple[bytes, bytes, RestoreTargetV1, Ed25519PublicKey]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    manifest = _manifest(_public_key_sha256(public_key))
    document = manifest.model_dump(mode="json")
    canonical = canonical_json_bytes(document)
    signature = base64.urlsafe_b64encode(private_key.sign(canonical)).rstrip(b"=").decode()
    envelope = SignatureEnvelopeV1(
        key_reference=manifest.signature.key_reference,
        public_key_sha256=manifest.signature.public_key_sha256,
        manifest_sha256=hashlib.sha256(canonical).hexdigest(),
        signature_base64url=signature,
    )
    return (
        canonical_json_bytes(document),
        canonical_json_bytes(envelope.model_dump(mode="json")),
        _target(manifest),
        public_key,
    )


class _IsolatedSigningAdapter:
    def __init__(self, private_key: Ed25519PrivateKey, key_reference: str) -> None:
        self._private_key = private_key
        self.key_reference = key_reference
        self.public_key = private_key.public_key()

    def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(message)


class _InvalidSignatureAdapter(_IsolatedSigningAdapter):
    def sign(self, message: bytes) -> bytes:
        del message
        return b"\x00" * 64


def _assert_error(code: str, function: Any) -> None:
    with pytest.raises(BackupContractError) as captured:
        function()
    assert captured.value.code == code


def test_committed_json_schemas_match_the_strict_runtime_contracts() -> None:
    models = {
        "hc-platform-backup-v1.schema.json": BackupManifestV1,
        "hc-platform-restore-target-v1.schema.json": RestoreTargetV1,
        "hc-platform-signature-v1.schema.json": SignatureEnvelopeV1,
        "hc-whole-backup-plan-v1.schema.json": WholeBackupPlanV1,
        "hc-platform-checksums-v1.schema.json": WholeBackupChecksumsV1,
        "hc-platform-restore-plan-request-v1.schema.json": RestorePlanRequestV1,
        "hc-platform-restore-plan-v1.schema.json": RestorePlanV1,
        "hc-platform-restore-approval-v1.schema.json": RestoreApprovalV1,
        "hc-platform-restore-checkpoint-v1.schema.json": RestoreExecutionCheckpointV1,
    }
    for filename, model in models.items():
        committed = json.loads((CONTRACT_DIRECTORY / filename).read_text(encoding="utf-8"))
        assert committed == model.model_json_schema()
        assert committed["additionalProperties"] is False
        assert committed["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        for definition in committed.get("$defs", {}).values():
            if definition.get("type") == "object":
                assert definition["additionalProperties"] is False


def test_real_ed25519_signature_and_correct_target_are_accepted() -> None:
    manifest_bytes, signature_bytes, target, public_key = _signed_backup()
    verified = verify_backup_for_target(
        manifest_bytes,
        signature_bytes,
        target=target,
        public_key=public_key,
    )
    assert verified.manifest.backup_id == target.expected_backup_id
    assert verified.target_instance_id == "isolated-dr-host-001"
    assert verified.manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()


def test_golden_v1_manifest_remains_authentic_and_backward_compatible() -> None:
    fixture = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    public_key_raw = base64.urlsafe_b64decode(fixture["public_key_base64url"] + "==")
    public_key = Ed25519PublicKey.from_public_bytes(public_key_raw)
    manifest_bytes = canonical_json_bytes(fixture["manifest"])
    signature_bytes = canonical_json_bytes(fixture["signature"])

    verified = verify_signed_manifest(
        manifest_bytes,
        signature_bytes,
        public_key=public_key,
    )

    assert verified.manifest.format_version == "hc-platform-backup/v1"
    assert verified.manifest.backup_id == "backup-20260828-001"
    assert verified.manifest.status == "INTEGRITY_VERIFIED"
    assert find_plaintext_secret_material(fixture) == ()


def test_pre_rst3_v1_manifest_without_database_size_remains_authentic() -> None:
    fixture = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    del fixture["manifest"]["postgresql"]["database_size_bytes"]
    del fixture["manifest"]["postgresql"]["database_name"]
    del fixture["manifest"]["postgresql"]["content_evidence"]
    manifest_bytes = canonical_json_bytes(fixture["manifest"])
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    signature = base64.urlsafe_b64encode(private_key.sign(manifest_bytes)).rstrip(b"=").decode()
    envelope = {
        **fixture["signature"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "signature_base64url": signature,
    }

    verified = verify_signed_manifest(
        manifest_bytes,
        canonical_json_bytes(envelope),
        public_key=private_key.public_key(),
    )

    assert verified.manifest.postgresql.database_size_bytes is None
    assert verified.manifest.postgresql.database_name is None
    assert verified.manifest.postgresql.content_evidence is None


def test_golden_v1_manifest_rejects_single_field_tampering() -> None:
    fixture = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    public_key_raw = base64.urlsafe_b64decode(fixture["public_key_base64url"] + "==")
    public_key = Ed25519PublicKey.from_public_bytes(public_key_raw)
    fixture["manifest"]["object_store"]["total_bytes"] += 1

    _assert_error(
        "BACKUP_SIGNATURE_MANIFEST_HASH_MISMATCH",
        lambda: verify_signed_manifest(
            canonical_json_bytes(fixture["manifest"]),
            canonical_json_bytes(fixture["signature"]),
            public_key=public_key,
        ),
    )


def test_external_signing_boundary_produces_self_verified_canonical_artifacts() -> None:
    private_key = Ed25519PrivateKey.generate()
    signer = _IsolatedSigningAdapter(
        private_key,
        "kms://security/signing-ed25519/versions/v3",
    )
    manifest = _manifest(_public_key_sha256(signer.public_key))

    signed = sign_backup_manifest(manifest, signer=signer)
    authenticated = verify_signed_manifest(
        signed.manifest_bytes,
        signed.signature_bytes,
        public_key=signer.public_key,
    )

    assert signed.manifest_bytes == canonical_json_bytes(manifest.model_dump(mode="json"))
    assert signed.manifest_sha256 == authenticated.manifest_sha256
    assert signed.signature_sha256 == authenticated.signature_sha256
    assert authenticated.manifest == manifest


def test_external_signer_identity_mismatch_fails_before_signing() -> None:
    trusted_private_key = Ed25519PrivateKey.generate()
    trusted_public_key = trusted_private_key.public_key()
    manifest = _manifest(_public_key_sha256(trusted_public_key))
    wrong_signer = _IsolatedSigningAdapter(
        Ed25519PrivateKey.generate(),
        manifest.signature.key_reference,
    )

    _assert_error(
        "BACKUP_SIGNER_POLICY_MISMATCH",
        lambda: sign_backup_manifest(manifest, signer=wrong_signer),
    )


def test_external_signer_result_is_verified_before_artifacts_are_returned() -> None:
    private_key = Ed25519PrivateKey.generate()
    signer = _InvalidSignatureAdapter(
        private_key,
        "kms://security/signing-ed25519/versions/v3",
    )
    manifest = _manifest(_public_key_sha256(signer.public_key))

    _assert_error(
        "BACKUP_SIGNATURE_INVALID",
        lambda: sign_backup_manifest(manifest, signer=signer),
    )


def test_plaintext_secret_scanner_is_zero_for_manifest_and_rejects_leaks() -> None:
    manifest_bytes, signature_bytes, target, public_key = _signed_backup()
    document = json.loads(manifest_bytes)
    assert find_plaintext_secret_material(document) == ()

    document["secrets_and_kms"]["dependencies"][0]["password"] = "plaintext-is-forbidden"
    poisoned = canonical_json_bytes(document)
    assert find_plaintext_secret_material(document) == ("secrets_and_kms.dependencies[0].password",)
    _assert_error(
        "BACKUP_PLAINTEXT_SECRET_DETECTED",
        lambda: verify_backup_for_target(
            poisoned, signature_bytes, target=target, public_key=public_key
        ),
    )


def test_tampered_manifest_fails_closed_before_target_use() -> None:
    manifest_bytes, signature_bytes, target, public_key = _signed_backup()
    document = json.loads(manifest_bytes)
    checksums = next(
        artifact for artifact in document["artifacts"] if artifact["role"] == "checksums"
    )
    checksums["sha256"] = _sha256("attacker-replaced-checksums")
    _assert_error(
        "BACKUP_SIGNATURE_MANIFEST_HASH_MISMATCH",
        lambda: verify_backup_for_target(
            canonical_json_bytes(document),
            signature_bytes,
            target=target,
            public_key=public_key,
        ),
    )


def test_unknown_backup_version_fails_closed_without_compatibility_guessing() -> None:
    manifest_bytes, signature_bytes, target, public_key = _signed_backup()
    document = json.loads(manifest_bytes)
    document["format_version"] = "hc-platform-backup/v2"
    _assert_error(
        "BACKUP_FORMAT_UNSUPPORTED",
        lambda: verify_backup_for_target(
            canonical_json_bytes(document),
            signature_bytes,
            target=target,
            public_key=public_key,
        ),
    )


def test_wrong_source_target_binding_and_wrong_release_fail_closed() -> None:
    manifest_bytes, signature_bytes, target, public_key = _signed_backup()
    wrong_source = target.model_copy(update={"expected_source_environment_id": "staging-cn-east"})
    _assert_error(
        "BACKUP_TARGET_IDENTITY_MISMATCH",
        lambda: verify_backup_for_target(
            manifest_bytes,
            signature_bytes,
            target=wrong_source,
            public_key=public_key,
        ),
    )

    wrong_release_identity = target.requested_release.model_copy(update={"chart_version": "0.2.0"})
    wrong_release = target.model_copy(update={"requested_release": wrong_release_identity})
    _assert_error(
        "BACKUP_RELEASE_MISMATCH",
        lambda: verify_backup_for_target(
            manifest_bytes,
            signature_bytes,
            target=wrong_release,
            public_key=public_key,
        ),
    )


def test_wrong_signing_key_and_missing_state_domain_fail_closed() -> None:
    manifest_bytes, signature_bytes, target, _public_key = _signed_backup()
    wrong_public_key = Ed25519PrivateKey.generate().public_key()
    _assert_error(
        "BACKUP_SIGNING_KEY_MISMATCH",
        lambda: verify_backup_for_target(
            manifest_bytes,
            signature_bytes,
            target=target,
            public_key=wrong_public_key,
        ),
    )

    document = json.loads(manifest_bytes)
    del document["state_domains"]["object_store"]
    with pytest.raises(ValidationError):
        BackupManifestV1.model_validate(document)


def test_restore_target_must_be_fenced_and_kms_references_are_immutable() -> None:
    manifest_bytes, _signature_bytes, target, _public_key = _signed_backup()
    manifest = BackupManifestV1.model_validate_json(manifest_bytes)
    target_document = target.model_dump(mode="json")
    target_document["target_is_empty"] = False
    with pytest.raises(ValidationError, match="explicit temporary restore authorization"):
        RestoreTargetV1.model_validate(target_document)

    document = manifest.model_dump(mode="json")
    document["secrets_and_kms"]["repository_key_reference"] = "kms://security/repository/latest"
    with pytest.raises(ValidationError):
        BackupManifestV1.model_validate(document)

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from psycopg.conninfo import conninfo_to_dict

from hc_data_platform.backup.contracts import (
    RestoreTargetV1,
    canonical_json_bytes,
    verify_signed_manifest,
)
from hc_data_platform.backup.postgresql import PostgresEndpoint
from hc_data_platform.backup.restore import (
    CompositeRestoreTargetInspector,
    PostgreSQLRestoreTargetAdapter,
    RestorePlanError,
    RestorePlanRequestV1,
    RestoreReadinessEvidenceV1,
    RestoreTemporalReadinessV1,
    S3RestoreTargetAdapter,
    create_restore_plan,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "backend/tests/backup/fixtures/hc-platform-backup-v1.golden.json"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required")
    return value


def _endpoint(dsn: str) -> PostgresEndpoint:
    parsed = conninfo_to_dict(normalize_postgres_dsn(dsn))
    return PostgresEndpoint(
        host=parsed["host"],
        port=int(parsed["port"]),
        database=parsed["dbname"],
        username=parsed["user"],
        password=parsed["password"],
        sslmode="disable",
    )


def _authenticated() -> tuple[Any, Any]:
    fixture = json.loads(GOLDEN.read_text(encoding="utf-8"))
    public_key = Ed25519PublicKey.from_public_bytes(
        base64.urlsafe_b64decode(fixture["public_key_base64url"] + "==")
    )
    verified = verify_signed_manifest(
        canonical_json_bytes(fixture["manifest"]),
        canonical_json_bytes(fixture["signature"]),
        public_key=public_key,
    )
    return verified, verified.manifest


def _request(manifest: Any, endpoint: PostgresEndpoint, prefix: str) -> RestorePlanRequestV1:
    now = datetime.now(timezone.utc)
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
        operation_id="rst301-real-operation",
        expected_backup_id=manifest.backup_id,
        expected_source_platform_id=manifest.source_platform_id,
        expected_source_environment_id=manifest.source_environment_id,
        target_instance_id="rst301-real-target",
        target_environment_id="rst301-real-rehearsal",
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
    return RestorePlanRequestV1(
        request_id="rst301-real-request",
        requested_at=now,
        target=target,
        target_postgresql_database=endpoint.database,
        object_restore_mode="copy_referenced",
        target_object_store_bucket_reference=_required("HC_RESTORE_TEST_OBJECT_BUCKET_REFERENCE"),
        target_object_store_prefix=prefix,
        readiness=RestoreReadinessEvidenceV1(
            target_instance_id=target.target_instance_id,
            target_environment_id=target.target_environment_id,
            observed_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(hours=1),
            evidence_reference="provider://rst301/real-readiness",
            evidence_sha256="d" * 64,
            postgresql_available_bytes=128 * 1024 * 1024,
            object_store_available_bytes=128 * 1024 * 1024,
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
        ),
    )


@pytest.mark.integration
def test_real_postgres_minio_restore_plan_is_read_only_and_fails_closed() -> None:
    assert _required("HC_RESTORE_TEST_ALLOW_MUTATION") == "disposable-only"
    endpoint = _endpoint(_required("HC_RESTORE_TEST_POSTGRES_DSN"))
    staging = Path(_required("HC_RESTORE_TEST_STAGING_DIR"))
    object_endpoint = _required("HC_RESTORE_TEST_OBJECT_ENDPOINT")
    bucket = _required("HC_RESTORE_TEST_OBJECT_BUCKET")
    prefix = _required("HC_RESTORE_TEST_OBJECT_PREFIX").strip("/")
    boto3 = pytest.importorskip("boto3")
    client = boto3.client(
        "s3",
        endpoint_url=object_endpoint,
        aws_access_key_id=_required("HC_RESTORE_TEST_OBJECT_ACCESS_KEY"),
        aws_secret_access_key=_required("HC_RESTORE_TEST_OBJECT_SECRET_KEY"),
        region_name="us-east-1",
    )
    assert client.get_bucket_versioning(Bucket=bucket).get("Status") == "Enabled"
    assert not client.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/", MaxKeys=1).get("Contents")
    authenticated, manifest = _authenticated()
    request = _request(manifest, endpoint, prefix)

    def connect(_: PostgresEndpoint) -> Any:
        return psycopg.connect(_required("HC_RESTORE_TEST_POSTGRES_DSN"))

    postgres = PostgreSQLRestoreTargetAdapter(endpoint, connection_factory=connect)
    objects = S3RestoreTargetAdapter(
        client,
        bucket=bucket,
        bucket_reference=request.target_object_store_bucket_reference,
        prefix=prefix,
    )
    inspector = CompositeRestoreTargetInspector(
        postgres,
        objects,
        staging_directory=staging,
    )

    before = postgres.inspect(endpoint.database)
    plan = create_restore_plan(authenticated, request, inspector=inspector)
    after = postgres.inspect(endpoint.database)
    assert plan.status == "PREFLIGHT_PASSED"
    assert plan.writes_enabled is False and plan.payloads_downloaded is False
    assert before.user_object_count == after.user_object_count == 0

    wrong_release = request.model_copy(
        update={
            "readiness": request.readiness.model_copy(
                update={
                    "deployed_release": manifest.release.model_copy(
                        update={"chart_version": "9.9.9"}
                    )
                }
            )
        }
    )
    with pytest.raises(RestorePlanError) as release_error:
        create_restore_plan(authenticated, wrong_release, inspector=inspector)
    assert release_error.value.code == "BACKUP_RELEASE_MISMATCH"

    wrong_kms_target = request.target.model_copy(
        update={
            "available_kms_key_references": frozenset(
                {manifest.secrets_and_kms.signing_key_reference}
            )
        }
    )
    wrong_kms = request.model_copy(update={"target": wrong_kms_target})
    with pytest.raises(RestorePlanError) as kms_error:
        create_restore_plan(authenticated, wrong_kms, inspector=inspector)
    assert kms_error.value.code == "BACKUP_KMS_KEY_UNAVAILABLE"
    assert postgres.inspect(endpoint.database).user_object_count == 0
    assert objects.inspect().object_count == 0

    with psycopg.connect(_required("HC_RESTORE_TEST_POSTGRES_DSN"), autocommit=True) as connection:
        connection.execute("CREATE TABLE rst301_nonempty_probe (id bigint PRIMARY KEY)")
    try:
        with pytest.raises(RestorePlanError) as nonempty:
            create_restore_plan(authenticated, request, inspector=inspector)
        assert nonempty.value.code == "RESTORE_POSTGRES_TARGET_NOT_EMPTY"
        with psycopg.connect(_required("HC_RESTORE_TEST_POSTGRES_DSN")) as connection:
            assert connection.execute("SELECT count(*) FROM rst301_nonempty_probe").fetchone() == (
                0,
            )
    finally:
        with psycopg.connect(
            _required("HC_RESTORE_TEST_POSTGRES_DSN"), autocommit=True
        ) as connection:
            connection.execute("DROP TABLE rst301_nonempty_probe")

    sentinel_key = f"{prefix}/nonempty-sentinel"
    created = client.put_object(Bucket=bucket, Key=sentinel_key, Body=b"preserve-me")
    try:
        with pytest.raises(RestorePlanError) as nonempty_objects:
            create_restore_plan(authenticated, request, inspector=inspector)
        assert nonempty_objects.value.code == "RESTORE_OBJECT_STORE_TARGET_NOT_EMPTY"
        head = client.head_object(Bucket=bucket, Key=sentinel_key)
        assert head["VersionId"] == created["VersionId"]
        assert head["ContentLength"] == len(b"preserve-me")
    finally:
        client.delete_object(Bucket=bucket, Key=sentinel_key)

    insufficient = request.model_copy(
        update={"readiness": request.readiness.model_copy(update={"postgresql_available_bytes": 1})}
    )
    with pytest.raises(RestorePlanError) as capacity:
        create_restore_plan(authenticated, insufficient, inspector=inspector)
    assert capacity.value.code == "RESTORE_CAPACITY_INSUFFICIENT"
    assert postgres.inspect(endpoint.database).user_object_count == 0
    assert objects.inspect().object_count == 0

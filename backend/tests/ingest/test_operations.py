from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import TEST_SESSIONS
from sqlalchemy import select

from app.core.idempotency import IdempotencyRecord
from app.domains.ingest import models, schemas
from app.platform.ports.fakes import FakeObjectStoragePort
from app.platform.storage import get_object_storage

ROOT = "/api/v1/projects/prj_fx_01/regions/cn-shanghai"


def idem(number: int) -> str:
    return f"idempotency-key-{number:04d}"


def source_command(name: str = "Shanghai collector A") -> dict:
    return {
        "name": name,
        "source_type": "ROBOT",
        "source_format": "LEROBOT_V2",
        "source_format_version": "2.1",
        "binding": {"kind": "ROBOT", "robot_id": "robot_fx_01"},
        "configuration": {
            "kind": "ROBOT",
            "transport": "HTTPS",
            "endpoint_ref": "endpoint_ref_fx_01",
            "tls_profile_id": "tls_fx_01",
        },
        "upload_policy_code": "STANDARD",
        "credential_input": None,
    }


async def mutate(client, method: str, url: str, headers: dict, number: int, **kwargs):
    request_headers = {
        **headers,
        "Idempotency-Key": idem(number),
        **kwargs.pop("extra_headers", {}),
    }
    return await client.request(method, url, headers=request_headers, **kwargs)


async def create_enabled_source(client, headers: dict, base: int = 1) -> tuple[str, dict, str]:
    created = await mutate(
        client, "POST", f"{ROOT}/data-sources", headers, base, json=source_command()
    )
    assert created.status_code == 201, created.text
    schemas.DataSourceEnvelope.model_validate(created.json())
    source = created.json()["data"]
    enabled = await mutate(
        client,
        "POST",
        f"{ROOT}/data-sources/{source['id']}:enable",
        headers,
        base + 1,
        extra_headers={"If-Match": created.headers["etag"]},
        json={"reason": "ready", "expected_administrative_state": "DISABLED"},
    )
    assert enabled.status_code == 200, enabled.text
    return source["id"], enabled.json()["data"], enabled.headers["etag"]


async def create_upload(client, headers: dict, source: dict, number: int = 20):
    response = await mutate(
        client,
        "POST",
        f"{ROOT}/upload-sessions",
        headers,
        number,
        json={
            "data_source_id": source["id"],
            "target_dataset_id": None,
            "source_format": source["source_format"],
            "source_format_version": source["source_format_version"],
            "expected_source_versions": {
                "configuration_version": source["config_version"],
                "credential_version": source["credential_version"],
                "upload_policy_version": "1",
            },
            "objects": [
                {
                    "client_object_id": "client_object_fx_01",
                    "relative_path": "data/chunk-000.parquet",
                    "size_bytes": "100",
                    "media_type": "application/vnd.apache.parquet",
                    "last_modified_at": None,
                    "declared_sha256": None,
                }
            ],
            "client_capabilities": {
                "supports_web_worker_hash": True,
                "supports_crc64": True,
                "supports_background_continuation": True,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response


@pytest.mark.asyncio
async def test_all_standard_operations_happy_path(client, headers) -> None:
    created = await mutate(
        client, "POST", f"{ROOT}/data-sources", headers, 1, json=source_command()
    )
    assert created.status_code == 201, created.text
    source = created.json()["data"]
    source_id = source["id"]

    source_list = await client.get(f"{ROOT}/data-sources", headers=headers)
    assert source_list.status_code == 200
    schemas.DataSourceCursorPageEnvelope.model_validate(source_list.json())
    source_page = await client.get(f"{ROOT}/data-sources/page", headers=headers)
    assert source_page.status_code == 200
    schemas.DataSourcePageEnvelope.model_validate(source_page.json())
    fetched = await client.get(f"{ROOT}/data-sources/{source_id}", headers=headers)
    assert fetched.status_code == 200
    assert (
        await client.get(
            f"{ROOT}/data-sources/{source_id}",
            headers={**headers, "If-None-Match": fetched.headers["etag"]},
        )
    ).status_code == 304

    update_body = source_command("Shanghai collector A2")
    update_body.pop("source_type")
    update_body.pop("credential_input")
    update_body["change_reason"] = "normalize display name"
    updated = await mutate(
        client,
        "PATCH",
        f"{ROOT}/data-sources/{source_id}",
        headers,
        2,
        extra_headers={"If-Match": created.headers["etag"]},
        json=update_body,
    )
    assert updated.status_code == 200, updated.text

    rotated = await mutate(
        client,
        "POST",
        f"{ROOT}/data-sources/{source_id}:rotate-credential",
        headers,
        3,
        extra_headers={"If-Match": updated.headers["etag"]},
        json={"credential_input": {"kind": "TOKEN", "token": "write-only"}, "reason": "rotation"},
    )
    assert rotated.status_code == 200, rotated.text
    assert "write-only" not in rotated.text
    source = rotated.json()["data"]

    connection = await mutate(
        client,
        "POST",
        f"{ROOT}/data-sources/{source_id}:test-connection",
        headers,
        4,
        extra_headers={"If-Match": rotated.headers["etag"]},
        json={
            "observed_config_version": source["config_version"],
            "observed_credential_version": source["credential_version"],
        },
    )
    assert connection.status_code == 202, connection.text
    schemas.ConnectionTestJobEnvelope.model_validate(connection.json())
    assert connection.headers["location"].startswith("/api/v1/jobs/")

    enabled = await mutate(
        client,
        "POST",
        f"{ROOT}/data-sources/{source_id}:enable",
        headers,
        5,
        extra_headers={"If-Match": rotated.headers["etag"]},
        json={"reason": "ready", "expected_administrative_state": "DISABLED"},
    )
    assert enabled.status_code == 200, enabled.text
    disabled = await mutate(
        client,
        "POST",
        f"{ROOT}/data-sources/{source_id}:disable",
        headers,
        6,
        extra_headers={"If-Match": enabled.headers["etag"]},
        json={"reason": "maintenance", "expected_administrative_state": "ENABLED"},
    )
    assert disabled.status_code == 200, disabled.text
    enabled = await mutate(
        client,
        "POST",
        f"{ROOT}/data-sources/{source_id}:enable",
        headers,
        7,
        extra_headers={"If-Match": disabled.headers["etag"]},
        json={"reason": "ready", "expected_administrative_state": "DISABLED"},
    )
    assert enabled.status_code == 200, enabled.text
    source = enabled.json()["data"]

    metrics = await client.get(f"{ROOT}/upload-sessions:summary", headers=headers)
    assert metrics.status_code == 200
    schemas.UploadSessionMetricsEnvelope.model_validate(metrics.json())
    options = await client.get(f"{ROOT}/upload-sessions:creation-options", headers=headers)
    assert options.status_code == 200
    schemas.UploadCreationOptionsEnvelope.model_validate(options.json())
    upload_response = await create_upload(client, headers, source, 8)
    upload_data = upload_response.json()["data"]
    schemas.UploadHandoffEnvelope.model_validate(upload_response.json())
    upload = upload_data["upload_session"]
    upload_id = upload["upload_id"]
    object_id = upload_data["secret"]["object_plans"][0]["upload_object_id"]
    authorization_id = upload_data["secret"]["authorization"]["authorization_id"]
    replayed_upload = await create_upload(client, headers, source, 8)
    assert replayed_upload.json()["data"]["upload_session"]["upload_id"] == upload_id
    assert replayed_upload.json()["data"]["secret"]["authorization"]["expires_at"]
    async with TEST_SESSIONS() as session:
        receipts = list((await session.scalars(select(IdempotencyRecord))).all())
        assert "access_key_secret" not in str(
            [item.response_body_or_resource_ref for item in receipts]
        )
        assert "object_key" not in str([item.response_body_or_resource_ref for item in receipts])
    assert (await client.get(f"{ROOT}/upload-sessions", headers=headers)).status_code == 200
    bootstrap = await client.get(f"{ROOT}/upload-sessions/{upload_id}/bootstrap", headers=headers)
    assert bootstrap.status_code == 200
    schemas.UploadSessionBootstrapEnvelope.model_validate(bootstrap.json())
    assert (
        await client.get(
            f"{ROOT}/upload-sessions/{upload_id}/bootstrap",
            headers={**headers, "If-None-Match": bootstrap.headers["etag"]},
        )
    ).status_code == 304

    renewed = await mutate(
        client,
        "POST",
        f"{ROOT}/upload-sessions/{upload_id}:renew-upload-authorization",
        headers,
        9,
        extra_headers={"If-Match": upload_response.headers["etag"]},
        json={
            "expected_authorization_id": authorization_id,
            "observed_resource_version": upload["resource_version"],
            "reason": "EXPIRING",
        },
    )
    assert renewed.status_code == 200, renewed.text
    paused = await mutate(
        client,
        "POST",
        f"{ROOT}/upload-sessions/{upload_id}:pause",
        headers,
        10,
        extra_headers={"If-Match": upload_response.headers["etag"]},
        json={"expected_lifecycle_status": "UPLOADING", "reason": "operator pause"},
    )
    assert paused.status_code == 200, paused.text
    resumed = await mutate(
        client,
        "POST",
        f"{ROOT}/upload-sessions/{upload_id}:resume",
        headers,
        11,
        extra_headers={"If-Match": paused.headers["etag"]},
        json={
            "expected_lifecycle_status": "PAUSED",
            "reason": "operator resume",
            "recovery_descriptor_version": paused.json()["data"]["resource_version"],
        },
    )
    assert resumed.status_code == 200, resumed.text
    retried = await mutate(
        client,
        "POST",
        f"{ROOT}/upload-sessions/{upload_id}:retry-failed-parts",
        headers,
        12,
        extra_headers={"If-Match": resumed.headers["etag"]},
        json={
            "requested_parts": [
                {
                    "upload_object_id": object_id,
                    "part_number": "1",
                    "observed_attempt": "0",
                    "observed_error_code": "NETWORK_TIMEOUT",
                }
            ],
            "reason": "transient transport error",
        },
    )
    assert retried.status_code == 200, retried.text
    schemas.UploadPartRetryEnvelope.model_validate(retried.json())
    storage = get_object_storage()
    assert isinstance(storage, FakeObjectStoragePort)
    storage.put_object_fact(
        "platform-ingest",
        f"sessions/{upload_id}/{object_id}",
        size_bytes=100,
        etag="provider-object-etag",
    )
    assert (
        await client.get(f"{ROOT}/upload-sessions/{upload_id}/objects", headers=headers)
    ).status_code == 200
    assert (
        await client.get(
            f"{ROOT}/upload-sessions/{upload_id}/objects/{object_id}/parts", headers=headers
        )
    ).status_code == 200

    manifest = {
        "schema_version": "source-upload-manifest/v1",
        "canonicalization": "RFC8785",
        "hash_algorithm": "SHA256",
        "source_format": "LEROBOT_V2",
        "source_format_version": "2.1",
        "objects": [
            {
                "upload_object_id": object_id,
                "relative_path": "data/chunk-000.parquet",
                "size_bytes": "100",
                "sha256": "a" * 64,
                "parts": [
                    {
                        "part_number": "1",
                        "provider_etag": "provider-etag-1",
                        "checksum_algorithm": "CRC64_ECMA",
                        "checksum_value": "checksum-1",
                    }
                ],
            }
        ],
    }
    manifest_sha = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    object_set = [
        {
            "upload_object_id": object_id,
            "relative_path": "data/chunk-000.parquet",
            "size_bytes": "100",
            "sha256": "a" * 64,
        }
    ]
    object_set_hash = hashlib.sha256(
        json.dumps(object_set, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    submitted = await mutate(
        client,
        "POST",
        f"{ROOT}/upload-sessions/{upload_id}:submit-manifest",
        headers,
        13,
        extra_headers={"If-Match": retried.headers["etag"]},
        json={
            "expected_object_set_hash": object_set_hash,
            "manifest_sha256": manifest_sha,
            "manifest": manifest,
        },
    )
    assert submitted.status_code == 202, submitted.text
    schemas.VerificationRunJobEnvelope.model_validate(submitted.json())
    run_id = submitted.json()["data"]["verification_run"]["verification_run_id"]
    assert (
        await client.get(f"{ROOT}/upload-sessions/{upload_id}/source-manifest", headers=headers)
    ).status_code == 200
    assert (
        await client.get(
            f"{ROOT}/upload-sessions/{upload_id}/source-manifest/nodes", headers=headers
        )
    ).status_code == 200
    assert (
        await client.get(f"{ROOT}/upload-sessions/{upload_id}/verification-runs", headers=headers)
    ).status_code == 200
    assert (
        await client.get(
            f"{ROOT}/upload-sessions/{upload_id}/verification-runs/{run_id}/findings",
            headers=headers,
        )
    ).status_code == 200
    assert (
        await client.get(f"{ROOT}/upload-sessions/{upload_id}/events", headers=headers)
    ).status_code == 200
    current = (
        await client.get(f"{ROOT}/upload-sessions/{upload_id}/bootstrap", headers=headers)
    ).json()["data"]["session"]
    cancelled = await mutate(
        client,
        "POST",
        f"{ROOT}/upload-sessions/{upload_id}:cancel",
        headers,
        14,
        extra_headers={"If-Match": current["etag"]},
        json={"expected_lifecycle_status": "PENDING_VERIFY", "reason": "operator cancel"},
    )
    assert cancelled.status_code == 202, cancelled.text
    schemas.UploadJobEnvelope.model_validate(cancelled.json())


@pytest.mark.asyncio
async def test_quarantine_retry_and_replacement_happy_paths(client, headers) -> None:
    _source_id, source, _etag = await create_enabled_source(client, headers, 100)
    upload_response = await create_upload(client, headers, source, 102)
    upload = upload_response.json()["data"]["upload_session"]
    upload_id = upload["upload_id"]
    run_id = "verification_run_fx_failed"
    quarantine_id = "quarantine_fx_01"
    object_set_hash = "b" * 64
    manifest_sha = "c" * 64
    now = datetime.now(UTC)
    run_projection = {
        "verification_run_id": run_id,
        "supersedes_run_id": None,
        "object_set_hash": object_set_hash,
        "manifest_sha256": manifest_sha,
        "adapter_version": "adapter-v1",
        "schema_ref": None,
        "status": "FAILED",
        "stages": [
            {
                "code": "OBJECT_SHA256",
                "status": "FAILED",
                "started_at": now.isoformat(),
                "finished_at": now.isoformat(),
                "job_id": "job_fx_failed",
                "finding_count": "1",
                "retryable": False,
                "skip_reason": None,
            }
        ],
        "finding_counts": {"info": "0", "warning": "0", "error": "1"},
        "job_id": "job_fx_failed",
        "started_at": now.isoformat(),
        "finished_at": now.isoformat(),
        "created_at": now.isoformat(),
        "resource_version": "1",
    }
    quarantine_projection = {
        "quarantine_id": quarantine_id,
        "upload_id": upload_id,
        "verification_run_id": run_id,
        "object_set_hash": object_set_hash,
        "manifest_sha256": manifest_sha,
        "reason_code": "OBJECT_SHA256_MISMATCH",
        "safe_summary": "Digest mismatch.",
        "disposition": "OPEN",
        "retain_until": (now + timedelta(days=30)).isoformat(),
        "created_at": now.isoformat(),
        "superseded_by_quarantine_id": None,
    }
    async with TEST_SESSIONS() as session, session.begin():
        row = await session.get(models.UploadSession, upload_id)
        row.lifecycle_status = "QUARANTINED"
        row.verification_status = "FAILED"
        row.resource_version = 2
        row.etag = '"upload-rv-2"'
        row.current_verification_run_id = run_id
        row.current_quarantine_id = quarantine_id
        row.object_set_hash = object_set_hash
        row.manifest_sha256 = manifest_sha
        projection = dict(row.projection)
        projection.update(
            lifecycle_status="QUARANTINED",
            verification_status="FAILED",
            resource_version="2",
            etag=row.etag,
            latest_verification_run_id=run_id,
        )
        row.projection = projection
        session.add(
            models.VerificationRun(
                verification_run_id=run_id,
                upload_id=upload_id,
                supersedes_run_id=None,
                job_id="job_fx_failed",
                object_set_hash=object_set_hash,
                manifest_sha256=manifest_sha,
                status="FAILED",
                resource_version=1,
                projection=run_projection,
                created_at=now,
            )
        )
        session.add(
            models.Quarantine(
                quarantine_id=quarantine_id,
                upload_id=upload_id,
                verification_run_id=run_id,
                object_set_hash=object_set_hash,
                manifest_sha256=manifest_sha,
                disposition="OPEN",
                resource_version=1,
                projection=quarantine_projection,
                created_at=now,
            )
        )

    replacement = await mutate(
        client,
        "POST",
        f"{ROOT}/upload-sessions/{upload_id}:create-replacement",
        headers,
        103,
        extra_headers={"If-Match": '"upload-rv-2"'},
        json={
            "reason": "replace corrupted object",
            "replacement_reason_code": "DIGEST_MISMATCH",
            "target_dataset_id": None,
            "objects": [
                {
                    "client_object_id": "client_object_replacement",
                    "relative_path": "data/replacement.parquet",
                    "size_bytes": "100",
                    "media_type": "application/vnd.apache.parquet",
                    "last_modified_at": None,
                    "declared_sha256": None,
                }
            ],
        },
    )
    assert replacement.status_code == 201, replacement.text
    schemas.UploadHandoffEnvelope.model_validate(replacement.json())
    assert replacement.json()["data"]["upload_session"]["supersedes_upload_id"] == upload_id

    async with TEST_SESSIONS() as session, session.begin():
        quarantine = await session.get(models.Quarantine, quarantine_id)
        quarantine.disposition = "OPEN"
        projection = dict(quarantine.projection)
        projection["disposition"] = "OPEN"
        quarantine.projection = projection
    retried = await mutate(
        client,
        "POST",
        f"{ROOT}/upload-sessions/{upload_id}:retry-verification",
        headers,
        104,
        extra_headers={"If-Match": '"upload-rv-2"'},
        json={
            "failed_verification_run_id": run_id,
            "expected_object_set_hash": object_set_hash,
            "expected_manifest_sha256": manifest_sha,
            "reason": "operator approved retry",
        },
    )
    assert retried.status_code == 202, retried.text
    schemas.VerificationRunJobEnvelope.model_validate(retried.json())
    assert retried.json()["data"]["verification_run"]["supersedes_run_id"] == run_id


@pytest.mark.asyncio
async def test_representative_403_404_409_and_422(client, headers) -> None:
    denied_headers = {
        **headers,
        "Authorization": "Bearer test:user_fx_01:ingest_source.read",
        "Idempotency-Key": idem(300),
    }
    denied = await client.post(
        f"{ROOT}/data-sources", headers=denied_headers, json=source_command()
    )
    assert denied.status_code == 403
    missing = await client.get(f"{ROOT}/data-sources/source_missing", headers=headers)
    assert missing.status_code == 404
    _source_id, source, etag = await create_enabled_source(client, headers, 301)
    conflict = await mutate(
        client,
        "POST",
        f"{ROOT}/data-sources/{source['id']}:enable",
        headers,
        303,
        extra_headers={"If-Match": etag},
        json={"reason": "again", "expected_administrative_state": "ENABLED"},
    )
    assert conflict.status_code == 409
    invalid = await mutate(
        client,
        "POST",
        f"{ROOT}/data-sources",
        headers,
        304,
        json={**source_command(), "name": ""},
    )
    assert invalid.status_code == 422
    assert set(invalid.json()["error"]) == {
        "code",
        "message",
        "field_errors",
        "operation_errors",
        "blocked_reasons",
        "request_id",
        "retryable",
    }

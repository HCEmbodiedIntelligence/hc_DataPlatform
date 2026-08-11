from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from conftest import TEST_SESSIONS

from app.core.idempotency import IdempotencyRecord
from app.main import create_app

SPEC_FILES = (
    "/home/czy/plan/backend/01-ingest/ingest-api.openapi.yaml",
    "/home/czy/plan/backend/02-dataset-version-review/dataset-version-review-api.openapi.yaml",
    "/home/czy/plan/backend/03-manual-cleaning/manual-cleaning-api.openapi.yaml",
    "/home/czy/plan/backend/04-storage-lifecycle/storage-lifecycle-api.openapi.yaml",
    "/home/czy/plan/backend/05-robotics-calibration-schema/robotics-calibration-schema-api.openapi.yaml",
    "/home/czy/plan/backend/06-access-audit/access-audit-api.openapi.yaml",
    "/home/czy/plan/backend/07-platform-foundation/platform-api-baseline.yaml",
    "/home/czy/plan/backend/09-data-annotation/data-annotation-api.openapi.yaml",
)


def _operation_ids(document: dict) -> set[str]:
    return {
        operation["operationId"]
        for path_item in document.get("paths", {}).values()
        for operation in path_item.values()
        if isinstance(operation, dict) and operation.get("operationId")
    }


def _headers(*capabilities: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer test:integration:{','.join(capabilities)}",
        "X-Organization-Id": "org_fx_01",
        "X-Project-Id": "prj_fx_01",
        "X-Region-Code": "cn-shanghai-1",
        "X-Client-Version": "test-1",
        "Accept": "application/json",
    }


async def _insert_completed_export_job() -> None:
    now = datetime.now(UTC)
    async with TEST_SESSIONS.begin() as session:
        session.add(
            IdempotencyRecord(
                record_id="idem_record_platform_job",
                scope=(
                    '{"actor_id":"integration","operation_id":"createDatasetManifestListExport",'
                    '"organization_id":"org_fx_01","project_id":"prj_fx_01",'
                    '"region_code":"cn-shanghai-1"}'
                ),
                idempotency_key="create-export-job-fixture",
                request_hash=None,
                state="COMPLETED",
                http_status=202,
                response_schema_version="1",
                response_body_or_resource_ref={
                    "job": {
                        "job_id": "job_export_fx_01",
                        "kind": "DATASET_EXPORT",
                        "status": "SUCCEEDED",
                        "etag": '"rv-2"',
                    }
                },
                async_job_id="job_export_fx_01",
                created_at=now,
                locked_until=None,
                completed_at=now,
                expires_at=now + timedelta(days=1),
            )
        )


def test_runtime_openapi_is_the_exact_union_of_all_eight_domain_specs() -> None:
    expected: set[str] = set()
    for path in SPEC_FILES:
        expected |= _operation_ids(yaml.safe_load(Path(path).read_text()))
    runtime = _operation_ids(create_app().openapi())
    assert len(expected) == 256
    assert runtime == expected


@pytest.mark.asyncio
async def test_platform_job_polling_is_scope_safe_and_conditional(client) -> None:
    await _insert_completed_export_job()
    response = await client.get(
        "/api/v1/jobs/job_export_fx_01",
        headers=_headers("dataset.read"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "DATASET_EXPORT"
    assert response.json()["scope"]["project_id"] == "prj_fx_01"
    not_modified = await client.get(
        "/api/v1/jobs/job_export_fx_01",
        headers={**_headers("dataset.read"), "If-None-Match": response.headers["etag"]},
    )
    assert not_modified.status_code == 304
    forbidden = await client.get(
        "/api/v1/jobs/job_export_fx_01",
        headers=_headers("audit.read"),
    )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_export_download_authorization_is_ephemeral_and_idempotent(client) -> None:
    await _insert_completed_export_job()
    headers = {
        **_headers("dataset.read"),
        "Idempotency-Key": "authorize-export-fixture",
    }
    first = await client.post(
        "/api/v1/projects/prj_fx_01/export-jobs/job_export_fx_01/download-authorizations",
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "private, no-store"
    assert first.json()["data"]["transport"] == "STREAM"
    replay = await client.post(
        "/api/v1/projects/prj_fx_01/export-jobs/job_export_fx_01/download-authorizations",
        headers=headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["grant_id"] == first.json()["data"]["grant_id"]

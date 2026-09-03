"""Authenticated, immutable device CAPTURED/SAVED fact ingestion."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.security.auth import AuthContext

from .models import DeviceCaptureFact, DeviceCaptureFactRequest


def _fingerprint(request: DeviceCaptureFactRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class DeviceCaptureFactRepository(Protocol):
    def put_fact(
        self,
        fact: DeviceCaptureFact,
        *,
        source_fingerprint: str,
    ) -> DeviceCaptureFact: ...


class InMemoryDeviceCaptureFactRepository:
    def __init__(self) -> None:
        self._facts: dict[tuple[str, str, str, str, str], tuple[str, DeviceCaptureFact]] = {}
        self._lock = RLock()

    def put_fact(self, fact: DeviceCaptureFact, *, source_fingerprint: str) -> DeviceCaptureFact:
        identity = (
            fact.organization_id,
            fact.project_id,
            fact.region_code,
            fact.device_id,
            fact.source_event_id,
        )
        with self._lock:
            existing = self._facts.get(identity)
            if existing is None:
                self._facts[identity] = (source_fingerprint, fact)
                return fact
            if existing[0] != source_fingerprint:
                raise _immutable_conflict()
            return existing[1]


class DeviceCaptureFactService:
    def __init__(self, repository: DeviceCaptureFactRepository) -> None:
        self._repository = repository

    def record(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        request: DeviceCaptureFactRequest,
    ) -> DeviceCaptureFact:
        if not auth.service_identity:
            raise problem(
                status=403,
                code="DEVICE_SERVICE_IDENTITY_REQUIRED",
                title="Device service identity required",
                detail=(
                    "Only an authenticated device-agent service identity may assert capture facts."
                ),
            )
        if organization_id not in auth.organization_ids:
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization scope denied",
                detail="The device identity does not belong to the requested organization.",
            )
        fingerprint = _fingerprint(request)
        fact = DeviceCaptureFact(
            **request.model_dump(),
            fact_id=uuid5(
                NAMESPACE_URL,
                "/".join(
                    (
                        "hc-data-platform/device-capture-fact",
                        organization_id,
                        project_id,
                        region_code,
                        request.device_id,
                        request.source_event_id,
                    )
                ),
            ),
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            producer_subject_id=auth.subject_id,
            received_at=datetime.now(timezone.utc),
        )
        return self._repository.put_fact(fact, source_fingerprint=fingerprint)


class PostgresDeviceCaptureFactRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def put_fact(self, fact: DeviceCaptureFact, *, source_fingerprint: str) -> DeviceCaptureFact:
        context = current_request_context()
        if (
            context.organization_id != fact.organization_id
            or context.project_id != fact.project_id
            or context.region_code != fact.region_code
            or context.subject_id != fact.producer_subject_id
            or not context.service_identity
        ):
            raise ValueError("device capture fact scope does not match request context")

        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT 1
                    FROM ingest.collection_jobs job
                    WHERE job.organization_id = %s
                      AND job.project_id = %s
                      AND job.region_code = %s
                      AND job.task_id = %s
                      AND job.collection_job_id = %s
                      AND job.robot_id = %s
                    """,
                    (
                        fact.organization_id,
                        fact.project_id,
                        fact.region_code,
                        fact.collection_task_id,
                        fact.collection_job_id,
                        fact.robot_id,
                    ),
                )
                if cursor.fetchone() is None:
                    raise problem(
                        status=409,
                        code="DEVICE_COLLECTION_ASSIGNMENT_MISMATCH",
                        title="Device collection assignment mismatch",
                        detail=(
                            "The asserted task, collection job, and robot are not an exact "
                            "match in the authorized scope."
                        ),
                    )
                cursor.execute(
                    """
                    INSERT INTO ingest.device_capture_facts (
                        fact_id, organization_id, project_id, region_code,
                        source_event_id, event_type, collection_task_id,
                        collection_job_id, recording_request_id, data_package_id,
                        robot_id, device_id, device_sequence_no,
                        capture_started_at, capture_ended_at, saved_at,
                        local_artifact_size, local_artifact_sha256, recorder_version,
                        occurred_at, received_at, producer_subject_id,
                        source_fingerprint, fact_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
                    ON CONFLICT (
                        organization_id, project_id, region_code, device_id, source_event_id
                    ) DO NOTHING
                    """,
                    (
                        fact.fact_id,
                        fact.organization_id,
                        fact.project_id,
                        fact.region_code,
                        fact.source_event_id,
                        fact.event_type.value,
                        fact.collection_task_id,
                        fact.collection_job_id,
                        fact.recording_request_id,
                        fact.data_package_id,
                        fact.robot_id,
                        fact.device_id,
                        fact.device_sequence_no,
                        fact.capture_started_at,
                        fact.capture_ended_at,
                        fact.saved_at,
                        fact.local_artifact_size,
                        fact.local_artifact_sha256,
                        fact.recorder_version,
                        fact.occurred_at,
                        fact.received_at,
                        fact.producer_subject_id,
                        source_fingerprint,
                        fact.model_dump_json(),
                    ),
                )
                cursor.execute(
                    """
                    SELECT source_fingerprint, fact_json
                    FROM ingest.device_capture_facts
                    WHERE organization_id = %s
                      AND project_id = %s
                      AND region_code = %s
                      AND device_id = %s
                      AND source_event_id = %s
                    """,
                    (
                        fact.organization_id,
                        fact.project_id,
                        fact.region_code,
                        fact.device_id,
                        fact.source_event_id,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("persisted device capture fact is not visible in its scope")
                if str(row[0]) != source_fingerprint:
                    raise _immutable_conflict()
                persisted = DeviceCaptureFact.model_validate(row[1])
            connection.commit()
            return persisted
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def _immutable_conflict() -> Exception:
    return problem(
        status=409,
        code="DEVICE_CAPTURE_FACT_IMMUTABLE",
        title="Device capture fact is immutable",
        detail="This device source event already exists with different content.",
    )

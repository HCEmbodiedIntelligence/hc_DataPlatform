"""Durable whole-recording quality checks, before any slice authoring."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.audit import canonical_hash

from .content_duplicates import check_original_mcap
from .models import RecordingQualitySummary, RecordingScope, RecordingStatus

EVENT_TYPE = "continuous-recording.quality.requested.v1"
WORKFLOW_NAME = "ContinuousRecordingQualityWorkflow"


def quality_request(recording, request_id=None):
    scope = recording.scope
    identity = "/".join(
        (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            recording.recording_id,
            recording.source_sha256,
        )
    )
    workflow_id = "recording-quality:" + str(uuid5(NAMESPACE_URL, identity))
    if request_id is not None:
        workflow_id += ":retry:" + str(request_id)
    now = datetime.now(timezone.utc)
    event = DomainEventEnvelope(
        event_id=str(uuid5(NAMESPACE_URL, workflow_id)),
        event_type=EVENT_TYPE,
        aggregate_type="continuous_recording",
        aggregate_id=recording.recording_id,
        organization_id=scope.organization_id,
        project_id=scope.project_id,
        region_code=scope.region_code,
        occurred_at=now,
        payload={
            **scope.model_dump(),
            "recording_id": recording.recording_id,
            "source_sha256": recording.source_sha256,
            "workflow_id": workflow_id,
        },
    )
    return recording.model_copy(
        update={
            "status": RecordingStatus.PENDING_QC,
            "quality_workflow_id": workflow_id,
            "quality_error_code": None,
            "quality_summary": None,
            "updated_at": now,
        }
    ), event


def enqueue_quality(cursor, event):
    cursor.execute(
        """INSERT INTO core.outbox_events
        (event_id,organization_id,project_id,region_code,event_type,envelope,occurred_at,
         available_at,published_at,publish_attempts)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,NULL,0)
        ON CONFLICT (event_id) DO NOTHING""",
        (
            event.event_id,
            event.organization_id,
            event.project_id,
            event.region_code,
            event.event_type,
            event.model_dump_json(exclude_none=True),
            event.occurred_at,
            event.occurred_at,
        ),
    )


def save_quality_state(cursor, recording):
    cursor.execute(
        """UPDATE ingest.continuous_recordings SET status=%s,recording_document=%s::jsonb,
        updated_at=%s WHERE organization_id=%s AND project_id=%s AND region_code=%s
        AND recording_id=%s""",
        (
            recording.status.value,
            recording.model_dump_json(),
            recording.updated_at,
            recording.scope.organization_id,
            recording.scope.project_id,
            recording.scope.region_code,
            recording.recording_id,
        ),
    )


def prepare_quality_retry(recording, request_id):
    updated, event = quality_request(recording, request_id)
    if recording.quality_workflow_id == updated.quality_workflow_id:
        return recording, None
    if recording.status not in {RecordingStatus.QC_FAILED, RecordingStatus.QC_ERROR}:
        raise problem(
            status=409,
            code="RECORDING_QUALITY_RETRY_NOT_ALLOWED",
            title="当前录制不能重新质检",
            detail="仅质检未通过或执行失败的录制可以重试。",
        )
    return updated, event


class RecordingQualityOutboxHandler:
    EVENT_TYPE = EVENT_TYPE

    def __init__(self, launcher):
        self.launcher = launcher

    async def __call__(self, event):
        request = event.payload
        if (
            event.event_type != EVENT_TYPE
            or not event.organization_id
            or not event.region_code
            or request.get("organization_id") != event.organization_id
            or request.get("project_id") != event.project_id
            or request.get("region_code") != event.region_code
            or request.get("recording_id") != event.aggregate_id
            or not request.get("source_sha256")
            or not request.get("workflow_id")
        ):
            raise ValueError("recording quality event differs from its source scope")
        return await self.launcher.start(
            workflow_name=WORKFLOW_NAME,
            workflow_input=request,
            workflow_id=request["workflow_id"],
            job_type=WORKFLOW_NAME,
            project_id=event.project_id,
            resource_id=event.aggregate_id,
        )


class RecordingQualityService:
    def __init__(self, connection_factory, resolver, processor, *, storage=None):
        self.connection_factory = connection_factory
        self.resolver = resolver
        self.processor = processor
        self.storage = storage

    @staticmethod
    def _current(cursor, request):
        from .repository import PostgresContinuousRecordingRepository

        scope = RecordingScope(
            **{key: request[key] for key in ("organization_id", "project_id", "region_code")}
        )
        recording = PostgresContinuousRecordingRepository._get_locked(
            cursor, scope, request["recording_id"]
        )
        if recording is None:
            raise ValueError("recording quality source is missing")
        if recording.source_sha256 != request["source_sha256"]:
            raise ValueError("recording quality source changed")
        if recording.quality_workflow_id != request["workflow_id"]:
            raise ValueError("stale recording quality attempt")
        return recording

    def run(self, request):
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                # Keep a session lock while decoding, without a long SQL transaction.
                cursor.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (request["workflow_id"],)
                )
                if not cursor.fetchone()[0]:
                    raise RuntimeError("recording quality attempt is already running")
                recording = self._current(cursor, request)
                if recording.quality_summary is not None:
                    if recording.quality_summary.status == "PASS":
                        self._continue_processing(cursor, recording, request)
                    connection.commit()
                    return recording.quality_summary.model_dump(mode="json")
                if recording.status not in {RecordingStatus.PENDING_QC, RecordingStatus.QC_RUNNING}:
                    raise ValueError("recording is not awaiting quality checks")
                save_quality_state(
                    cursor,
                    recording.model_copy(
                        update={
                            "status": RecordingStatus.QC_RUNNING,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    ),
                )
                connection.commit()
            resolved = self.resolver.resolve_recording(
                organization_id=request["organization_id"],
                project_id=request["project_id"],
                region_code=request["region_code"],
                recording_id=request["recording_id"],
            )
            document = self.processor.inspect_quality(resolved, recording_quality=True)
            with connection.cursor() as cursor:
                recording = self._current(cursor, request)
                check_original_mcap(cursor, recording, document)
                digest = canonical_hash(document)
                summary = RecordingQualitySummary(
                    report_id=f"recording_qc_{digest}",
                    status=document["status"],
                    finding_codes=sorted(
                        set(document["finding_codes"])
                        | {
                            finding["code"]
                            for finding in document["quality_report"]["findings"]
                            if finding["severity"] != "info"
                        }
                    ),
                    warning_codes=document["warning_codes"],
                    checked_at=datetime.now(timezone.utc),
                )
                cursor.execute(
                    """INSERT INTO ingest.recording_quality_reports
                    (organization_id,project_id,region_code,recording_id,report_id,report_document)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                    (
                        recording.scope.organization_id,
                        recording.scope.project_id,
                        recording.scope.region_code,
                        recording.recording_id,
                        summary.report_id,
                        json.dumps(document),
                    ),
                )
                updated = recording.model_copy(update={
                    "status": RecordingStatus.READY_FOR_SLICING if summary.status == "PASS"
                              else RecordingStatus.QC_FAILED,
                    "quality_summary": summary, "quality_error_code": None,
                    "updated_at": summary.checked_at,
                })
                save_quality_state(cursor, updated)
            # The quality report remains durable if dispatching ingestion needs a retry.
            connection.commit()
            if summary.status == "PASS":
                with connection.cursor() as cursor:
                    self._continue_processing(cursor, self._current(cursor, request), request)
                connection.commit()
            return summary.model_dump(mode="json")
        except Exception:
            connection.rollback()
            raise
        finally:
            # A closed session releases its advisory lock, including worker crashes.
            connection.close()

    def _continue_processing(self, cursor, recording, request):
        if recording.ingest_mode == "WHOLE_RECORDING":
            from .whole_recording import enqueue_whole_recording

            return enqueue_whole_recording(cursor, recording, request_id=request["workflow_id"])
        from .slice_reuse import finalize_previous

        finalize_previous(
            cursor, recording, storage=self.storage, actor_id="recording-quality-worker",
            request_id=request["workflow_id"],
        )

    def failed(self, request):
        connection = self.connection_factory()
        try:
            with connection.cursor() as cursor:
                recording = self._current(cursor, request)
                if recording.status in {RecordingStatus.PENDING_QC, RecordingStatus.QC_RUNNING}:
                    save_quality_state(
                        cursor,
                        recording.model_copy(
                            update={
                                "status": RecordingStatus.QC_ERROR,
                                "quality_error_code": request.get(
                                    "error_code", "RECORDING_QUALITY_FAILED"
                                ),
                                "updated_at": datetime.now(timezone.utc),
                            }
                        ),
                    )
            connection.commit()
        finally:
            connection.close()

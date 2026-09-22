"""Atomic robot Raw/job/upload/episode projection and durable outbox scheduling."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope

from .models import RobotIngestUpload, StrictModel
from .processing_contract import ProcessingDocument, ProcessingFailure, processing_result

EVENT_TYPE = "robot.ingest.processing.requested.v1"
WORKFLOW_NAME = "RobotIngestProcessingWorkflow"


class ProcessingTask(StrictModel):
    organization_id: str
    project_id: str
    region_code: str
    upload_id: str
    raw_source_id: str
    generation: int
    workflow_id: str

    @property
    def scope(self) -> tuple[str, str, str, str]:
        return self.organization_id, self.project_id, self.region_code, self.raw_source_id


def _parse(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _task(upload: RobotIngestUpload, generation: int) -> ProcessingTask:
    locator = uuid5(
        NAMESPACE_URL,
        f"robot-processing:{upload.target.organization_id}:{upload.upload_id}:{generation}",
    )
    return ProcessingTask(
        organization_id=upload.target.organization_id,
        project_id=upload.target.project_id,
        region_code=upload.target.region_code,
        upload_id=upload.upload_id,
        raw_source_id=upload.raw_source_id,
        generation=generation,
        workflow_id=f"robot-processing-{locator.hex}",
    )


def enqueue(
    cursor: Any,
    upload: RobotIngestUpload,
    *,
    generation: int = 0,
    document: ProcessingDocument | None = None,
) -> ProcessingTask:
    """Called within the Raw commit transaction; no network calls after commit."""
    task = _task(upload, generation)
    doc = document or ProcessingDocument()
    cursor.execute(
        """INSERT INTO ingest.robot_processing
        (organization_id,project_id,region_code,raw_source_id,upload_id,generation,workflow_id,document)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        ON CONFLICT (organization_id,project_id,region_code,raw_source_id) DO UPDATE
        SET generation=EXCLUDED.generation, workflow_id=EXCLUDED.workflow_id,
            document=EXCLUDED.document""",
        (*task.scope, task.upload_id, generation, task.workflow_id, doc.model_dump_json()),
    )
    cursor.execute(
        """UPDATE ingest.raw_ingest_jobs SET workflow_id=%s, attempts=%s,
        status='PENDING', last_error_code=NULL, updated_at=clock_timestamp()
        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND raw_source_id=%s""",
        (task.workflow_id, generation, *task.scope),
    )
    event = DomainEventEnvelope(
        event_id=str(uuid5(NAMESPACE_URL, task.workflow_id)),
        event_type=EVENT_TYPE,
        aggregate_type="raw_source",
        aggregate_id=task.raw_source_id,
        organization_id=task.organization_id,
        project_id=task.project_id,
        region_code=task.region_code,
        payload=task.model_dump(),
    )
    cursor.execute(
        """INSERT INTO core.outbox_events
        (event_id,organization_id,project_id,region_code,event_type,envelope,occurred_at,available_at)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT (event_id) DO NOTHING""",
        (
            event.event_id,
            task.organization_id,
            task.project_id,
            task.region_code,
            EVENT_TYPE,
            event.model_dump_json(),
            event.occurred_at,
            event.occurred_at,
        ),
    )
    return task


class ProcessingStore:
    def __init__(self, connections: Callable[[], Any]) -> None:
        self.connections = connections

    def _locked(
        self, cursor: Any, task: ProcessingTask
    ) -> tuple[RobotIngestUpload, ProcessingDocument]:
        cursor.execute(
            """SELECT upload_document FROM ingest.robot_ingest_uploads
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
              AND raw_source_id=%s AND upload_id=%s FOR UPDATE""",
            (*task.scope, task.upload_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ProcessingFailure("ROBOT_PROCESSING_SOURCE_NOT_FOUND")
        upload = RobotIngestUpload.model_validate(_parse(row[0]))
        cursor.execute(
            """SELECT generation,workflow_id,document FROM ingest.robot_processing
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
            FOR UPDATE""",
            task.scope,
        )
        row = cursor.fetchone()
        if row is None or (row[0], row[1]) != (task.generation, task.workflow_id):
            raise ProcessingFailure("ROBOT_PROCESSING_STALE_ATTEMPT")
        return upload, ProcessingDocument.model_validate(_parse(row[2]))

    def read(self, upload: RobotIngestUpload) -> tuple[ProcessingTask | None, ProcessingDocument]:
        with self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT generation,workflow_id,document FROM ingest.robot_processing
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
                AND upload_id=%s""",
                (
                    upload.target.organization_id,
                    upload.target.project_id,
                    upload.target.region_code,
                    upload.raw_source_id,
                    upload.upload_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None, ProcessingDocument(
                    phase="DONE", error_code="ROBOT_PROCESSING_NOT_SCHEDULED", retryable=True
                )
            task = _task(upload, row[0])
            if task.workflow_id != row[1]:
                raise ValueError("processing workflow identity drift")
            return task, ProcessingDocument.model_validate(_parse(row[2]))

    def load(self, task: ProcessingTask) -> tuple[RobotIngestUpload, ProcessingDocument]:
        with self.connections() as connection, connection.cursor() as cursor:
            return self._locked(cursor, task)

    def change(
        self, task: ProcessingTask, edit: Callable[[ProcessingDocument], ProcessingDocument]
    ) -> ProcessingDocument:
        with self.connections() as connection, connection.cursor() as cursor:
            upload, document = self._locked(cursor, task)
            updated = ProcessingDocument.model_validate(edit(document).model_dump())
            self._write(cursor, task, upload, updated)
            return updated

    def _write(
        self, cursor: Any, task: ProcessingTask, upload: RobotIngestUpload, doc: ProcessingDocument
    ) -> None:
        result = processing_result(upload, doc)
        now = datetime.now(timezone.utc)
        receipts = [ep.receipt for ep in doc.episodes if ep.receipt is not None]
        changes = dict(
            processing_status=result.processing_status,
            quality_status=result.quality_status,
            verified_episode_count=len(doc.episodes) if doc.episodes else None,
            verified_frame_count=sum(r.frame_count for r in receipts),
            verified_sample_count=sum(r.sample_count for r in receipts),
            qc_pass_episode_count=sum(r.quality_status == "PASS" for r in receipts),
            qc_risk_episode_count=sum(r.quality_status == "RISK" for r in receipts),
            qc_reject_episode_count=sum(r.quality_status == "REJECT" for r in receipts),
            updated_at=now,
        )
        updated = RobotIngestUpload.model_validate({**upload.model_dump(), **changes})
        cursor.execute(
            """UPDATE ingest.robot_processing SET document=%s::jsonb
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND raw_source_id=%s""",
            (doc.model_dump_json(), *task.scope),
        )
        cursor.execute(
            """UPDATE ingest.robot_ingest_uploads SET upload_document=%s::jsonb,updated_at=%s
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND raw_source_id=%s""",
            (updated.model_dump_json(), now, *task.scope),
        )
        cursor.execute(
            """UPDATE ingest.raw_sources SET processing_status=%s,quality_status=%s,
            verified_episode_count=%s,verified_frame_count=%s,verified_sample_count=%s,
            qc_pass_episode_count=%s,qc_risk_episode_count=%s,qc_reject_episode_count=%s,updated_at=%s
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND raw_source_id=%s""",
            (
                result.processing_status,
                result.quality_status.value,
                updated.verified_episode_count,
                updated.verified_frame_count,
                updated.verified_sample_count,
                updated.qc_pass_episode_count,
                updated.qc_risk_episode_count,
                updated.qc_reject_episode_count,
                now,
                *task.scope,
            ),
        )
        job_status = (
            ("PENDING" if doc.phase == "PENDING" else "RUNNING")
            if not result.terminal
            else {
                "READY": "SUCCEEDED",
                "PARTIALLY_FAILED": "PARTIALLY_FAILED",
                "FAILED": "FAILED",
            }[result.processing_status]
        )
        error = doc.error_code or next(
            (ep.error_code for ep in doc.episodes if ep.error_code), None
        )
        cursor.execute(
            """UPDATE ingest.raw_ingest_jobs SET status=%s,last_error_code=%s,updated_at=%s
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND raw_source_id=%s""",
            (job_status, error, now, *task.scope),
        )
        for ep in doc.episodes:
            r = ep.receipt
            cursor.execute(
                """INSERT INTO ingest.raw_source_episodes
                (organization_id,project_id,region_code,raw_source_id,episode_id,source_episode_index,
                 status,frame_count,sample_count,dataset_version,lance_version,quality_status,
                 qc_report_id,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (organization_id,project_id,region_code,raw_source_id,episode_id)
                DO UPDATE SET status=EXCLUDED.status,frame_count=EXCLUDED.frame_count,
                 sample_count=EXCLUDED.sample_count,dataset_version=EXCLUDED.dataset_version,
                 lance_version=EXCLUDED.lance_version,quality_status=EXCLUDED.quality_status,
                 qc_report_id=EXCLUDED.qc_report_id,updated_at=EXCLUDED.updated_at""",
                (
                    *task.scope,
                    ep.episode_id,
                    ep.source_episode_index,
                    ep.status,
                    r.frame_count if r else None,
                    r.sample_count if r else None,
                    r.dataset_version if r else None,
                    r.lance_version if r else None,
                    ep.quality_status.value,
                    r.qc_report_id if r else None,
                    now,
                    now,
                ),
            )

    def retry(self, upload: RobotIngestUpload, request_id: str) -> None:
        """Explicit retry or historical recovery; only immutable compatible robot uploads."""
        with self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT upload_document FROM ingest.robot_ingest_uploads
                WHERE organization_id=%s AND upload_id=%s AND ingest_identity_id=%s FOR UPDATE""",
                (upload.target.organization_id, upload.upload_id, upload.ingest_identity_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ProcessingFailure("ROBOT_PROCESSING_SOURCE_NOT_FOUND")
            upload = RobotIngestUpload.model_validate(_parse(row[0]))
            if not eligible(upload):
                raise problem(
                    status=409,
                    code="ROBOT_PROCESSING_INCOMPATIBLE",
                    title="Source cannot be processed",
                    detail="A complete committed LeRobot v3 robot upload is required.",
                )
            task = _task(upload, 0)
            cursor.execute(
                """SELECT generation,document FROM ingest.robot_processing
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND raw_source_id=%s""",
                task.scope,
            )
            row = cursor.fetchone()
            doc = (
                ProcessingDocument()
                if row is None
                else ProcessingDocument.model_validate(_parse(row[1]))
            )
            generation = 0 if row is None else row[0] + 1
            cursor.execute(
                """SELECT 1 FROM ingest.robot_processing_requests
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
                AND request_id=%s""",
                (*task.scope, request_id),
            )
            if cursor.fetchone():
                return
            if row is not None and (
                doc.phase != "DONE"
                or not (
                    doc.retryable
                    or any(ep.status == "FAILED" and ep.retryable for ep in doc.episodes)
                )
            ):
                raise problem(
                    status=409,
                    code="ROBOT_PROCESSING_RETRY_UNAVAILABLE",
                    title="Processing retry unavailable",
                    detail="Retry requires a terminal retryable failure.",
                )
            if row is None:
                cursor.execute(
                    """SELECT 1 FROM ingest.raw_source_episodes WHERE organization_id=%s
                    AND project_id=%s AND region_code=%s AND raw_source_id=%s LIMIT 1""",
                    task.scope,
                )
                if cursor.fetchone() or upload.processing_status.value not in {
                    "PENDING",
                    "DISCOVERING_EPISODES",
                }:
                    raise problem(
                        status=409,
                        code="ROBOT_PROCESSING_LEGACY_REVIEW_REQUIRED",
                        title="Historical processing needs review",
                        detail="Existing processing facts require operator reconciliation.",
                    )
            reset = ProcessingDocument(
                episodes=tuple(
                    ep.model_copy(
                        update={"status": "PENDING", "error_code": None, "retryable": False}
                    )
                    if ep.status == "FAILED" and ep.retryable
                    else ep
                    for ep in doc.episodes
                )
            )
            task = enqueue(cursor, upload, generation=generation, document=reset)
            self._write(cursor, task, upload, reset)
            cursor.execute(
                """INSERT INTO ingest.robot_processing_requests
                (organization_id,project_id,region_code,raw_source_id,request_id,generation)
                VALUES (%s,%s,%s,%s,%s,%s)""",
                (*task.scope, request_id, generation),
            )


def eligible(upload: RobotIngestUpload) -> bool:
    return (
        upload.state.value == "COMMITTED"
        and upload.raw_source_id is not None
        and upload.source_format == "LEROBOT_V3"
        and upload.source_format_version == "v3.0"
        and upload.capture_mode.value == "PRESEGMENTED"
        and bool(upload.assets)
        and all(a.state.value == "COMPLETED" for a in upload.assets)
    )

"""Read projection of direct uploads, receipt, recording QC and admission.

Count an upload once, and count Episodes separately. Never treat receipt or a
successful recording preparation as training-data admission.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CaptureProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str
    capture_mode: str
    rollout_id: str
    data_package_id: str
    collection_task_id: str
    robot_id: str
    dataset_id: str
    recording_id: str | None = None
    source_import_id: str | None = None
    upload_state: str
    stage: Literal[
        "UPLOADING",
        "RECEIVED",
        "PREPARING",
        "AWAITING_SLICE",
        "PROCESSING",
        "READY",
        "BLOCKED",
        "FAILED",
        "CANCELLED",
        "DISCARDED",
    ]
    received: bool
    duration_seconds: float | None = None
    episode_count: int = Field(ge=0)
    ready_episode_count: int = Field(ge=0)
    failed_episode_count: int = Field(ge=0)
    blocked_episode_count: int = Field(default=0, ge=0)
    qc_outcome: Literal["PASS", "RISK", "REJECT"] | None = None
    error_code: str | None = None
    created_at: datetime
    received_at: datetime | None = None


class CaptureCounts(BaseModel):
    upload_count: int = 0
    received_count: int = 0
    awaiting_slice_count: int = 0
    processing_count: int = 0
    failed_count: int = 0
    blocked_count: int = 0
    episode_count: int = 0
    ready_episode_count: int = 0
    failed_episode_count: int = 0


class CaptureProgressPage(BaseModel):
    items: tuple[CaptureProgress, ...]
    total: int
    counts: CaptureCounts


def capture_counts(items):
    return CaptureCounts(
        upload_count=len(items),
        received_count=sum(item.received for item in items),
        awaiting_slice_count=sum(item.stage == "AWAITING_SLICE" for item in items),
        processing_count=sum(
            item.stage in {"RECEIVED", "PREPARING", "PROCESSING"} for item in items
        ),
        failed_count=sum(item.stage == "FAILED" for item in items),
        blocked_count=sum(item.stage == "BLOCKED" for item in items),
        episode_count=sum(item.episode_count for item in items),
        ready_episode_count=sum(item.ready_episode_count for item in items),
        failed_episode_count=sum(item.failed_episode_count for item in items),
    )


def read_captures(cursor, organization_id, project_id, region_code, task_id=None):
    cursor.execute(
        """
        SELECT u.upload_id,u.collection_task_id,u.authenticated_robot_id,u.dataset_id,
               r.recording_id,u.state,u.committed_at,u.created_at,
               u.upload_document #>> '{manifest,capture_started_at}',
               u.upload_document #>> '{manifest,capture_ended_at}',
               r.status,j.status,j.last_error_code,
               COALESCE(episodes.items,'[]'::jsonb), u.capture_mode,
               COALESCE(r.rollout_id,u.upload_id), COALESCE(r.data_package_id,u.upload_id),
               u.raw_source_id
        FROM ingest.robot_ingest_uploads u
        LEFT JOIN ingest.continuous_recordings r
          ON r.organization_id=u.organization_id AND r.project_id=u.project_id
         AND r.region_code=u.region_code AND r.collection_job_id=u.collection_job_id
         AND r.recording_id=u.upload_document #>> '{manifest,format_metadata,recording_id}'
        LEFT JOIN ingest.raw_ingest_jobs j
          ON j.organization_id=u.organization_id AND j.project_id=u.project_id
         AND j.region_code=u.region_code AND j.raw_source_id=u.raw_source_id
        LEFT JOIN LATERAL (
          SELECT jsonb_agg(item) AS items FROM (
            SELECT jsonb_build_object('status', CASE WHEN dispatch.last_error_code IS NOT NULL
              THEN 'DISPATCH_BLOCKED' ELSE COALESCE(p.status,'PENDING_QC') END,
              'quality', CASE WHEN p.status='QC_FAILED' THEN 'REJECT'
                              WHEN p.qc_report_id IS NOT NULL
                              AND p.status IN ('PENDING_ALIGNMENT','ALIGNING','READY','FAILED')
                              THEN 'PASS' END,
              'error',COALESCE(p.failure_code,dispatch.last_error_code)) AS item
            FROM ingest.recording_episode_slices s
            LEFT JOIN ingest.recording_episode_processing p
              ON p.organization_id=s.organization_id AND p.project_id=s.project_id
             AND p.region_code=s.region_code AND p.recording_id=s.recording_id
             AND p.episode_id=s.episode_id AND p.finalized_revision=s.revision
            LEFT JOIN core.outbox_events dispatch
              ON dispatch.organization_id=p.organization_id AND dispatch.project_id=p.project_id
             AND dispatch.region_code=p.region_code AND dispatch.event_id=p.event_id
             AND dispatch.published_at IS NULL AND p.status='PENDING_QC'
            WHERE s.organization_id=u.organization_id AND s.project_id=u.project_id
              AND s.region_code=u.region_code AND s.recording_id=r.recording_id
              AND s.revision=r.finalized_revision
            UNION ALL
            SELECT jsonb_build_object('status',e.status,'quality',e.quality_status,
                'error',j.last_error_code)
            FROM ingest.raw_source_episodes e
            WHERE e.organization_id=u.organization_id AND e.project_id=u.project_id
              AND e.region_code=u.region_code AND e.raw_source_id=u.raw_source_id
              AND r.recording_id IS NULL
          ) combined
        ) episodes ON true
        WHERE u.organization_id=%s AND u.project_id=%s AND u.region_code=%s
          AND (%s::text IS NULL OR u.collection_task_id=%s)
        ORDER BY u.created_at DESC,u.upload_id
        """,
        (organization_id, project_id, region_code, task_id, task_id),
    )
    return tuple(capture_from_row(row) for row in cursor.fetchall())


def capture_from_row(row):
    (
        upload_id,
        task,
        robot,
        dataset,
        recording,
        state,
        committed,
        created,
        start,
        end,
        recording_status,
        job_status,
        error,
        episodes,
        mode,
        rollout,
        package,
        *source,
    ) = row
    received = state == "COMMITTED"
    all_discarded = bool(episodes) and all(ep["status"] == "DISCARDED" for ep in episodes)
    episodes = [ep for ep in episodes if ep["status"] != "DISCARDED"]
    ready = sum(
        ep["status"] == "READY" and ep.get("quality") not in {"RISK", "REJECT"} for ep in episodes
    )
    failed = sum(
        ep["status"] in {"FAILED", "QC_FAILED"} or ep.get("quality") in {"RISK", "REJECT"}
        for ep in episodes
    )
    blocked = sum(ep["status"] == "DISPATCH_BLOCKED" for ep in episodes)
    quality = (
        "REJECT"
        if any(ep.get("quality") == "REJECT" for ep in episodes)
        else "RISK"
        if any(ep.get("quality") == "RISK" for ep in episodes)
        else "PASS"
        if episodes and all(ep.get("quality") == "PASS" for ep in episodes)
        else None
    )
    stage = (
        "DISCARDED"
        if all_discarded
        else "CANCELLED"
        if state == "CANCELLED"
        else "FAILED"
        if state == "FAILED"
        else "UPLOADING"
        if not received
        else "AWAITING_SLICE"
        if recording_status == "READY_FOR_SLICING"
        else "READY"
        if episodes and ready == len(episodes)
        else "FAILED"
        if failed and ready + failed + blocked == len(episodes)
        else "BLOCKED"
        if blocked
        else "PROCESSING"
        if episodes or recording_status == "SLICED"
        else "FAILED"
        if job_status in {"FAILED", "PARTIALLY_FAILED"}
        else "PREPARING"
        if job_status in {"PENDING", "RUNNING"}
        else "RECEIVED"
    )
    duration = (
        max(0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
        if start and end
        else None
    )
    return CaptureProgress(
        upload_id=upload_id,
        capture_mode=mode,
        rollout_id=rollout,
        data_package_id=package,
        collection_task_id=task,
        robot_id=robot,
        dataset_id=dataset,
        recording_id=recording,
        source_import_id=source[0] if source else None,
        upload_state=state,
        stage=stage,
        received=received,
        duration_seconds=duration,
        episode_count=len(episodes),
        ready_episode_count=ready,
        failed_episode_count=failed,
        blocked_episode_count=blocked,
        qc_outcome=quality,
        error_code=next((ep.get("error") for ep in episodes if ep.get("error")), error),
        created_at=created,
        received_at=committed,
    )

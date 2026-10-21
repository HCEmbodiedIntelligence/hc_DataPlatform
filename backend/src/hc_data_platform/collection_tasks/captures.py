"""Read projection of robot/web uploads, receipt, recording QC and admission.

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
    canonical_episode_id: str | None = None
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
    unique = [item for item in items if not item.canonical_episode_id]
    return CaptureCounts(
        upload_count=len({item.upload_id for item in items}),
        received_count=len({item.upload_id for item in unique if item.received}),
        awaiting_slice_count=sum(item.stage == "AWAITING_SLICE" for item in unique),
        processing_count=sum(
            item.stage in {"RECEIVED", "PREPARING", "PROCESSING"} for item in unique
        ),
        failed_count=sum(item.stage == "FAILED" for item in unique),
        blocked_count=sum(item.stage == "BLOCKED" for item in unique),
        episode_count=sum(item.episode_count for item in unique),
        ready_episode_count=sum(item.ready_episode_count for item in unique),
        failed_episode_count=sum(item.failed_episode_count for item in unique),
    )


def read_captures(cursor, organization_id, project_id, region_code, task_id=None):
    cursor.execute(
        """
        WITH scope AS (
          SELECT %s::text AS organization_id, %s::text AS project_id,
                 %s::text AS region_code, %s::text AS task_id
        ), uploads AS (
          SELECT u.organization_id,u.project_id,u.region_code,u.upload_id,
                 u.collection_task_id,u.authenticated_robot_id,u.dataset_id,
                 u.state,u.committed_at,u.created_at,u.upload_document,
                 u.capture_mode,u.collection_job_id,u.raw_source_id
          FROM ingest.robot_ingest_uploads u
          JOIN scope USING (organization_id,project_id,region_code)
          WHERE scope.task_id IS NULL OR u.collection_task_id=scope.task_id
          UNION ALL
          -- A native web import has a committed Raw receipt but no robot upload
          -- or legacy rollout object. Count that receipt once across its Episodes.
          SELECT s.organization_id,s.project_id,s.region_code,s.upload_id,
                 s.collection_task_id,s.robot_id,s.dataset_id,
                 s.raw_status,s.committed_at,s.created_at,'{}'::jsonb,
                 'PRESEGMENTED',s.raw_source_id,s.raw_source_id
          FROM ingest.raw_sources s
          JOIN scope USING (organization_id,project_id,region_code)
          WHERE s.source_format='LEROBOT_V3' AND s.raw_status='COMMITTED'
            AND s.collection_task_id IS NOT NULL AND s.dataset_id IS NOT NULL
            AND s.robot_id IS NOT NULL
            AND (scope.task_id IS NULL OR s.collection_task_id=scope.task_id)
            AND NOT EXISTS (
              SELECT 1 FROM ingest.robot_ingest_uploads direct
              WHERE direct.organization_id=s.organization_id
                AND direct.project_id=s.project_id AND direct.region_code=s.region_code
                AND (direct.raw_source_id=s.raw_source_id OR direct.upload_id=s.upload_id)
            )
            AND NOT EXISTS (
              SELECT 1 FROM ingest.rollout_objects legacy
              WHERE legacy.organization_id=s.organization_id
                AND legacy.project_id=s.project_id AND legacy.region_code=s.region_code
                AND legacy.manifest_key=s.manifest_key
            )
        )
        SELECT u.upload_id,u.collection_task_id,u.authenticated_robot_id,u.dataset_id,
               r.recording_id,u.state,u.committed_at,u.created_at,
               COALESCE(r.capture_started_at::text,
                        u.upload_document #>> '{manifest,capture_started_at}'),
               COALESCE(r.capture_ended_at::text,
                        u.upload_document #>> '{manifest,capture_ended_at}'),
               r.status,j.status,j.last_error_code,
               COALESCE(episodes.items,'[]'::jsonb), u.capture_mode,
               COALESCE(r.rollout_id,u.upload_id), COALESCE(r.data_package_id,u.upload_id),
               u.raw_source_id
        FROM uploads u
        LEFT JOIN ingest.continuous_recordings r
          ON r.organization_id=u.organization_id AND r.project_id=u.project_id
         AND r.region_code=u.region_code
         AND ((r.collection_job_id=u.collection_job_id
           AND r.recording_id=u.upload_document #>> '{manifest,format_metadata,recording_id}')
           OR r.recording_id IN (
             SELECT latest.recording_id FROM (
               SELECT DISTINCT ON (m.source_path) m.recording_id
               FROM ingest.mcap_processing_files m
               WHERE m.organization_id=u.organization_id AND m.project_id=u.project_id
                 AND m.region_code=u.region_code AND m.raw_source_id=u.raw_source_id
               ORDER BY m.source_path,m.created_at DESC,m.processing_id
             ) latest
           ))
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
            SELECT jsonb_build_object('status',e.status,
                'quality',COALESCE(q.status,NULLIF(e.quality_status,'PENDING')),
                'error',j.last_error_code)
            FROM ingest.raw_source_episodes e
            LEFT JOIN quality_rollout_summaries q
              ON q.organization_id=e.organization_id AND q.project_id=e.project_id
             AND q.region_code=e.region_code
             AND q.rollout_id=COALESCE(e.data_ref #>> '{identity,rollout_id}',e.episode_id)
            WHERE e.organization_id=u.organization_id AND e.project_id=u.project_id
              AND e.region_code=u.region_code AND e.raw_source_id=u.raw_source_id
              AND r.recording_id IS NULL
          ) combined
        ) episodes ON true
        ORDER BY u.created_at DESC,u.upload_id
        """,
        (organization_id, project_id, region_code, task_id),
    )
    items = tuple(capture_from_row(row) for row in cursor.fetchall())
    cursor.execute(
        (
            """SELECT raw_source_id,canonical_episode_id FROM ingest.recording_source_links WHERE
            organization_id=%s AND project_id=%s AND region_code=%s"""
        ),
        (organization_id, project_id, region_code),
    )
    links = dict(cursor.fetchall())
    return tuple(
        item.model_copy(
            update={
                "canonical_episode_id": links[item.source_import_id],
                "stage": "RECEIVED",
                "error_code": "RECORDING_ALREADY_PROCESSED",
                "episode_count": 0,
                "ready_episode_count": 0,
                "failed_episode_count": 0,
            }
        )
        if item.source_import_id in links
        else item
        for item in items
    )


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
        else "BLOCKED"
        if job_status == "FAILED"
        and error
        in {
            "MCAP_MAPPING_REQUIRED",
            "MCAP_MAPPING_INVALID",
            "MCAP_DEPTH_UNITS_REQUIRED",
            "ROBOT_PROCESSING_CONFIG_REQUIRED",
            "ROBOT_PROCESSING_CONFIG_CONFLICT",
            "ROBOT_PROCESSING_CONFIG_VERSION_UNKNOWN",
            "ROBOT_TASK_DATA_INCOMPATIBLE",
        }
        else "FAILED"
        if job_status in {"FAILED", "PARTIALLY_FAILED"}
        else "PREPARING"
        if job_status == "RUNNING"
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

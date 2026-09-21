"""Scoped, durable browser progress and explicit retries of committed native imports."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict

from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.ingest.processing_status import processing_interruption
from hc_data_platform.workflow.models import workflow_id

from .configuration import connections
from .models import CreateLeRobotImportV1


class StartStoredProcessing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection_task_id: str
    robot_id: str


def start_stored_processing(
    organization_id: str,
    project_id: str,
    region_code: str,
    import_id: str,
    manifest: CreateLeRobotImportV1,
) -> NativeImportProgress:
    """Atomically attach processing metadata, retaining every immutable Raw object."""
    from .configuration import validate_target

    validate_target(organization_id, project_id, region_code, manifest)
    scope = (organization_id, project_id, region_code, import_id)
    locator = workflow_id("lerobot-import", project_id, f"{region_code}/{import_id}")
    with connections()() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT processing_status, collection_task_id, robot_id FROM ingest.raw_sources
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
            AND raw_status='COMMITTED' AND source_format='LEROBOT_V3' FOR UPDATE""",
            scope,
        )
        row = cursor.fetchone()
        if row is None:
            raise problem(
                status=404,
                code="RAW_SOURCE_NOT_FOUND",
                title="原始数据不存在",
                detail="没有可处理的 LeRobot 原始数据。",
            )
        if row[0] != "NOT_REQUESTED":
            if (row[1], row[2]) != (manifest.collection_task_id, manifest.robot_id):
                raise problem(
                    status=409,
                    code="RAW_PROCESSING_ALREADY_BOUND",
                    title="处理任务已创建",
                    detail="不能改变已经开始处理的数据归属。",
                )
        else:
            cursor.execute(
                """UPDATE ingest.raw_sources SET collection_task_id=%s, robot_id=%s,
                processing_status='PENDING', updated_at=clock_timestamp()
                WHERE organization_id=%s AND project_id=%s
                AND region_code=%s AND raw_source_id=%s""",
                (manifest.collection_task_id, manifest.robot_id, *scope),
            )
            cursor.executemany(
                """INSERT INTO ingest.raw_source_episodes
                (organization_id, project_id, region_code, raw_source_id, episode_id,
                 source_episode_index, status, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,'PENDING',clock_timestamp(),clock_timestamp())
                ON CONFLICT DO NOTHING""",
                [
                    (*scope, f"lerobot-{import_id[:16]}-ep-{i:06d}", i)
                    for i in range(manifest.episode_count)
                ],
            )
            cursor.execute(
                """UPDATE ingest.raw_ingest_jobs SET job_type='LEROBOT_IMPORT',
                adapter_name='lerobot_v3', workflow_id=%s, status='PENDING', last_error_code=NULL,
                updated_at=clock_timestamp()
                WHERE organization_id=%s AND project_id=%s
                AND region_code=%s AND raw_source_id=%s""",
                (locator, *scope),
            )
            event = DomainEventEnvelope(
                event_id=str(uuid5(NAMESPACE_URL, f"lerobot-dispatch:{organization_id}:{locator}")),
                event_type="lerobot.import.requested.v1",
                aggregate_type="raw_source",
                aggregate_id=import_id,
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                payload={"raw_source_id": import_id, "workflow_id": locator},
            )
            cursor.execute(
                """INSERT INTO core.outbox_events (event_id,organization_id,project_id,region_code,
                event_type,envelope,occurred_at,available_at)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp()) ON CONFLICT DO NOTHING""",
                (
                    event.event_id,
                    organization_id,
                    project_id,
                    region_code,
                    event.event_type,
                    event.model_dump_json(),
                    event.occurred_at,
                ),
            )
    return get_progress(*scope)


class NativeImportProgress(BaseModel):
    import_id: str
    dataset_id: str
    collection_task_id: str | None = None
    status: str
    episode_count: int
    ready: int
    failed: int
    last_error_code: str | None
    updated_at: datetime
    source_format: str = "LEROBOT_V3"
    file_count: int = 0
    total_bytes: int = 0
    discarded: int = 0
    resume_required: int = 0
    reprocessing_conflicts: int = 0


def _interruption_counts(
    cursor: Any, scope: tuple[str, str, str], import_ids: list[str]
) -> dict[str, dict[str, int]]:
    if not import_ids:
        return {}
    cursor.execute(
        """SELECT e.raw_source_id, e.status, batch.status, qc.status,
                  workflow.status, workflow.error_code, alignment.status,
                  resolution.status, rollout.duplicate_of_rollout_id,
                  EXISTS (SELECT 1 FROM lance_rollout_lineage lineage
                          WHERE lineage.organization_id=e.organization_id
                            AND lineage.project_id=e.project_id
                            AND lineage.rollout_id=e.episode_id)
           FROM ingest.raw_source_episodes e
           JOIN ingest.raw_ingest_jobs batch USING
               (organization_id, project_id, region_code, raw_source_id)
           LEFT JOIN quality_rollout_summaries qc
             ON qc.organization_id=e.organization_id AND qc.project_id=e.project_id
            AND qc.region_code=e.region_code AND qc.rollout_id=e.episode_id
           LEFT JOIN ingest.rollouts rollout
             ON rollout.organization_id=e.organization_id AND rollout.project_id=e.project_id
            AND rollout.region_code=e.region_code AND rollout.rollout_id=e.episode_id
           LEFT JOIN LATERAL (
               SELECT status, error_code FROM workflow.jobs w
               WHERE w.organization_id=e.organization_id AND w.project_id=e.project_id
                 AND w.resource_id=e.episode_id AND w.job_type='IngestRolloutWorkflow'
               ORDER BY updated_at DESC, job_id DESC LIMIT 1
           ) workflow ON true
           LEFT JOIN LATERAL (
               SELECT status FROM aligned_fragment_attempts a
               WHERE a.organization_id=e.organization_id AND a.project_id=e.project_id
                 AND a.region_code=e.region_code AND a.rollout_id=e.episode_id
               ORDER BY updated_at DESC, attempt_id DESC LIMIT 1
           ) alignment ON true
           LEFT JOIN LATERAL (
               SELECT status FROM ingest.episode_resolutions r
               WHERE r.organization_id=e.organization_id AND r.project_id=e.project_id
                 AND r.region_code=e.region_code AND r.episode_id=e.episode_id
               ORDER BY created_at DESC LIMIT 1
           ) resolution ON true
           WHERE e.organization_id=%s AND e.project_id=%s AND e.region_code=%s
             AND e.raw_source_id=ANY(%s) AND e.status='FAILED'""",
        (*scope, import_ids),
    )
    counts: dict[str, dict[str, int]] = {}
    for row in cursor.fetchall():
        interruption = processing_interruption(
            source_episode_status=row[1],
            source_processing_status=row[2],
            qc_status=row[3],
            workflow_status=row[4],
            workflow_error_code=row[5],
            alignment_status=row[6],
            resolution_status=row[7],
            duplicate_of_rollout_id=row[8],
            lance_ready=row[9],
        )
        if interruption is not None:
            current = counts.setdefault(row[0], {})
            current[interruption] = current.get(interruption, 0) + 1
    return counts


def list_progress(
    organization_id: str,
    project_id: str,
    region_code: str,
    import_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    dataset_id: str | None = None,
    include_all_sources: bool = False,
) -> list[NativeImportProgress]:
    with connections()() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT s.raw_source_id, s.dataset_id,
            CASE WHEN j.job_type='RAW_STORAGE' THEN 'RAW_COMMITTED' ELSE j.status END,
            count(e.episode_id), count(*) FILTER (WHERE e.status='READY'),
            count(*) FILTER (WHERE e.status='FAILED'), j.last_error_code, j.updated_at,
            s.source_format, s.file_count, s.total_bytes,
            count(*) FILTER (WHERE e.status='DISCARDED'), s.collection_task_id
            FROM ingest.raw_sources s JOIN ingest.raw_ingest_jobs j USING
              (organization_id, project_id, region_code, raw_source_id)
            LEFT JOIN ingest.raw_source_episodes e USING
              (organization_id, project_id, region_code, raw_source_id)
            WHERE s.organization_id=%s AND s.project_id=%s AND s.region_code=%s
              AND (%s OR j.job_type='RAW_STORAGE' OR s.source_format='LEROBOT_V3')
              AND s.raw_status='COMMITTED'
              AND (%s::text IS NULL OR s.raw_source_id=%s)
              AND (%s::text IS NULL OR s.dataset_id=%s)
            GROUP BY s.raw_source_id, s.dataset_id, j.job_type, j.status,
            j.last_error_code, j.updated_at, s.source_format, s.file_count, s.total_bytes,
            s.collection_task_id
            ORDER BY j.updated_at DESC, s.raw_source_id LIMIT %s OFFSET %s""",
            (
                organization_id,
                project_id,
                region_code,
                include_all_sources,
                import_id,
                import_id,
                dataset_id,
                dataset_id,
                limit,
                offset,
            ),
        )
        rows = cursor.fetchall()
        interruptions = _interruption_counts(
            cursor,
            (organization_id, project_id, region_code),
            [row[0] for row in rows if row[5] > 0],
        )
        return [
            NativeImportProgress(
                import_id=r[0],
                dataset_id=r[1],
                status=r[2],
                episode_count=r[3],
                ready=r[4],
                failed=r[5],
                last_error_code=r[6],
                updated_at=r[7],
                source_format=r[8],
                file_count=r[9],
                total_bytes=r[10],
                discarded=r[11],
                collection_task_id=r[12],
                resume_required=interruptions.get(r[0], {}).get("RESUME_REQUIRED", 0),
                reprocessing_conflicts=interruptions.get(r[0], {}).get("PROCESSING_CONFLICT", 0),
            )
            for r in rows
        ]


def get_progress(
    organization_id: str, project_id: str, region_code: str, import_id: str
) -> NativeImportProgress:
    rows = list_progress(organization_id, project_id, region_code, import_id)
    if not rows:
        raise problem(
            status=404,
            code="LEROBOT_IMPORT_NOT_FOUND",
            title="导入记录不存在",
            detail="当前项目和区域中没有这条 LeRobot 导入记录。",
        )
    return rows[0]


def retry_processing(
    organization_id: str, project_id: str, region_code: str, import_id: str
) -> NativeImportProgress:
    scope = (organization_id, project_id, region_code, import_id)
    with connections()() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT status, attempts FROM ingest.raw_ingest_jobs
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
              AND job_type='LEROBOT_IMPORT' FOR UPDATE""",
            scope,
        )
        row = cursor.fetchone()
        if row is None:
            raise problem(
                status=404,
                code="LEROBOT_IMPORT_NOT_FOUND",
                title="导入记录不存在",
                detail="当前项目和区域中没有这条 LeRobot 导入记录。",
            )
        if row[0] not in {"FAILED", "PARTIALLY_FAILED", "CANCELLED"}:
            raise problem(
                status=409,
                code="LEROBOT_RETRY_UNAVAILABLE",
                title="当前不能重试",
                detail="仅失败或取消的处理任务可以重试。",
            )
        attempt = int(row[1]) + 1
        locator = workflow_id(
            "lerobot-import", project_id, f"{region_code}/{import_id}/retry-{attempt}"
        )
        cursor.execute(
            """UPDATE ingest.raw_ingest_jobs SET status='PENDING', attempts=%s,
            workflow_id=%s, last_error_code=NULL, updated_at=clock_timestamp()
            WHERE organization_id=%s AND project_id=%s
                AND region_code=%s AND raw_source_id=%s""",
            (attempt, locator, *scope),
        )
        cursor.execute(
            """UPDATE ingest.raw_sources SET processing_status='PENDING',
            updated_at=clock_timestamp() WHERE organization_id=%s AND project_id=%s
            AND region_code=%s AND raw_source_id=%s""",
            scope,
        )
        cursor.execute(
            """UPDATE ingest.raw_source_episodes SET status='PENDING',
            updated_at=clock_timestamp()
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
              AND raw_source_id=%s AND status='FAILED'""",
            scope,
        )
        event = DomainEventEnvelope(
            event_id=str(uuid5(NAMESPACE_URL, f"lerobot-dispatch:{organization_id}:{locator}")),
            event_type="lerobot.import.requested.v1",
            aggregate_type="raw_source",
            aggregate_id=import_id,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            payload={"raw_source_id": import_id, "workflow_id": locator},
        )
        cursor.execute(
            """INSERT INTO core.outbox_events (event_id, organization_id, project_id,
            region_code, event_type, envelope, occurred_at, available_at)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())
            ON CONFLICT (event_id) DO NOTHING""",
            (
                event.event_id,
                organization_id,
                project_id,
                region_code,
                event.event_type,
                event.model_dump_json(),
                event.occurred_at,
            ),
        )
    return get_progress(*scope)

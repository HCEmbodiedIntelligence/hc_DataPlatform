"""Scoped, durable browser progress and explicit retries of committed native imports."""

from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict

from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
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
    status: str
    episode_count: int
    ready: int
    failed: int
    last_error_code: str | None
    updated_at: datetime
    source_format: str = "LEROBOT_V3"
    file_count: int = 0
    total_bytes: int = 0


def list_progress(
    organization_id: str,
    project_id: str,
    region_code: str,
    import_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    dataset_id: str | None = None,
) -> list[NativeImportProgress]:
    with connections()() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT s.raw_source_id, s.dataset_id,
            CASE WHEN j.job_type='RAW_STORAGE' THEN 'RAW_COMMITTED' ELSE j.status END,
            count(e.episode_id), count(*) FILTER (WHERE e.status='READY'),
            count(*) FILTER (WHERE e.status='FAILED'), j.last_error_code, j.updated_at,
            s.source_format, s.file_count, s.total_bytes
            FROM ingest.raw_sources s JOIN ingest.raw_ingest_jobs j USING
              (organization_id, project_id, region_code, raw_source_id)
            LEFT JOIN ingest.raw_source_episodes e USING
              (organization_id, project_id, region_code, raw_source_id)
            WHERE s.organization_id=%s AND s.project_id=%s AND s.region_code=%s
              AND (j.job_type='RAW_STORAGE' OR s.source_format='LEROBOT_V3')
              AND (%s::text IS NULL OR s.raw_source_id=%s)
              AND (%s::text IS NULL OR s.dataset_id=%s)
            GROUP BY s.raw_source_id, s.dataset_id, j.job_type, j.status,
            j.last_error_code, j.updated_at, s.source_format, s.file_count, s.total_bytes
            ORDER BY j.updated_at DESC, s.raw_source_id LIMIT %s OFFSET %s""",
            (
                organization_id,
                project_id,
                region_code,
                import_id,
                import_id,
                dataset_id,
                dataset_id,
                limit,
                offset,
            ),
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
            )
            for r in cursor.fetchall()
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

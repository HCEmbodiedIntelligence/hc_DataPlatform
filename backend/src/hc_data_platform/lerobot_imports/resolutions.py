"""Scoped, auditable decisions for an uncommitted native processing conflict."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.workflow.models import workflow_id

from .configuration import connections


class ResolveEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    action: Literal["REPROCESS", "DISCARD"]
    expected_attempt_id: str = Field(min_length=1, max_length=128)


class EpisodeResolution(BaseModel):
    resolution_id: str | None = None
    action: Literal["REPROCESS", "DISCARD"] | None = None
    status: Literal["UNRESOLVED", "PENDING", "RUNNING", "SUCCEEDED", "FAILED", "DISCARDED"]
    available: bool
    unavailable_reason: str | None = None
    error_code: str | None = None
    updated_at: datetime | None = None


def _facts(
    cursor: Any, scope: tuple[str, ...], index: int, *, lock: bool = False
) -> dict[str, Any]:
    # The aggregate job lock serializes decisions against batch retries.
    cursor.execute(
        """SELECT j.status, e.status, e.episode_id
        FROM ingest.raw_ingest_jobs j
        JOIN ingest.raw_sources s USING (organization_id, project_id, region_code, raw_source_id)
        JOIN ingest.raw_source_episodes e USING
            (organization_id, project_id, region_code, raw_source_id)
        WHERE j.organization_id=%s AND j.project_id=%s AND j.region_code=%s
          AND j.raw_source_id=%s AND e.source_episode_index=%s
          AND s.source_format='LEROBOT_V3' AND s.raw_status='COMMITTED'
        """
        + (" FOR UPDATE OF j, e" if lock else ""),
        (*scope, index),
    )
    row = cursor.fetchone()
    if row is None:
        raise problem(
            status=404,
            code="EPISODE_NOT_FOUND",
            title="数据不存在",
            detail="当前项目和区域中没有这条原始数据。",
        )
    batch, episode, episode_id = row
    cursor.execute(
        """SELECT resolution_id, action, status, error_code, updated_at
        FROM ingest.episode_resolutions
        WHERE organization_id=%s AND project_id=%s AND region_code=%s
          AND raw_source_id=%s AND episode_id=%s
        ORDER BY created_at DESC LIMIT 1""",
        (*scope, episode_id),
    )
    resolution = cursor.fetchone()
    cursor.execute(
        """SELECT status, error_code FROM workflow.jobs
        WHERE organization_id=%s AND project_id=%s
          AND resource_id=%s AND job_type='IngestRolloutWorkflow'
        ORDER BY updated_at DESC, job_id DESC LIMIT 1""",
        (*scope[:2], episode_id),
    )
    workflow = cursor.fetchone()
    cursor.execute(
        """SELECT attempt_id FROM aligned_fragment_attempts
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND rollout_id=%s
        ORDER BY updated_at DESC, attempt_id DESC LIMIT 1""",
        (*scope[:3], episode_id),
    )
    attempt = cursor.fetchone()
    cursor.execute(
        """SELECT 1 FROM lance_rollout_lineage
        WHERE organization_id=%s AND project_id=%s AND rollout_id=%s LIMIT 1""",
        (*scope[:2], episode_id),
    )
    committed = cursor.fetchone() is not None
    retry_failed = resolution is not None and resolution[2] == "FAILED"
    conflict = workflow is not None and workflow == (
        "TECHNICAL_FAILED",
        "ALIGNMENT_ATTEMPT_IMMUTABLE",
    )
    reason = (
        "该数据已入库，不能在重复数据处理中移除或再次入库。"
        if committed or episode == "READY"
        else "这条记录已移除。"
        if episode == "DISCARDED"
        else "本批数据正在处理，请等待处理结束后再操作。"
        if batch in {"PENDING", "RUNNING"}
        else "当前记录不再是待处理的结果冲突，请刷新查看最新状态。"
        if episode != "FAILED" or not (conflict or retry_failed) or not attempt
        else None
    )
    return {
        "episode_id": episode_id,
        "attempt": attempt[0] if attempt else None,
        "resolution": resolution,
        "reason": reason,
    }


def _view(facts: dict[str, Any]) -> EpisodeResolution:
    row = facts["resolution"]
    return EpisodeResolution(
        resolution_id=row[0] if row else None,
        action=row[1] if row else None,
        status=row[2] if row else "UNRESOLVED",
        error_code=row[3] if row else None,
        updated_at=row[4] if row else None,
        available=facts["reason"] is None,
        unavailable_reason=facts["reason"],
    )


def get_resolution(
    organization_id: str, project_id: str, region_code: str, import_id: str, episode_index: int
) -> EpisodeResolution:
    with connections()() as connection, connection.cursor() as cursor:
        return _view(
            _facts(cursor, (organization_id, project_id, region_code, import_id), episode_index)
        )


def refresh_import_state(cursor: Any, scope: tuple[str, ...]) -> None:
    """Reconcile the aggregate from all receipts, including explicitly discarded ones."""
    cursor.execute(
        """SELECT count(*) FILTER (WHERE status IN ('READY', 'DISCARDED')),
        count(*) FILTER (WHERE status NOT IN ('READY', 'DISCARDED'))
        FROM ingest.raw_source_episodes
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
        scope,
    )
    done, remaining = cursor.fetchone()
    status = "SUCCEEDED" if remaining == 0 else "PARTIALLY_FAILED" if done else "FAILED"
    cursor.execute(
        """UPDATE ingest.raw_ingest_jobs SET status=%s,
        last_error_code=CASE WHEN %s='SUCCEEDED' THEN NULL ELSE last_error_code END,
        updated_at=clock_timestamp()
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
        (status, status, *scope),
    )
    cursor.execute(
        """UPDATE ingest.raw_sources SET processing_status=%s, updated_at=clock_timestamp()
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
        ("READY" if status == "SUCCEEDED" else status, *scope),
    )


def resolve_episode(
    organization_id: str,
    project_id: str,
    region_code: str,
    import_id: str,
    episode_index: int,
    command: ResolveEpisode,
    actor_id: str,
) -> EpisodeResolution:
    scope = (organization_id, project_id, region_code, import_id)
    resolution_id = str(command.request_id)
    with connections()() as connection, connection.cursor() as cursor:
        facts = _facts(cursor, scope, episode_index, lock=True)
        cursor.execute(
            """SELECT organization_id, project_id, region_code, raw_source_id,
            episode_id, action FROM ingest.episode_resolutions WHERE resolution_id=%s""",
            (resolution_id,),
        )
        replay = cursor.fetchone()
        if replay is not None and replay != (*scope, facts["episode_id"], command.action):
            raise problem(
                status=409,
                code="EPISODE_RESOLUTION_REQUEST_REUSED",
                title="请求编号已使用",
                detail="请刷新页面后重新选择操作。",
            )
        existing = facts["resolution"]
        if replay is not None and existing and existing[0] != resolution_id:
            # A delayed retry of an older request reports the current decision;
            # it must never create another processing run.
            return _view(facts)
        if existing and existing[0] == resolution_id and existing[1] == command.action:
            return _view(facts)
        if facts["reason"]:
            raise problem(
                status=409,
                code="EPISODE_RESOLUTION_UNAVAILABLE",
                title="当前不可处理",
                detail=facts["reason"],
            )
        if facts["attempt"] != command.expected_attempt_id:
            raise problem(
                status=409,
                code="EPISODE_RESOLUTION_STALE",
                title="处理记录已变化",
                detail="请刷新详情，核对最新处理结果后重试。",
            )
        status = "DISCARDED" if command.action == "DISCARD" else "PENDING"
        locator = workflow_id(
            "lerobot-import", project_id, f"{region_code}/{import_id}/resolution-{resolution_id}"
        )
        cursor.execute(
            """INSERT INTO ingest.episode_resolutions
            (resolution_id, organization_id, project_id, region_code, raw_source_id,
             episode_id, action, status, previous_attempt_id, workflow_id, actor_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                resolution_id,
                *scope,
                facts["episode_id"],
                command.action,
                status,
                facts["attempt"],
                locator if command.action == "REPROCESS" else None,
                actor_id,
            ),
        )
        cursor.execute(
            """UPDATE ingest.raw_source_episodes SET status=%s, updated_at=clock_timestamp()
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
              AND raw_source_id=%s AND episode_id=%s""",
            (status, *scope, facts["episode_id"]),
        )
        if command.action == "DISCARD":
            refresh_import_state(cursor, scope)
        else:
            cursor.execute(
                """UPDATE ingest.raw_ingest_jobs SET status='PENDING', workflow_id=%s,
                attempts=attempts+1, last_error_code=NULL, updated_at=clock_timestamp()
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND raw_source_id=%s""",
                (locator, *scope),
            )
            cursor.execute(
                """UPDATE ingest.raw_sources SET processing_status='PENDING',
                updated_at=clock_timestamp()
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND raw_source_id=%s""",
                scope,
            )
            event = DomainEventEnvelope(
                event_type="lerobot.import.requested.v1",
                aggregate_type="raw_source",
                aggregate_id=import_id,
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                payload={
                    "raw_source_id": import_id,
                    "workflow_id": locator,
                    "episode_index": episode_index,
                    "resolution_id": resolution_id,
                },
            )
            cursor.execute(
                """INSERT INTO core.outbox_events (event_id, organization_id, project_id,
                region_code, event_type, envelope, occurred_at, available_at)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())""",
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
        return _view(_facts(cursor, scope, episode_index))

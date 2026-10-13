"""Requeue an uncommitted Episode through the existing QC/ingest outbox."""

import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.workflow.models import continuous_episode_workflow_id

from .asset_models import EpisodeProcessing, EpisodeProcessingStatus, RetryEpisodeCommand


def retry_event_id(scope, recording_id: str, episode_id: str, request_id) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            ":".join(
                (
                    "continuous-episode-retry/v1",
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    recording_id,
                    episode_id,
                    str(request_id),
                )
            ),
        )
    )


def prepare_retry(previous: EpisodeProcessing, command: RetryEpisodeCommand, *, discard=False):
    if previous.workflow_id != command.expected_workflow_id:
        raise problem(
            status=409,
            code="EPISODE_RETRY_STALE",
            title="处理记录已变化",
            detail="请刷新最新处理结果后重试。",
        )
    dispatch_blocked = (
        previous.status == EpisodeProcessingStatus.PENDING_QC
        and previous.dispatch_error_code is not None
    )
    if not dispatch_blocked and previous.status not in {
        EpisodeProcessingStatus.QC_FAILED,
        EpisodeProcessingStatus.FAILED,
    }:
        raise problem(
            status=409,
            code="EPISODE_RETRY_UNAVAILABLE",
            title="当前不可重新处理",
            detail="只可处理已失败或等待配置且未入库的 Episode；正在启动或处理中的数据请稍后刷新。",
        )
    if previous.data_ref or previous.dataset_version or previous.lance_version:
        raise committed_conflict()
    scope = previous.scope
    now = datetime.now(timezone.utc)
    locator = (
        continuous_episode_workflow_id(
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            previous.recording_id,
            previous.episode_id,
        )
        + f":retry:{command.request_id}"
    )
    event = DomainEventEnvelope(
        event_id=retry_event_id(
            scope, previous.recording_id, previous.episode_id, command.request_id
        ),
        event_type="continuous-recording.episode.workflow.requested.v1",
        aggregate_type="continuous_recording_episode",
        aggregate_id=f"{previous.recording_id}/{previous.episode_id}",
        organization_id=scope.organization_id,
        project_id=scope.project_id,
        region_code=scope.region_code,
        occurred_at=now,
        payload={
            "recording_id": previous.recording_id,
            "episode_id": previous.episode_id,
            "finalized_revision": previous.finalized_revision,
            "workflow_id": locator,
            "retry_request_id": str(command.request_id),
            "expected_workflow_id": command.expected_workflow_id,
        },
    )
    # Retain the prior QC receipt until the new evaluation replaces it, so the
    # issue remains visible while it is being processed.
    episode = EpisodeProcessing.model_validate(
        {
            **previous.model_dump(),
            "status": EpisodeProcessingStatus.DISCARDED
            if discard
            else EpisodeProcessingStatus.PENDING_QC,
            "workflow_id": previous.workflow_id if discard else locator,
            "event_id": previous.event_id if discard else event.event_id,
            "failure_code": None,
            "failure_stage": None,
            "dispatch_error_code": None,
            "alignment_attempt_id": None,
            "updated_at": now,
        }
    )
    return episode, event


def committed_conflict():
    return problem(
        status=409,
        code="EPISODE_ALREADY_COMMITTED",
        title="数据已经入库",
        detail="已入库的数据不能从原始数据处理入口再次入库，请使用清洗流程。",
    )


def retry_postgres(
    factory, scope, recording_id, episode_id, command, *, actor_id, request_id, discard=False
):
    scoped = (scope.organization_id, scope.project_id, scope.region_code)
    key = (*scoped, recording_id, episode_id)
    event_id = retry_event_id(scope, recording_id, episode_id, command.request_id)
    connection = factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT episode_document FROM ingest.recording_episode_processing
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND recording_id=%s AND episode_id=%s FOR UPDATE""",
                key,
            )
            row = cursor.fetchone()
            if row is None:
                raise problem(
                    status=404,
                    code="CONTINUOUS_EPISODE_NOT_FOUND",
                    title="Episode 不存在",
                    detail="当前范围中没有这条 Episode。",
                )
            previous = EpisodeProcessing.model_validate(row[0])
            cursor.execute(
                "SELECT action,details FROM core.audit_events WHERE audit_id=%s", (event_id,)
            )
            replay = cursor.fetchone()
            if replay:
                if (
                    replay[0]
                    != (
                        "continuous_recording.episode.discarded"
                        if discard
                        else "continuous_recording.episode.retried"
                    )
                    or replay[1].get("expected_workflow_id") != command.expected_workflow_id
                ):
                    raise problem(
                        status=409,
                        code="EPISODE_RETRY_REQUEST_REUSED",
                        title="请求编号已使用",
                        detail="请刷新处理结果后重试。",
                    )
                return previous
            rollout = f"{recording_id}:{episode_id}"
            cursor.execute(
                """SELECT 1 FROM episode_store.versions v JOIN episode_store.bindings b USING
                  (organization_id,project_id,region_code,dataset_id,episode_id)
                WHERE v.organization_id=%s AND v.project_id=%s AND v.region_code=%s
                  AND b.rollout_id=%s AND v.status='COMMITTED'
                UNION ALL SELECT 1 FROM lance_rollout_lineage
                WHERE organization_id=%s AND project_id=%s AND rollout_id=%s LIMIT 1""",
                (*scoped, rollout, scope.organization_id, scope.project_id, rollout),
            )
            if cursor.fetchone() is not None:
                raise committed_conflict()
            if previous.status == EpisodeProcessingStatus.PENDING_QC:
                # Lock the old dispatch before checking its lease: a UI snapshot
                # must never cancel or duplicate an activity that is being started.
                cursor.execute(
                    """SELECT last_error_code FROM core.outbox_events
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND event_id=%s AND published_at IS NULL AND claim_token IS NULL
                    FOR UPDATE""",
                    (*scoped, previous.event_id),
                )
                delivery = cursor.fetchone()
                previous = previous.model_copy(
                    update={"dispatch_error_code": delivery[0] if delivery else None}
                )
            episode, event = prepare_retry(previous, command, discard=discard)
            if previous.status == EpisodeProcessingStatus.PENDING_QC and not discard:
                # Retire the blocked event in the same transaction as its replacement.
                cursor.execute(
                    """UPDATE core.outbox_events SET published_at=%s
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND event_id=%s""",
                    (event.occurred_at, *scoped, previous.event_id),
                )
            cursor.execute(
                """UPDATE ingest.recording_episode_processing SET status=%s,
                workflow_id=%s, event_id=%s, alignment_attempt_id=NULL,
                failure_code=NULL, failure_stage=NULL, episode_document=%s::jsonb, updated_at=%s
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND recording_id=%s AND episode_id=%s""",
                (
                    episode.status.value,
                    episode.workflow_id,
                    episode.event_id,
                    episode.model_dump_json(),
                    episode.updated_at,
                    *key,
                ),
            )
            if not discard:
                cursor.execute(
                    """INSERT INTO core.outbox_events (event_id,organization_id,project_id,
                region_code,event_type,envelope,occurred_at,available_at)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                    (
                        event.event_id,
                        *scoped,
                        event.event_type,
                        event.model_dump_json(),
                        event.occurred_at,
                        event.occurred_at,
                    ),
                )
            cursor.execute(
                """INSERT INTO core.audit_events
                (audit_id,organization_id,project_id,region_code,actor_id,action,
                 resource_type,resource_id,request_id,before_hash,after_hash,details,occurred_at)
                VALUES (%s,%s,%s,%s,%s,%s,
                        'continuous_recording_episode',%s,%s,%s,%s,%s::jsonb,%s)""",
                (
                    event.event_id,
                    *scoped,
                    actor_id,
                    "continuous_recording.episode.discarded"
                    if discard
                    else "continuous_recording.episode.retried",
                    rollout,
                    request_id,
                    canonical_hash(previous.model_dump(mode="json")),
                    canonical_hash(episode.model_dump(mode="json")),
                    json.dumps(event.payload),
                    event.occurred_at,
                ),
            )
        connection.commit()
        return episode
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

"""Continuous uploads in the existing dashboard's package lifecycle projection."""

from dataclasses import dataclass, replace

from hc_data_platform.collection_tasks.captures import CaptureProgress

from .models import TaskIssueFinding
from .repository import TaskStatusPackageFact


@dataclass(frozen=True, slots=True)
class RecordingEpisodeWorkflow:
    annotation_task_id: str | None = None
    annotation_status: str | None = None
    review_decision: str | None = None
    published: bool = False
    processing_status: str = "READY"
    qc_status: str | None = "PASS"
    error_code: str | None = None


def read_recording_workflows(cursor, organization_id, project_id, region_code):
    """Use each finalized slice's bound task, not the parent recording's rollout."""
    cursor.execute(
        """
        SELECT r.recording_id, a.task_id, a.status, review.decision,
               EXISTS (
                 SELECT 1 FROM publishing.rollout_publication_lineage publication
                 WHERE publication.organization_id=a.organization_id
                   AND publication.project_id=a.project_id
                   AND publication.region_code=a.region_code
                   AND publication.rollout_id=a.rollout_id
               ) AS published,
               COALESCE(p.status, 'PENDING_QC'),
               CASE WHEN p.status='QC_FAILED' THEN 'REJECT'
                    WHEN p.qc_report_id IS NOT NULL
                      AND p.status IN ('PENDING_ALIGNMENT','ALIGNING','READY','FAILED')
                    THEN 'PASS' END,
               COALESCE(p.failure_code, dispatch.last_error_code)
        FROM ingest.continuous_recordings r
        JOIN ingest.recording_episode_slices s
          ON s.organization_id=r.organization_id AND s.project_id=r.project_id
         AND s.region_code=r.region_code AND s.recording_id=r.recording_id
         AND s.revision=r.finalized_revision
        LEFT JOIN ingest.recording_episode_processing p
          ON p.organization_id=s.organization_id AND p.project_id=s.project_id
         AND p.region_code=s.region_code AND p.recording_id=s.recording_id
         AND p.episode_id=s.episode_id AND p.finalized_revision=s.revision
        LEFT JOIN core.outbox_events dispatch
          ON dispatch.organization_id=p.organization_id AND dispatch.project_id=p.project_id
         AND dispatch.region_code=p.region_code AND dispatch.event_id=p.event_id
         AND dispatch.published_at IS NULL AND p.status='PENDING_QC'
        LEFT JOIN annotation.annotation_tasks a
          ON a.organization_id=p.organization_id AND a.project_id=p.project_id
         AND a.region_code=p.region_code AND a.dataset_id=p.dataset_id
         AND a.task_id=p.annotation_task_id AND p.status='READY'
        LEFT JOIN LATERAL (
          SELECT decision.decision FROM annotation.annotation_reviews decision
          WHERE decision.task_id=a.task_id
          ORDER BY decision.created_at DESC, decision.review_id DESC LIMIT 1
        ) review ON true
        WHERE r.organization_id=%s AND r.project_id=%s AND r.region_code=%s
          AND p.status IS DISTINCT FROM 'DISCARDED'
        ORDER BY r.recording_id,s.ordinal,s.episode_id
        """,
        (organization_id, project_id, region_code),
    )
    result = {}
    for recording, task_id, status, review, published, processing, qc, error in cursor.fetchall():
        result.setdefault(recording, []).append(
            RecordingEpisodeWorkflow(task_id, status, review, published, processing, qc, error)
        )
    return {recording: tuple(episodes) for recording, episodes in result.items()}


def _annotation_priority(episode: RecordingEpisodeWorkflow) -> int:
    # A package only finishes a stage once every active slice has finished it.
    # Select the earliest unfinished slice for the existing package action link.
    if episode.annotation_task_id is None:
        return 0
    return {
        "NEEDS_REVISION": 1,
        "REJECTED": 1,
        "DRAFT": 2,
        "SUBMITTED": 3,
        "APPROVED": 4,
    }.get(episode.annotation_status, 0)


def read_recording_findings(cursor, organization_id, project_id, region_code):
    cursor.execute(
        """
        SELECT p.recording_id, q.report_document
        FROM ingest.recording_episode_processing p
        JOIN ingest.recording_episode_qc_reports q
          ON (q.organization_id,q.project_id,q.region_code,q.recording_id,q.episode_id,q.report_id)
           = (p.organization_id,p.project_id,p.region_code,
                           p.recording_id,p.episode_id,p.qc_report_id)
        WHERE p.organization_id=%s AND p.project_id=%s AND p.region_code=%s
          AND p.status='QC_FAILED'
        """,
        (organization_id, project_id, region_code),
    )
    result = {}
    for recording, document in cursor.fetchall():
        findings = result.setdefault(recording, [])
        for finding in document["quality_report"]["findings"]:
            if finding["severity"].upper() == "INFO":
                continue
            findings.append(TaskIssueFinding.model_validate(finding))
        for code in document.get("finding_codes", []):
            if not any(item.code == code for item in findings):
                findings.append(
                    TaskIssueFinding(
                        code=code,
                        severity="ERROR",
                        message={
                            "OPENARM_CAPTURE_FRAME_INVALID": "采集包含无效帧",
                            "OPENARM_CAPTURE_VECTOR_INVALID": "关节或动作向量包含缺失值 / 无效值",
                            "OPENARM_CAPTURE_VECTOR_LAYOUT_MISMATCH": (
                                "有效关节或动作缺失，或名称 / 单位不一致"
                            ),
                            "QUALITY_RISK": "自动检查存在风险项",
                        }.get(code, code),
                    )
                )
    return {key: tuple(values) for key, values in result.items()}


def recording_package(
    capture: CaptureProgress,
    findings=(),
    *,
    episodes: tuple[RecordingEpisodeWorkflow, ...] = (),
) -> TaskStatusPackageFact:
    downstream = min(episodes, key=_annotation_priority) if episodes else RecordingEpisodeWorkflow()
    fact = TaskStatusPackageFact(
        task_id=capture.collection_task_id,
        rollout_id=capture.rollout_id,
        data_package_id=capture.data_package_id,
        rollout_status=capture.upload_state,
        upload_status=capture.upload_state,
        raw_committed=capture.received,
        duplicate_of_rollout_id=capture.canonical_episode_id,
        recording_id=capture.recording_id,
        source_import_id=capture.source_import_id,
        resolution_status="DISCARDED" if capture.stage == "DISCARDED" else None,
        upload_failure_code=capture.error_code if not capture.received else None,
        verification_status=(
            "REJECTED"
            if capture.stage == "FAILED" and not capture.recording_id
            else "RAW_VERIFIED"
            if capture.recording_id
            else None
        ),
        verification_reason_code=capture.error_code,
        qc_status=capture.qc_outcome,
        lance_ready=capture.stage == "READY",
        annotation_task_id=downstream.annotation_task_id,
        annotation_status=downstream.annotation_status,
        review_decision=downstream.review_decision,
        published=bool(episodes) and all(episode.published for episode in episodes),
        workflow_status=(
            "TECHNICAL_FAILED"
            if capture.stage in {"FAILED", "BLOCKED"}
            else "SUCCEEDED"
            if capture.stage == "READY"
            else "RUNNING"
            if capture.stage in {"PREPARING", "PROCESSING"}
            else "PENDING"
        ),
        workflow_stage=(
            "dispatch"
            if capture.stage == "BLOCKED"
            else "awaiting_slice"
            if capture.stage == "AWAITING_SLICE"
            else "alignment"
            if capture.qc_outcome == "PASS"
            else "verification"
        ),
        workflow_error_code=capture.error_code,
        quality_findings=findings,
    )
    return replace(
        fact,
        episode_facts=tuple(_episode_fact(fact, episode) for episode in episodes),
    )


def _episode_fact(
    package: TaskStatusPackageFact, episode: RecordingEpisodeWorkflow
) -> TaskStatusPackageFact:
    status = episode.processing_status
    return replace(
        package,
        qc_status=episode.qc_status,
        lance_ready=status == "READY",
        annotation_task_id=episode.annotation_task_id,
        annotation_status=episode.annotation_status,
        review_decision=episode.review_decision,
        published=episode.published,
        verification_reason_code=episode.error_code,
        workflow_error_code=episode.error_code,
        workflow_status=(
            "TECHNICAL_FAILED"
            if status == "FAILED" or episode.error_code
            else "RUNNING"
            if status in {"QC_RUNNING", "ALIGNING"}
            else "SUCCEEDED"
            if status == "READY"
            else "PENDING"
        ),
        workflow_stage=(
            "dispatch"
            if status == "PENDING_QC" and episode.error_code
            else "alignment"
            if episode.qc_status == "PASS"
            else "verification"
        ),
        source_episode_status="FAILED" if status == "FAILED" else None,
        episode_facts=(),
    )


def merge_recording_facts(tasks, packages, captures, findings, selected_id, *, workflows=None):
    """One package per upload; replace matching legacy facts instead of double counting."""
    visible_tasks = {task.task_id for task in tasks}
    continuous = [
        item
        for item in captures
        if item.capture_mode == "CONTINUOUS" and item.collection_task_id in visible_tasks
    ]
    by_key = {(item.task_id, item.data_package_id): item for item in packages}
    additions = []
    for capture in continuous:
        if selected_id is not None and capture.collection_task_id != selected_id:
            continue
        key = (capture.collection_task_id, capture.data_package_id)
        if key not in by_key:
            additions.append(capture)
        by_key[key] = recording_package(
            capture,
            findings.get(capture.recording_id, ()),
            episodes=(workflows or {}).get(capture.recording_id, ()),
        )
    # Task totals must cover all tasks, even when the package list is filtered.
    # Existing registered facts may include manual recordings; only direct uploads
    # absent from the selected package projection add counts to that task.
    updated = []
    for task in tasks:
        if selected_id is not None and task.task_id != selected_id:
            extra = [item for item in continuous if item.collection_task_id == task.task_id]
        else:
            extra = [item for item in additions if item.collection_task_id == task.task_id]
        updated.append(
            replace(
                task,
                registered_count=task.registered_count + len(extra),
                received_count=task.received_count + sum(item.received for item in extra),
                confirmed_duration_seconds=task.confirmed_duration_seconds
                + sum(item.duration_seconds or 0 for item in extra if item.received),
            )
        )
    return tuple(updated), tuple(by_key.values())

"""Continuous uploads in the existing dashboard's package lifecycle projection."""

from dataclasses import replace

from hc_data_platform.collection_tasks.captures import CaptureProgress

from .models import TaskIssueFinding
from .repository import TaskStatusPackageFact


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


def recording_package(capture: CaptureProgress, findings=()) -> TaskStatusPackageFact:
    return TaskStatusPackageFact(
        task_id=capture.collection_task_id,
        rollout_id=capture.rollout_id,
        data_package_id=capture.data_package_id,
        rollout_status=capture.upload_state,
        upload_status=capture.upload_state,
        raw_committed=capture.received,
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
        workflow_status="TECHNICAL_FAILED" if capture.stage in {"FAILED", "BLOCKED"} else "RUNNING",
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


def merge_recording_facts(tasks, packages, captures, findings, selected_id):
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
        by_key[key] = recording_package(capture, findings.get(capture.recording_id, ()))
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

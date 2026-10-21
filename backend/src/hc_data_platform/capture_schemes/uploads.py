"""Bind upload sessions to server-owned recording snapshots in the upload transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hc_data_platform.core.errors import ProblemException

from . import plans
from .common import Document, reject
from .models import PrepareRecording

if TYPE_CHECKING:
    from hc_data_platform.robot_ingest.models import RobotIngestUpload


def freeze_upload(db: Any, upload: RobotIngestUpload) -> Document:
    target = upload.target
    scope = (target.organization_id, target.project_id, target.region_code)
    reference = upload.manifest.capture_plan
    if reference:
        row = db.execute(
            """SELECT document FROM ingest.capture_recording_snapshots
            WHERE organization_id=%s AND robot_id=%s AND recording_id=%s
            AND project_id=%s AND region_code=%s""",
            (scope[0], upload.authenticated_robot_id, reference.recording_id, scope[1], scope[2]),
        ).fetchone()
        if row is None:
            reject("CAPTURE_VERSION_MISMATCH", "录制快照不存在或不属于当前机器人")
        snapshot = row[0]
        expected = {
            "collection_task_id": target.collection_task_id,
            "dataset_id": target.dataset_id,
            "source_format": upload.source_format,
            **reference.model_dump(mode="json"),
        }
        if any(snapshot.get(key) != value for key, value in expected.items()):
            reject("CAPTURE_VERSION_MISMATCH", "上传引用与服务端录制快照不一致")
        return dict(snapshot)
    plan = plans.get_plan(db, scope, target.collection_task_id)
    if plan is None:
        reject("CAPTURE_PLAN_REQUIRED", "任务采集方案待配置", compatibility="LEGACY_UNRESOLVED")
    if (
        upload.manifest.processing_config is not None
        and upload.manifest.processing_config != plan["scheme"]
    ):
        reject("CAPTURE_VERSION_MISMATCH", "旧客户端配置引用与任务方案不一致")
    snapshot = plans.prepare_recording(
        db,
        scope,
        target.collection_task_id,
        upload.authenticated_robot_id,
        PrepareRecording(
            expected_plan_revision=plan["revision"],
            recording_id=upload.upload_id,
            source_format=upload.source_format,
        ),
        "robot:" + upload.ingest_identity_id,
    )
    # Compatibility is recorded separately from the immutable snapshot's hash.
    return {**snapshot, "reference_origin": "SERVER_TASK_PLAN_COMPATIBILITY"}


def snapshot_or_problem(db: Any, upload: RobotIngestUpload) -> Document:
    try:
        # A configuration problem preserves Raw upload permission, but must not
        # leave half a plan mutation behind or become an automatic technical retry.
        with db.transaction():
            return freeze_upload(db, upload)
    except ProblemException as exc:
        return {
            "error": exc.problem.code,
            "details": exc.problem.details,
            "detail": exc.problem.detail,
            "source": "TASK_PLAN_UNRESOLVED",
            "retryable": False,
        }

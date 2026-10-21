"""Browser intake freezes one task receipt, including unresolved configurations.

The upload owner persists the returned document in its immutable upload session.
An accepted session is always resumed before asking this port for a new receipt.
"""

from collections.abc import Callable
from typing import Any

from hc_data_platform.core.errors import ProblemException

from . import associations, plans
from .common import Document, lock, reject
from .ports import unresolved_receipt
from .references import PrepareRecording, Scope


class PostgresWebUploadPlans:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connections = connection_factory

    def prepare(
        self,
        scope: Scope,
        upload_id: str,
        *,
        dataset_id: str,
        task_id: str | None,
        robot_id: str | None,
        source_format: str,
        actor: str,
    ) -> Document:
        if not task_id or not robot_id:
            return unresolved_receipt("CAPTURE_PLAN_REQUIRED", "此上传尚未选择任务及参与实例。")
        with self._connections() as db:
            lock(db, "web-upload", *scope, upload_id)
            try:
                with db.transaction():
                    return self._prepare(
                        db, scope, upload_id, dataset_id, task_id, robot_id, source_format, actor
                    )
            except ProblemException as exc:
                return unresolved_receipt(
                    exc.problem.code, exc.problem.detail, **exc.problem.details
                )

    @staticmethod
    def _prepare(
        db: Any,
        scope: Scope,
        upload_id: str,
        dataset_id: str,
        task_id: str,
        robot_id: str,
        source_format: str,
        actor: str,
    ) -> Document:
        recording_id = "web-" + upload_id
        expected = {
            "organization_id": scope[0],
            "project_id": scope[1],
            "region_code": scope[2],
            "collection_task_id": task_id,
            "dataset_id": dataset_id,
            "robot_id": robot_id,
            "source_format": source_format,
        }
        # A crash after preparing but before writing upload-session.json must not
        # select a newer revision on restart, including after participants change.
        saved = db.execute(
            """SELECT document FROM ingest.capture_recording_snapshots
            WHERE organization_id=%s AND robot_id=%s AND recording_id=%s""",
            (scope[0], robot_id, recording_id),
        ).fetchone()
        if saved:
            snapshot = dict(saved[0])
            if any(snapshot.get(key) != value for key, value in expected.items()):
                reject("CAPTURE_VERSION_MISMATCH", "网页上传与已有录制快照归属不一致。")
            associations.require_robot(db, scope[0], robot_id)
            return snapshot
        record = plans.task_record(db, scope, task_id, for_update=True)
        if record["dataset_id"] != dataset_id:
            reject("CAPTURE_PLAN_TARGET_MISMATCH", "上传数据集与任务采集计划不一致。")
        plan = plans.get_plan(db, scope, task_id)
        if plan is None:
            reject("CAPTURE_PLAN_REQUIRED", "任务采集方案待配置。")
        return plans.prepare_recording(
            db,
            scope,
            task_id,
            robot_id,
            PrepareRecording.model_validate(
                {
                    "expected_plan_revision": plan["revision"],
                    "recording_id": recording_id,
                    "source_format": source_format,
                }
            ),
            actor,
        )

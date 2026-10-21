"""Owned transaction operations for task and upload persistence adapters.

The caller supplies its existing transaction. These operations neither commit nor
open another connection, so a rejected task/upload cannot leave a partial plan.
Callers do not depend on scheme table names, lock ordering or snapshot selection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import plans, uploads
from .common import Document, lock, reject
from .references import Scope, SetTaskPlan

if TYPE_CHECKING:
    from hc_data_platform.robot_ingest.models import RobotIngestUpload


def bind_task_plan(
    transaction: Any, scope: Scope, task_id: str, command: SetTaskPlan, actor: str
) -> None:
    plans.save_plan(transaction, scope, task_id, command, actor)


def require_unbound_task(
    transaction: Any, organization_id: str, project_id: str, task_id: str
) -> None:
    """The task owner must hold its task row lock before changing the dataset."""
    row = transaction.execute(
        """SELECT 1 FROM collection_tasks.capture_plan_heads
        WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
        (organization_id, project_id, task_id),
    ).fetchone()
    if row:
        reject(
            "CAPTURE_PLAN_TARGET_MISMATCH",
            "任务的数据集已保存在采集计划中，请新建目标任务。",
        )


def bind_upload(transaction: Any, upload: RobotIngestUpload) -> Document | None:
    if upload.source_format not in {"MCAP", "LEROBOT_V3", "CAPTURE_BUNDLE"}:
        return None
    # Start the caller's transaction before creating the snapshot savepoint.
    lock(
        transaction,
        "upload",
        upload.target.organization_id,
        upload.ingest_identity_id,
        upload.target.collection_task_id,
        upload.client_upload_id,
    )
    return uploads.snapshot_or_problem(transaction, upload)


def bind_raw_snapshot(
    transaction: Any, scope: Scope, raw_source_id: str, snapshot: Document
) -> None:
    """Persist the browser's frozen receipt in the caller's Raw graph transaction."""
    from .processing import save_raw_snapshot

    save_raw_snapshot(transaction, scope, raw_source_id, snapshot)

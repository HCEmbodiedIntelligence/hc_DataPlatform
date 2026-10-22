"""Explicitly resolve a rejected intake format without replacing its original receipt."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import plans
from .common import Document, audit, digest, encoded, reject
from .models import PrepareRecording
from .processing import Scope, require_snapshot, validate_snapshot

KIND = "RAW_CONFIGURATION_RECOVERY"


def recovery_key(scope: Scope, raw_source_id: str) -> str:
    return digest([*scope, raw_source_id])


def resolved_recovery(
    db: Any, scope: Scope, raw_source_id: str, raw: Document, original: Document
) -> Document:
    rows = db.execute(
        """SELECT source_sha256,scheme_id,scheme_version,document
        FROM ingest.capture_legacy_references
        WHERE organization_id=%s AND legacy_kind=%s AND legacy_key=%s""",
        (scope[0], KIND, recovery_key(scope, raw_source_id)),
    ).fetchall()
    if not rows:
        return original
    expected = dict(zip(("organization_id", "project_id", "region_code"), scope, strict=True))
    expected.update(raw_source_id=raw_source_id, original_snapshot_sha256=digest(original))
    if len(rows) != 1:
        reject("CAPTURE_VERSION_MISMATCH", "同一 Raw 存在不同恢复回执")
    source_sha, scheme, version, receipt = rows[0]
    snapshot = receipt.get("snapshot", {})
    if (
        source_sha != digest(original)
        or receipt.get("schema_version") != "capture-raw-recovery/v1"
        or any(receipt.get(key) != value for key, value in expected.items())
        or snapshot.get("scheme", {}).get("scheme_id") != scheme
        or snapshot.get("scheme", {}).get("version") != version
    ):
        reject("CAPTURE_VERSION_MISMATCH", "Raw 恢复回执与原始记录不一致")
    return validate_snapshot(snapshot, scope, raw)


def recover_for_retry(db: Any, scope: Scope, raw_source_id: str, actor: str) -> Document:
    """Pin the current task plan only for an explicitly retried format rejection.

    Existing valid snapshots, including prior recoveries, never follow later plan
    changes. The immutable legacy-reference journal owns the recovery receipt.
    The caller commits this receipt and its retry event in the same transaction.
    """
    from .processing import resolve_raw_snapshot

    row = db.execute(
        """SELECT s.dataset_id,s.collection_task_id,s.robot_id,s.source_format,
                  s.upload_id,j.status,s.derived_episode_count
        FROM ingest.raw_sources s JOIN ingest.raw_ingest_jobs j
        USING(organization_id,project_id,region_code,raw_source_id)
        WHERE s.organization_id=%s AND s.project_id=%s AND s.region_code=%s
          AND s.raw_source_id=%s AND s.raw_status='COMMITTED'
        FOR UPDATE OF s,j""",
        (*scope, raw_source_id),
    ).fetchone()
    if row is None:
        reject("RAW_SOURCE_NOT_FOUND", "当前范围内没有可重试的原始数据")
    original = resolve_raw_snapshot(db, scope, raw_source_id)
    if not original.get("error"):
        return original
    if (
        original.get("source") != "TASK_PLAN_UNRESOLVED"
        or original.get("error") != "CAPTURE_FORMAT_UNSUPPORTED"
        or row[3] not in {"MCAP", "CAPTURE_BUNDLE"}
    ):
        return require_snapshot(original)
    prepared = db.execute(
        """SELECT 1 FROM ingest.mcap_processing_files
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
          AND status IN ('READY','RUNNING','PENDING')
        UNION ALL SELECT 1 FROM ingest.raw_source_episodes
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
          AND (status='READY' OR data_ref IS NOT NULL)
        UNION ALL SELECT 1 FROM ingest.recording_uploads
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND upload_id=%s""",
        (
            *scope,
            raw_source_id,
            *scope,
            raw_source_id,
            *scope,
            uuid5(NAMESPACE_URL, "robot-recording:" + row[4]),
        ),
    ).fetchone()
    if row[5] != "FAILED" or row[6] or prepared:
        reject("CAPTURE_RETRY_UNAVAILABLE", "仅尚未生成录制或成果的失败记录可恢复采集方案")
    plan = plans.get_plan(db, scope, row[1])
    if plan is None:
        reject("CAPTURE_PLAN_REQUIRED", "请先为原采集任务配置支持此格式的方案")
    snapshot = plans.prepare_recording(
        db,
        scope,
        row[1],
        row[2],
        PrepareRecording(
            expected_plan_revision=plan["revision"],
            recording_id="raw-retry-" + recovery_key(scope, raw_source_id),
            source_format=row[3],
        ),
        actor,
    )
    snapshot = {**snapshot, "reference_origin": "EXPLICIT_PROCESSING_RETRY"}
    raw = dict(
        zip(("dataset_id", "collection_task_id", "robot_id", "source_format"), row[:4], strict=True)
    )
    require_snapshot(validate_snapshot(snapshot, scope, raw))
    receipt = {
        "schema_version": "capture-raw-recovery/v1",
        **dict(zip(("organization_id", "project_id", "region_code"), scope, strict=True)),
        "raw_source_id": raw_source_id,
        "original_snapshot_sha256": digest(original),
        "original_snapshot": original,
        "snapshot": snapshot,
        "requested_by": actor,
    }
    db.execute(
        """INSERT INTO ingest.capture_legacy_references
        (organization_id,legacy_kind,legacy_key,source_sha256,scheme_id,scheme_version,document)
        VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)""",
        (
            scope[0],
            KIND,
            recovery_key(scope, raw_source_id),
            digest(original),
            snapshot["scheme"]["scheme_id"],
            snapshot["scheme"]["version"],
            encoded(receipt),
        ),
    )
    audit(
        db, scope[0], snapshot["scheme"]["scheme_id"], actor, "RECOVER_RAW_CONFIGURATION", receipt
    )
    return snapshot

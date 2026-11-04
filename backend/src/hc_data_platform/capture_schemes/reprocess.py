"""Explicit, idempotent execution snapshots without mutating Raw receipt history."""

from types import SimpleNamespace

from pydantic import Field

from . import plans
from .common import audit, digest, encoded, now, reject
from .processing import require_snapshot, resolve_raw_snapshot, validate_snapshot
from .references import Identifier, Strict
from .schema import format_version


class ReprocessMcap(Strict):
    request_id: Identifier
    plan_revision: int = Field(ge=1)


def _retryable_intake(original):
    # The selected plan is checked again against the bound robot and dataset.
    return original.get("source") == "TASK_PLAN_UNRESOLVED" and original.get("error") in {
        "CAPTURE_PLAN_REQUIRED", "CAPTURE_MAPPING_UNVERIFIED", "CAPTURE_MAPPING_MISMATCH",
        "CAPTURE_ASSOCIATION_REQUIRED", "CAPTURE_FORMAT_UNSUPPORTED",
    }


def _bound_intake(db, scope, raw_id, original):
    """A stored rejection proves intake history. Raw owns the subsequently bound identity."""
    from .recovery import recovery_key

    row = db.execute(
        """SELECT s.dataset_id,s.collection_task_id,s.robot_id,s.source_format,r.document,r.content_sha256
        FROM ingest.raw_sources s JOIN ingest.raw_capture_snapshots r
        USING (organization_id,project_id,region_code,raw_source_id)
        WHERE s.organization_id=%s AND s.project_id=%s AND s.region_code=%s AND s.raw_source_id=%s
        AND s.source_format='MCAP' AND s.raw_status='COMMITTED'""", (*scope, raw_id),
    ).fetchone()
    if not row or not all(row[:3]) or row[4] != original or row[5] != digest(original):
        reject("CAPTURE_VERSION_MISMATCH", "无法核实原始上传回执及其任务、机器人归属")
    return {"dataset_id": row[0], "collection_task_id": row[1], "robot_id": row[2],
            "source_format": row[3], "recording_id": "raw-retry-" + recovery_key(scope, raw_id)}


def request(db, scope, raw, manifest, command, actor):
    from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
    from hc_data_platform.lerobot_imports.mcap_processing import enqueue_files, lock_source

    raw_id = raw.raw_source_id
    lock_source(db, scope, raw_id)
    previous = db.execute(
        """SELECT document,content_hash FROM ingest.capture_reprocess_requests
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
        AND request_id=%s""", (*scope, raw_id, command.request_id),
    ).fetchone()
    if previous:
        if previous[1] != digest(previous[0]) or previous[0]["plan_revision"] != command.plan_revision:
            reject("CAPTURE_REPROCESS_CONFLICT", "此请求编号已绑定其他方案版本")
        return previous[0]
    row = db.execute(
        """SELECT dataset_id,collection_task_id,robot_id,source_format,raw_status
        FROM ingest.raw_sources WHERE organization_id=%s AND project_id=%s AND region_code=%s
        AND raw_source_id=%s FOR UPDATE""", (*scope, raw_id),
    ).fetchone()
    if not row or row[3:] != ("MCAP", "COMMITTED"):
        reject("RAW_SOURCE_UNAVAILABLE", "需要当前范围内已提交的 MCAP 原件")
    dataset, task, robot = row[:3]
    if not task or not robot:
        reject("CAPTURE_PLAN_REQUIRED", "请先为此上传关联采集任务和机器人")
    current = plans.task_record(db, scope, task, for_update=True)
    if current["dataset_id"] != dataset or raw.dataset_id != dataset:
        reject("CAPTURE_PLAN_TARGET_MISMATCH", "原件与任务数据集不一致")
    statuses = db.execute(
        """SELECT DISTINCT ON (source_path) status FROM ingest.mcap_processing_files
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
        ORDER BY source_path,created_at DESC,processing_id""", (*scope, raw_id),
    ).fetchall()
    if any(row[0] in {"PENDING", "RUNNING"} for row in statuses):
        reject("CAPTURE_REPROCESS_BUSY", "该 Raw 正在处理，请等待本次执行结束")
    original = resolve_raw_snapshot(db, scope, raw_id)
    if _retryable_intake(original):
        identity = _bound_intake(db, scope, raw_id, original)
        source_hash = digest(original)
    else:
        identity = require_snapshot(original)
        source_hash = original["snapshot_sha256"]
    resolved = plans.for_robot(db, scope, task, robot, "MCAP", plan_revision=command.plan_revision)
    plan = resolved["plan"]
    if resolved["adapter"]["parser_version"] not in {"native-mcap-recording/3", "native-mcap-recording/4"}:
        reject("CAPTURE_PARSER_UPGRADE_REQUIRED", "请发布使用新版解析器的方案再重处理")
    plans.check_dataset(db, scope, dataset, task, plan["output"], freeze=True)
    content = {
        "schema_version": "capture-recording-snapshot/v1",
        "organization_id": scope[0], "project_id": scope[1], "region_code": scope[2],
        "collection_task_id": task, "dataset_id": dataset, "robot_id": robot,
        "task_instruction": db.execute("SELECT name FROM collection_tasks.collection_tasks WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s", (*scope[:2], task)).fetchone()[0],
        "recording_id": identity["recording_id"], "plan_revision": plan["revision"],
        "plan_sha256": plan["plan_sha256"], "scheme": plan["scheme"],
        "mapping_version": resolved["mapping"]["mapping_version"],
        "mapping_sha256": resolved["mapping"]["content_sha256"],
        "source_format": "MCAP", "effective_adapter": resolved["adapter"],
        "output": plan["output"], "output_contract_sha256": plan["output_contract_sha256"],
        "source": "TASK_PLAN_REPROCESS", "prepared_at": now(),
        "quality_policy": resolved["quality_policy"],
        "reprocessing": {"raw_source_id": raw_id, "request_id": command.request_id,
                         "original_snapshot_sha256": source_hash,
                         "format_version": format_version(plan["output"])},
    }
    snapshot = {**content, "snapshot_sha256": digest(content)}
    enqueue_files(db, scope, raw, manifest, SimpleNamespace(collection_task_id=task, robot_id=robot),
                  McapProcessingConfig.model_validate(resolved["adapter"]["configuration"]), reference=snapshot)
    records = db.execute(
        """SELECT processing_id,source_document FROM ingest.mcap_processing_files
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
        AND config_reference->>'snapshot_sha256'=%s ORDER BY source_path""",
        (*scope, raw_id, snapshot["snapshot_sha256"]),
    ).fetchall()
    if not records:
        reject("CAPTURE_REPROCESS_CONFLICT", "原件已有冲突成果，未创建执行记录")
    document = {"schema_version": "capture-reprocess/v1", "raw_source_id": raw_id,
                "request_id": command.request_id, "plan_revision": command.plan_revision,
                "original_snapshot": original, "execution_snapshot": snapshot,
                "files": [{"processing_id": ident, "source": source} for ident, source in records],
                "created_by": actor, "created_at": now()}
    db.execute(
        """INSERT INTO ingest.capture_reprocess_requests
        (organization_id,project_id,region_code,raw_source_id,request_id,plan_revision,document,content_hash)
        VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
        (*scope, raw_id, command.request_id, command.plan_revision, encoded(document), digest(document)),
    )
    audit(db, scope[0], plan["scheme"]["scheme_id"], actor, "REPROCESS_RAW", document)
    return document


def execution_snapshot(db, scope, raw_id, snapshot):
    """Read back both immutable authorities before downstream QC and storage."""
    original = resolve_raw_snapshot(db, scope, raw_id)
    if snapshot == original:
        return require_snapshot(original)
    ref = (snapshot or {}).get("reprocessing", {})
    row = db.execute(
        """SELECT document,content_hash FROM ingest.capture_reprocess_requests
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
        AND request_id=%s""", (*scope, raw_id, ref.get("request_id")),
    ).fetchone()
    if not row or digest(row[0]) != row[1] or row[0]["execution_snapshot"] != snapshot:
        reject("CAPTURE_VERSION_MISMATCH", "重处理快照与原件或执行记录不一致")
    recorded = row[0]["original_snapshot"]
    if _retryable_intake(recorded):
        if original.get("error") and original != recorded:
            return require_snapshot(original)
        identity = _bound_intake(db, scope, raw_id, recorded)
        if ref.get("original_snapshot_sha256") != digest(recorded):
            reject("CAPTURE_VERSION_MISMATCH", "重处理引用的上传回执不一致")
    else:
        identity = require_snapshot(original)
        if recorded != identity:
            reject("CAPTURE_VERSION_MISMATCH", "重处理快照与原件不一致")
    return require_snapshot(validate_snapshot(snapshot, scope, identity))

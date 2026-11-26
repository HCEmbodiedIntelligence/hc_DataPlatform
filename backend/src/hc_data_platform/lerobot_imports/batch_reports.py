"""Scoped, restartable upload QC downloads. A completed snapshot is published atomically."""

import contextvars
import fcntl
import hashlib
import json
import logging
import tempfile
from pathlib import Path
from threading import Thread

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.errors import problem

from .batch_report_records import failed_rows
from .batch_report_xlsx import write_workbook
from .configuration import connections


def report_error(*, status, code, title):
    return problem(status=status, code=code, title=title, detail=title)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def snapshots(scope, numbers):
    with connections()() as db:
        db.execute("SET TRANSACTION READ ONLY")
        batches = db.execute(
            """SELECT b.batch_key,b.batch_number,j.status
               FROM ingest.upload_batches b
               LEFT JOIN workflow.jobs j ON b.batch_key LIKE 'oss:%%'
                 AND (j.organization_id,j.project_id,j.workflow_id) =
                     (b.organization_id,b.project_id,substr(b.batch_key,5))
               WHERE (b.organization_id,b.project_id,b.region_code)=(%s,%s,%s)
                 AND b.batch_number=ANY(%s)""", (*scope, list(numbers))
        ).fetchall()
        if len(batches) != len(set(numbers)):
            raise report_error(status=404, code="UPLOAD_BATCH_NOT_FOUND", title="上传批次不存在")
        for _, number, status in batches:
            if status in ("PENDING", "RUNNING"):
                raise report_error(status=409, code="UPLOAD_BATCH_REGISTERING",
                                   title=f"第 {number} 批仍在登记文件，请稍后生成报告")
        return failed_rows(db, scope, {key: number for key, number, _ in batches})


def directory(scope, number):
    cache = get_settings().object_store_cache_root or tempfile.gettempdir()
    return Path(cache) / "upload-qc-reports" / _digest(["failed-outcomes/v2", *scope, number])


def _status(folder):
    try:
        return json.loads((folder / "status.json").read_bytes())
    except FileNotFoundError:
        return {}


def _save(folder, state):
    tmp = folder / "status.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    tmp.replace(folder / "status.json")


def _lock(path):
    lock = path.open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def read_status(scope, number, version):
    folder = directory(scope, number)
    state = _status(folder)
    if state.get("version") != version:
        raise report_error(status=404, code="QC_REPORT_NOT_CURRENT", title="请重新生成本批次报告")
    if state["phase"] == "running":
        lock = _lock(folder / "lock")
        if lock:
            lock.close()
            state = _status(folder)
            if state.get("phase") == "running":
                return {**state, "phase": "failed", "message": "生成中断，请重试"}
    return state


def _generate(folder, lock, slot, number, records, state):
    try:
        tmp = folder / "report.tmp"
        write_workbook(tmp, number, records)
        state["completed"] = len(records)
        tmp.replace(folder / (state["version"] + ".xlsx"))
        for old in folder.glob("*.xlsx"):
            if old.stem != state["version"]:
                old.unlink(missing_ok=True)
        _save(folder, {**state, "phase": "ready"})
    except Exception:
        logging.getLogger(__name__).exception(
            "Upload batch %s QC report failed at item %s", number, state["completed"] + 1
        )
        _save(
            folder,
            {
                **state,
                "phase": "failed",
                "message": f"读取或校验第 {state['completed'] + 1} 项失败，请重试",
            },
        )
    finally:
        slot.close()
        lock.close()


def prepare(scope, number, storage):
    records = snapshots(scope, [number])[number]
    version = _digest(["failed-outcomes/v2", scope, number, records])
    folder = directory(scope, number)
    folder.mkdir(parents=True, exist_ok=True)
    lock = _lock(folder / "lock")
    if not lock:
        state = _status(folder)
        if state.get("version") == version:
            return state
        raise report_error(
            status=409, code="QC_REPORT_BUSY", title="该批次正在生成报告，请稍后重试"
        )
    slot = None
    handed_off = False
    try:
        state = _status(folder)
        if (
            state.get("version") == version
            and state["phase"] == "ready"
            and (folder / (version + ".xlsx")).is_file()
        ):
            return state
        # Bound concurrent workbook writers across API processes.
        for i in range(2):
            slot = _lock(folder.parent / f"worker-{i}.lock")
            if slot:
                break
        if not slot:
            raise report_error(
                status=429, code="QC_REPORT_CAPACITY", title="报告生成任务已满，请稍后重试"
            )
        state = {"version": version, "phase": "running", "total": len(records), "completed": 0}
        _save(folder, state)
        Thread(
            target=contextvars.copy_context().run,
            args=(_generate, folder, lock, slot, number, records, state.copy()),
            daemon=True,
        ).start()
        handed_off = True
        return state
    finally:
        if not handed_off:
            lock.close()
            if slot:
                slot.close()

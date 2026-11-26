"""Register selected OSS MCAP versions in the API before dispatching QC."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import ProblemException

from .configuration import connections
from .oss_batch import BATCH_WORKFLOW, BatchAccepted
from .oss_imports import CommitOssImport, _commit_source, _inspect_source, failure


def register_if_mcap(service, auth, batch, identity):
    command = batch.command
    scope = {k: getattr(batch, k) for k in ("organization_id", "project_id", "region_code")}
    service._authorize(auth, **scope, task_id=command.collection_task_id)
    inspect = getattr(service.storage, "inspect_reference", None)
    if inspect is None:
        return None
    with TemporaryDirectory(prefix="oss-version-register-") as directory:
        inventory = _inspect_source(service.storage, command.source_uri, Path(directory), mcap_references_only=True)
        if inventory is None:
            return None
        if inventory["inventory_sha256"] != command.inventory_sha256:
            raise failure("OSS_IMPORT_SOURCE_CHANGED", "OSS 文件版本已变化，请重新检查。", 409)
        selected = command.recording_paths or [None]
        if inventory.get("archive_members") and selected == [None]:
            raise failure("OSS_IMPORT_SELECT_RECORDING", "请选择要登记的 MCAP 文件。")
        base = command.model_dump(mode="json", exclude={"request_id", "recording_paths"})
        def register(path):
            item = CommitOssImport(**base, recording_path=path)
            try:
                from hc_data_platform.ingest.upload_batches import register_batch

                receipt = _commit_source(service, auth, scope, item, Path(directory), inspected=inventory)
                register_batch((batch.organization_id, batch.project_id, batch.region_code),
                               "oss", identity, receipt.import_id)
                return None
            except ProblemException as exc:
                if exc.problem.status >= 500 or exc.problem.retryable:
                    raise
                return f"{path or command.source_uri.rsplit('/', 1)[-1]} · {exc.problem.code}"
        return _register_items(batch, identity, selected, register)


def _register_items(batch, identity, selected, register):
    command = batch.command
    accepted = BatchAccepted(batch_id=identity, total=len(selected))
    with connections()() as db:
        # Keep concurrent HTTP retries from duplicating a batch. Completed members
        # also have deterministic Raw identities if an API process exits mid-request.
        db.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (identity,))
        row = db.execute(
            "SELECT status,workflow_run_id,result FROM workflow.jobs WHERE workflow_id=%s",
            (identity,),
        ).fetchone()
        if row and (row[1] or row[0] in {"SUCCEEDED", "TECHNICAL_FAILED"}):
            return accepted
        summary = row[2] if row else dict(
            region_code=batch.region_code, total=len(selected), submitted=0, failed=0,
            first_failure=None, source_name=command.source_uri.rstrip("/").rsplit("/", 1)[-1],
        )
        if not row:
            db.execute("""INSERT INTO workflow.jobs (job_id,organization_id,workflow_id,project_id,
                resource_id,job_type,status,stage,result,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,'RUNNING','registering',%s::jsonb,now(),now())""",
                (uuid5(NAMESPACE_URL, f"hc-data-platform/workflow-job/{identity}"),batch.organization_id,
                 identity,batch.project_id,str(command.request_id),BATCH_WORKFLOW,json.dumps(summary)))
        db.commit()
        offset = summary["submitted"] + summary["failed"]
        with ThreadPoolExecutor(max_workers=4) as executor:
            pending = [executor.submit(copy_context().run, register, path) for path in selected[offset:]]
            for future in pending:
                error = future.result()
                summary["submitted" if error is None else "failed"] += 1
                if error and not summary["first_failure"]:
                    summary["first_failure"] = error
                done = summary["submitted"] + summary["failed"] == len(selected)
                status = ("TECHNICAL_FAILED" if summary["failed"] else "SUCCEEDED") if done else "RUNNING"
                db.execute("""UPDATE workflow.jobs SET result=%s::jsonb,status=%s,stage=%s,updated_at=now()
                              WHERE workflow_id=%s""",
                           (json.dumps(summary),status,"completed" if done else "registering",identity))
                db.commit()
    return accepted

"""One durable dispatch decision inside the robot Raw commit transaction."""

from types import SimpleNamespace

from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
from hc_data_platform.lerobot_imports.mcap_processing import (
    enqueue_files,
    lock_source,
    set_source_status,
)

from . import processing_store, recording_bridge


def mcap_eligible(upload):
    return (
        upload.state.value == "COMMITTED"
        and upload.raw_source_id is not None
        and upload.source_format == "MCAP"
        and upload.capture_mode.value == "CONTINUOUS"
        and any(a.path.lower().endswith(".mcap") for a in upload.assets)
        and all(a.state.value == "COMPLETED" for a in upload.assets)
    )


def enqueue(cursor, upload):
    if processing_store.eligible(upload):
        processing_store.enqueue(cursor, upload)
        return
    if recording_bridge.eligible(upload):
        recording_bridge.enqueue(cursor, upload)
        return
    scope = (upload.target.organization_id, upload.target.project_id, upload.target.region_code)
    if mcap_eligible(upload):
        from hc_data_platform.capture_schemes.processing import resolve_raw_snapshot

        frozen = resolve_raw_snapshot(cursor, scope, upload.raw_source_id)
        if frozen.get("error"):
            from hc_data_platform.lerobot_imports.mcap_processing import record_rejection

            record_rejection(
                cursor,
                scope,
                SimpleNamespace(
                    raw_source_id=upload.raw_source_id, dataset_id=upload.target.dataset_id
                ),
                {
                    "files": [
                        {
                            "path": a.path,
                            "size": a.expected_size_bytes,
                            "sha256": a.expected_sha256,
                            "object_key": a.object_key,
                        }
                        for a in upload.assets
                    ]
                },
                SimpleNamespace(
                    collection_task_id=upload.target.collection_task_id,
                    robot_id=upload.authenticated_robot_id,
                ),
                frozen,
            )
            return
        cfg = McapProcessingConfig.model_validate(frozen["effective_adapter"]["configuration"])
        enqueue_files(
            cursor,
            scope,
            SimpleNamespace(
                raw_source_id=upload.raw_source_id, dataset_id=upload.target.dataset_id
            ),
            {
                "files": [
                    {
                        "path": a.path,
                        "size": a.expected_size_bytes,
                        "sha256": a.expected_sha256,
                        "object_key": a.object_key,
                    }
                    for a in upload.assets
                ]
            },
            SimpleNamespace(
                collection_task_id=upload.target.collection_task_id,
                robot_id=upload.authenticated_robot_id,
            ),
            cfg,
            reference=frozen,
        )
        return
    # Receipt success does not imply that a processor exists for this format/mode.
    set_source_status(cursor, scope, upload.raw_source_id, "FAILED", "ROBOT_PROCESSOR_UNAVAILABLE")


def retry_mcap(connections, upload):
    scope = (upload.target.organization_id, upload.target.project_id, upload.target.region_code)
    with recording_bridge.scope_context(*scope), connections() as db, db.cursor() as cursor:
        lock_source(cursor, scope, upload.raw_source_id)
        cursor.execute(
            """SELECT status FROM ingest.raw_ingest_jobs
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
            (*scope, upload.raw_source_id),
        )
        job = cursor.fetchone()
        if not job or job[0] not in {"FAILED", "PENDING"}:
            return
        if job[0] == "PENDING":
            cursor.execute(
                """SELECT 1 FROM ingest.mcap_processing_files
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND raw_source_id=%s""",
                (*scope, upload.raw_source_id),
            )
            if cursor.fetchone():
                return
        from hc_data_platform.lerobot_imports.mcap_processing import retry_source

        db.commit()
    if not retry_source(scope, upload.raw_source_id, connections):
        with recording_bridge.scope_context(*scope), connections() as db, db.cursor() as cursor:
            enqueue(cursor, upload)
            db.commit()


def mcap_result(connections, upload):
    scope = (upload.target.organization_id, upload.target.project_id, upload.target.region_code)
    with recording_bridge.scope_context(*scope), connections() as db:
        job = db.execute(
            """SELECT status,last_error_code,attempts FROM ingest.raw_ingest_jobs
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
            (*scope, upload.raw_source_id),
        ).fetchone()
        rows = db.execute(
            """SELECT r.recording_id,r.status FROM (
                SELECT DISTINCT ON (source_path) recording_id FROM ingest.mcap_processing_files
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
                ORDER BY source_path,created_at DESC,processing_id
            ) f JOIN ingest.continuous_recordings r ON r.recording_id=f.recording_id
            AND r.organization_id=%s AND r.project_id=%s AND r.region_code=%s""",
            (*scope, upload.raw_source_id, *scope),
        ).fetchall()
    done = bool(job and job[0] == "SUCCEEDED" and rows)
    failed = bool(job and job[0] == "FAILED")
    recording = rows[0][0] if done and len(rows) == 1 else None
    return {
        "upload_id": upload.upload_id,
        "raw_source_id": upload.raw_source_id,
        "recording_id": recording,
        "status": ("SLICED" if all(r[1] == "SLICED" for r in rows) else "AWAITING_SLICE")
        if done
        else "FAILED"
        if failed
        else "REGISTERING",
        "page_path": "/recordings/" + recording + "/slice"
        if recording
        else "/recordings"
        if done
        else None,
        "terminal": done or failed,
        "poll_after_seconds": 0 if done or failed else 5,
        "error_code": job[1] if failed else None,
        "registration_attempts": job[2] if job else 0,
    }

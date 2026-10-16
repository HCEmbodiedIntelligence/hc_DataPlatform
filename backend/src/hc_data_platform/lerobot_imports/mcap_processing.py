"""Durable standalone MCAP preparation; shares recording registration with robots."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.continuous_recordings.asset_models import (
    CreateRecordingUploadCommand,
    RecordingAsset,
    RecordingUpload,
)
from hc_data_platform.continuous_recordings.mcap_native import (
    VERSION,
    McapProcessingConfig,
    prepare,
)
from hc_data_platform.continuous_recordings.models import RecordingScope
from hc_data_platform.continuous_recordings.registration import (
    VerifiedRecordingFacts,
)
from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.ingest.models import utc_now
from hc_data_platform.object_storage.checksums import crc64_ecma
from hc_data_platform.robot_ingest.raw_recording import publish, verify
from hc_data_platform.security.audit import canonical_hash

from .configuration import connections, validate_target


def profile(scope, robot_id, config=None):
    with connections()() as db:
        exists = db.execute(
            """SELECT 1 FROM ingest.data_sources WHERE organization_id=%s AND project_id=%s
            AND region_code=%s AND source_type='ROBOT' AND administrative_state='ENABLED'
            AND source_document->'binding'->>'robot_id'=%s
            UNION ALL SELECT 1 FROM ingest.robot_ingest_uploads
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
              AND authenticated_robot_id=%s LIMIT 1""",
            (*scope, robot_id, *scope, robot_id),
        ).fetchone()
        if not exists:
            raise problem(
                status=404,
                code="MCAP_ROBOT_NOT_FOUND",
                title="机器人不存在",
                detail="请选择当前范围内已启用的机器人。",
            )
        if config is not None:
            config = McapProcessingConfig.model_validate(config)
            db.execute(
                """INSERT INTO ingest.mcap_robot_profiles VALUES(%s,%s,%s,%s,%s::jsonb,now())
                ON CONFLICT (organization_id,project_id,region_code,robot_id)
                DO UPDATE SET configuration=EXCLUDED.configuration,updated_at=now()""",
                (*scope, robot_id, config.model_dump_json()),
            )
            db.commit()
        row = db.execute(
            """SELECT configuration FROM ingest.mcap_robot_profiles
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND robot_id=%s""",
            (*scope, robot_id),
        ).fetchone()
    return McapProcessingConfig.model_validate(row[0] if row else {})


def progress(scope, raw_id):
    with connections()() as db:
        rows = db.execute(
            """SELECT DISTINCT ON (source_path)
            processing_id,source_path,status,error,recording_id,attempts,updated_at
            FROM ingest.mcap_processing_files
            WHERE organization_id=%s AND project_id=%s
            AND region_code=%s
            AND raw_source_id=%s ORDER BY source_path,created_at DESC,processing_id""",
            (*scope, raw_id),
        ).fetchall()
    return {
        "files": [
            dict(
                zip(
                    (
                        "processing_id",
                        "path",
                        "status",
                        "error",
                        "recording_id",
                        "attempts",
                        "updated_at",
                    ),
                    row,
                    strict=True,
                )
            )
            for row in rows
        ]
    }


def start(scope, raw, manifest, command):
    validate_target(
        *scope,
        SimpleNamespace(
            processing_mode="PROCESS",
            dataset_id=raw.dataset_id,
            collection_task_id=command.collection_task_id,
            robot_id=command.robot_id,
        ),
    )
    cfg = profile(scope, command.robot_id)
    with connections()() as db:
        enqueue_files(db, scope, raw, manifest, command, cfg)
        db.commit()
    return progress(scope, raw.raw_source_id)


def enqueue_files(db, scope, raw, manifest, command, cfg):
    """Queue preparation in the caller's Raw commit transaction for every ingress."""
    from hc_data_platform.robot_ingest.recording_bridge import EVENT_TYPE

    files = [f for f in manifest["files"] if f["path"].lower().endswith(".mcap")]
    if not files:
        raise ValueError("MCAP source has no MCAP members")
    lock_source(db, scope, raw.raw_source_id)
    db.execute(
        """UPDATE ingest.raw_ingest_jobs SET job_type='CONTINUOUS_RECORDING_DISCOVERY',
        adapter_name='mcap' WHERE organization_id=%s AND project_id=%s AND region_code=%s
        AND raw_source_id=%s""",
        (*scope, raw.raw_source_id),
    )
    for file in files:
        document = {
            "path": file["path"],
            "size": file["size"],
            "sha256": file["sha256"],
            "object_key": file.get("object_key") or raw.storage_prefix + "/" + file["path"],
            "processor_version": VERSION,
        }
        identity = {
            "scope": scope,
            "raw": raw.raw_source_id,
            "file": document,
            "robot": command.robot_id,
            "task": command.collection_task_id,
            "config": cfg.model_dump(mode="json"),
            "version": VERSION,
        }
        previous = db.execute(
            """SELECT processing_id,source_document,configuration,
            collection_task_id,robot_id,dataset_id
            FROM ingest.mcap_processing_files WHERE organization_id=%s AND project_id=%s
              AND region_code=%s AND raw_source_id=%s AND source_path=%s
            ORDER BY created_at DESC,processing_id LIMIT 1""",
            (*scope, raw.raw_source_id, file["path"]),
        ).fetchone()
        if previous and tuple(previous[1:]) == (
            document,
            cfg.model_dump(mode="json"),
            command.collection_task_id,
            command.robot_id,
            raw.dataset_id,
        ):
            ident = previous[0]
        else:
            # A configuration change is a new generation, including a return to an
            # earlier profile. Old workflow deliveries must never select it instead.
            if previous:
                identity["previous_processing_id"] = previous[0]
            ident = canonical_hash(identity)
        db.execute(
            """INSERT INTO ingest.mcap_processing_files
            (organization_id,project_id,region_code,raw_source_id,processing_id,source_path,
            source_document,
             configuration,collection_task_id,robot_id,dataset_id,status)
            VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,'PENDING')
            ON CONFLICT DO NOTHING""",
            (
                *scope,
                raw.raw_source_id,
                ident,
                file["path"],
                json.dumps(document),
                cfg.model_dump_json(),
                command.collection_task_id,
                command.robot_id,
                raw.dataset_id,
            ),
        )
        state = db.execute(
            """SELECT status,attempts FROM ingest.mcap_processing_files

            WHERE organization_id=%s AND project_id=%s
            AND region_code=%s
            AND processing_id=%s FOR UPDATE""",
            (*scope, ident),
        ).fetchone()
        if state[0] in {"READY", "RUNNING"}:
            continue
        attempt = state[1] + (state[0] == "FAILED")
        db.execute(
            """UPDATE ingest.mcap_processing_files
            SET status='PENDING',error=NULL,attempts=%s,updated_at=now()

            WHERE organization_id=%s AND project_id=%s
            AND region_code=%s
            AND processing_id=%s""",
            (attempt, *scope, ident),
        )
        event = DomainEventEnvelope(
            event_id=str(uuid5(NAMESPACE_URL, f"mcap:{ident}:{attempt}")),
            event_type=EVENT_TYPE,
            aggregate_type="raw_source",
            aggregate_id=raw.raw_source_id,
            organization_id=scope[0],
            project_id=scope[1],
            region_code=scope[2],
            payload={"mcap_processing_id": ident, "attempt": attempt},
        )
        db.execute(
            """INSERT INTO core.outbox_events
            (event_id,organization_id,project_id,region_code,event_type,envelope,occurred_at,available_at)
            VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,now()) ON CONFLICT DO NOTHING""",
            (
                event.event_id,
                *scope,
                event.event_type,
                event.model_dump_json(),
                event.occurred_at,
            ),
        )
    sync_source(db, scope, raw.raw_source_id)


def process_file(handler, event, checkpoint):
    scope = (event.organization_id, event.project_id, event.region_code)
    ident = event.payload["mcap_processing_id"]
    # Session advisory lock avoids duplicate expensive work; row updates remain visible.
    with handler.connections() as lock:
        lock.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", ("mcap:" + ident,))
        try:
            with handler.connections() as db:
                lock_source(db, scope, event.aggregate_id)
                row = db.execute(
                    """SELECT source_document,configuration,collection_task_id,
                    robot_id,dataset_id,status,attempts,source_path
                    FROM ingest.mcap_processing_files
                    WHERE organization_id=%s AND project_id=%s
                    AND region_code=%s
                    AND processing_id=%s AND raw_source_id=%s""",
                    (*scope, ident, event.aggregate_id),
                ).fetchone()
                if row is None:
                    raise ValueError("MCAP processing event has no scoped source")
                if row[5] == "READY":
                    return
                if event.payload.get("attempt", row[6]) != row[6]:
                    return
                latest = db.execute(
                    """SELECT processing_id FROM ingest.mcap_processing_files
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND raw_source_id=%s AND source_path=%s
                    ORDER BY created_at DESC,processing_id LIMIT 1""",
                    (*scope, event.aggregate_id, row[7]),
                ).fetchone()
                if latest[0] != ident:
                    return
                db.execute(
                    """UPDATE ingest.mcap_processing_files SET status='RUNNING',updated_at=now()

                    WHERE organization_id=%s AND project_id=%s
                    AND region_code=%s
                    AND processing_id=%s""",
                    (*scope, ident),
                )
                sync_source(db, scope, event.aggregate_id)
                db.commit()
            original, cfg, task, robot, dataset = row[:5]
            prefix = "/".join(("derived-mcap", *scope, ident))
            receipt_key = prefix + "/receipt.json"
            uid = uuid5(NAMESPACE_URL, "native-mcap:" + ident)
            if handler.storage.head(receipt_key):
                from .service import read_bounded

                receipt = json.loads(read_bounded(handler.storage, receipt_key, 16 * 1024**2))
                recorded = RecordingUpload.model_validate(receipt["upload"])
                assets = tuple(RecordingAsset.model_validate(a) for a in receipt["assets"])
                if (
                    recorded.scope
                    != RecordingScope(
                        organization_id=scope[0], project_id=scope[1], region_code=scope[2]
                    )
                    or recorded.upload_id != uid
                ):
                    raise ValueError("MCAP derivation receipt scope mismatch")
                for asset in assets:
                    expected = (
                        original["object_key"]
                        if asset.manifest.path == "source/original/source.mcap"
                        else prefix + "/" + asset.manifest.sha256 + "/" + asset.manifest.path
                    )
                    if asset.object_key != expected:
                        raise ValueError("MCAP derivation locator mismatch")
                    verify(
                        handler.storage,
                        asset.object_key,
                        asset.manifest.size,
                        asset.manifest.sha256,
                        checkpoint,
                    )
            else:
                with TemporaryDirectory(prefix="native-mcap-") as temporary:
                    root = Path(temporary)
                    source = root / "source.mcap"
                    with source.open("wb") as output:
                        verify(
                            handler.storage,
                            original["object_key"],
                            original["size"],
                            original["sha256"],
                            checkpoint,
                            output,
                        )
                    output = root / "derived"
                    config, provenance = prepare(source, output, cfg, checkpoint)
                    # These locators are platform-owned immutable receipts, not user input.
                    provenance.update(
                        source_object=original,
                        raw_source_id=event.aggregate_id,
                        collection_task_id=task,
                        robot_id=robot,
                        dataset_id=dataset,
                    )
                    (output / "mcap-provenance.json").write_text(json.dumps(provenance))
                    declarations, locations = [], {}
                    camera_files = {
                        "videos/" + c["camera_id"] + ".mp4": c["camera_id"]
                        for c in config["cameras"]
                    }
                    for path in sorted(output.rglob("*")):
                        if not path.is_file():
                            continue
                        relative = path.relative_to(output).as_posix()
                        sha, crc = hashlib.sha256(), 0
                        with path.open("rb") as stream:
                            for block in iter(lambda: stream.read(1024**2), b""):
                                sha.update(block)
                                crc = crc64_ecma(block, crc)
                                checkpoint()
                        digest, size = sha.hexdigest(), path.stat().st_size
                        key = prefix + "/" + digest + "/" + relative
                        head = publish(handler.storage, key, path, size, digest, checkpoint)
                        role = (
                            "RAW_VIDEO"
                            if relative in camera_files
                            else "SENSOR_DATA"
                            if relative == "sensors/robot.mcap"
                            else "RECORDING_CONFIG"
                            if relative == "recording-config.json"
                            else "AUXILIARY"
                        )
                        declarations.append(
                            dict(
                                path=relative,
                                role=role,
                                camera_id=camera_files.get(relative),
                                media_type="video/mp4"
                                if role == "RAW_VIDEO"
                                else "application/mcap"
                                if path.suffix == ".mcap"
                                else "application/octet-stream",
                                size=size,
                                sha256=digest,
                                crc64=crc,
                            )
                        )
                        locations[relative] = (key, head.etag)
                    declarations.append(
                        dict(
                            path="source/original/source.mcap",
                            role="AUXILIARY",
                            media_type="application/mcap",
                            size=original["size"],
                            sha256=original["sha256"],
                        )
                    )
                    locations["source/original/source.mcap"] = (
                        original["object_key"],
                        handler.storage.head(original["object_key"]).etag,
                    )
                    started = datetime.fromtimestamp(
                        provenance["source_origin_ns"] // 10**9, timezone.utc
                    ) + timedelta(microseconds=(provenance["source_origin_ns"] % 10**9) // 1000)
                    command = CreateRecordingUploadCommand(
                        recording_id="mcap-" + ident[:40],
                        rollout_id="mcap-capture-" + ident[:40],
                        data_package_id="mcap-package-" + ident[:40],
                        collection_task_id=task,
                        collection_job_id="mcap-job-" + ident[:40],
                        robot_id=robot,
                        device_id=robot,
                        capture_started_at=started,
                        capture_ended_at=started
                        + timedelta(microseconds=provenance["duration_ns"] // 1000),
                        recording_config=config,
                        assets=declarations,
                    )
                    now = utc_now()
                    recorded = RecordingUpload(
                        scope=RecordingScope(
                            organization_id=scope[0], project_id=scope[1], region_code=scope[2]
                        ),
                        upload_id=uid,
                        command=command,
                        manifest_sha256=canonical_hash(command.model_dump(mode="json")),
                        status="COMMITTED",
                        created_by="mcap-worker",
                        created_at=now,
                        updated_at=now,
                    )
                    assets = tuple(
                        RecordingAsset(
                            scope=recorded.scope,
                            upload_id=uid,
                            asset_id=uuid5(uid, a.path),
                            manifest=a,
                            object_key=locations[a.path][0],
                            multipart_upload_id="platform-derived",
                            status="COMMITTED",
                            object_etag=locations[a.path][1],
                            committed_at=now,
                        )
                        for a in command.assets
                    )
                    handler.storage.put_json(
                        receipt_key,
                        {
                            "upload": recorded.model_dump(mode="json"),
                            "assets": [a.model_dump(mode="json") for a in assets],
                        },
                        if_none_match=True,
                    )
            from hc_data_platform.continuous_recordings.previews import prepare_recording_previews

            prepare_recording_previews(handler.storage, recorded, assets, checkpoint)
            with handler.connections() as db, db.cursor() as cursor:
                lock_source(cursor, scope, event.aggregate_id)
                handler.registrations.register(cursor, VerifiedRecordingFacts(recorded, assets))
                cursor.execute(
                    """UPDATE ingest.mcap_processing_files
                    SET status='READY',recording_id=%s,error=NULL,updated_at=now()

                    WHERE organization_id=%s AND project_id=%s
                    AND region_code=%s
                    AND processing_id=%s""",
                    (recorded.command.recording_id, *scope, ident),
                )
                sync_source(cursor, scope, event.aggregate_id)
                db.commit()
        except Exception as exc:
            with handler.connections() as db:
                lock_source(db, scope, event.aggregate_id)
                db.execute(
                    """UPDATE ingest.mcap_processing_files
                    SET status='FAILED',error=%s,updated_at=now()

                    WHERE organization_id=%s AND project_id=%s
                    AND region_code=%s
                    AND processing_id=%s AND status<>'READY'""",
                    (str(exc)[:2000], *scope, ident),
                )
                sync_source(db, scope, event.aggregate_id)
                db.commit()
            raise
        finally:
            lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", ("mcap:" + ident,))


def mark_failed(connection_factory, event):
    """Settle terminal workflow failures without altering immutable Raw storage status."""
    with connection_factory() as db:
        lock_source(
            db, (event.organization_id, event.project_id, event.region_code), event.aggregate_id
        )
        changed = db.execute(
            """UPDATE ingest.mcap_processing_files
            SET status='FAILED',error=COALESCE(error,'MCAP_PREPARATION_FAILED'),updated_at=now()
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
              AND raw_source_id=%s AND processing_id=%s AND status<>'READY'
              AND (%s::int IS NULL OR attempts=%s)""",
            (
                event.organization_id,
                event.project_id,
                event.region_code,
                event.aggregate_id,
                event.payload["mcap_processing_id"],
                event.payload.get("attempt"),
                event.payload.get("attempt"),
            ),
        )
        if changed.rowcount:
            sync_source(
                db,
                (event.organization_id, event.project_id, event.region_code),
                event.aggregate_id,
            )
        db.commit()


def retry_source(scope, raw_id, factory):
    """Resume an already configured browser source through the same preparation queue."""
    with factory() as db:
        lock_source(db, scope, raw_id)
        rows = db.execute(
            """SELECT DISTINCT ON (source_path) source_document,collection_task_id,
            robot_id,dataset_id,status,configuration FROM ingest.mcap_processing_files
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
            ORDER BY source_path,created_at DESC,processing_id""",
            (*scope, raw_id),
        ).fetchall()
        if not rows:
            return False
        for document, task, robot, dataset, status, previous_config in rows:
            if status != "FAILED":
                continue
            profile_row = db.execute(
                """SELECT configuration FROM ingest.mcap_robot_profiles
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND robot_id=%s""",
                (*scope, robot),
            ).fetchone()
            # Keep the previous snapshot when the profile is absent (legacy browser imports).
            cfg = profile_row[0] if profile_row else previous_config
            enqueue_files(
                db,
                scope,
                SimpleNamespace(raw_source_id=raw_id, dataset_id=dataset),
                {"files": [document]},
                SimpleNamespace(collection_task_id=task, robot_id=robot),
                McapProcessingConfig.model_validate(cfg),
            )
        db.commit()
    return True


def set_source_status(db, scope, raw_id, status, error=None, *, event_id=None, attempts=None):
    """Project preparation state without changing Raw receipt or asserting Episode QC."""
    db.execute(
        """UPDATE ingest.raw_ingest_jobs
        SET status=%s,last_error_code=%s,updated_at=clock_timestamp(),
            workflow_id=COALESCE(%s,workflow_id),attempts=COALESCE(%s,attempts)
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
        (
            status,
            error,
            "robot-recording-" + event_id if event_id else None,
            attempts,
            *scope,
            raw_id,
        ),
    )
    processing = {"SUCCEEDED": "READY", "RUNNING": "PROCESSING"}.get(status, status)
    db.execute(
        """UPDATE ingest.raw_sources SET processing_status=%s,updated_at=clock_timestamp()
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
        (processing, *scope, raw_id),
    )
    db.execute(
        """UPDATE ingest.robot_ingest_uploads
        SET upload_document=jsonb_set(upload_document,'{processing_status}',to_jsonb(%s::text)),
            updated_at=clock_timestamp()
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
        (processing, *scope, raw_id),
    )


def sync_source(db, scope, raw_id):
    # Serialize aggregate snapshots after each file transition. A changed configuration
    # supersedes a previous attempt for the same source path, not other source files.
    db.execute(
        """SELECT 1 FROM ingest.raw_ingest_jobs
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
        FOR UPDATE""",
        (*scope, raw_id),
    )
    rows = db.execute(
        """SELECT DISTINCT ON (source_path) status,error,attempts,processing_id
        FROM ingest.mcap_processing_files
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
        ORDER BY source_path,created_at DESC,processing_id""",
        (*scope, raw_id),
    ).fetchall()
    if not rows:
        return
    statuses = {r[0] for r in rows}
    status = (
        "RUNNING"
        if "RUNNING" in statuses
        else "PENDING"
        if "PENDING" in statuses
        else "FAILED"
        if "FAILED" in statuses
        else "SUCCEEDED"
    )
    error = next((r[1] for r in rows if r[0] == "FAILED" and r[1]), None)
    code = error.split(":", 1)[0] if error else None
    if code and not re.fullmatch(r"MCAP_[A-Z0-9_]+", code):
        code = "MCAP_PREPARATION_FAILED"
    representative = next(r for r in rows if r[0] == ("READY" if status == "SUCCEEDED" else status))
    event_id = str(uuid5(NAMESPACE_URL, f"mcap:{representative[3]}:{representative[2]}"))
    if status == "FAILED" and not code:
        code = "MCAP_PREPARATION_FAILED"
    set_source_status(
        db,
        scope,
        raw_id,
        status,
        code if status == "FAILED" else None,
        event_id=event_id,
        attempts=max(r[2] for r in rows),
    )


def lock_source(db, scope, raw_id):
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("mcap-source:" + json.dumps([*scope, raw_id]),),
    )

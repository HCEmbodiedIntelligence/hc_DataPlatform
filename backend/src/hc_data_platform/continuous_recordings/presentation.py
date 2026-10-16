"""Readable labels for recording responses; never change stored capture identities."""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import unquote

from .models import ContinuousRecording

_DISPLAY_TIMEZONE = timezone(timedelta(hours=8))
_MACHINE_ID = re.compile(r"[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
_GENERIC_NAMES = {"raw", "robot", "recording", "source", "data"}


def decoded_filename(path: str | None) -> str | None:
    if not path:
        return None
    name = unquote(path).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(
        c for c in unicodedata.normalize("NFC", name) if not unicodedata.category(c).startswith("C")
    )
    return name.strip()[:255] or None


def recording_filename(
    recording: ContinuousRecording, task_code: str | None, source: str | None
) -> str:
    name = decoded_filename(source)
    stem = PurePosixPath(name).stem if name else ""
    if (
        name
        and stem.lower() not in _GENERIC_NAMES
        and not stem.isdecimal()
        and not _MACHINE_ID.search(stem)
    ):
        return name
    task = f"T{int(task_code)}" if task_code and task_code.isdecimal() else "recording"
    stamp = recording.capture_started_at.astimezone(_DISPLAY_TIMEZONE)
    suffix = PurePosixPath(name).suffix.lower() if name else ""
    return f"{task}_{stamp:%Y%m%d_%H%M%S}_{stamp.microsecond // 1000:03d}{suffix}"


def present_recordings(connection_factory, items: tuple[ContinuousRecording, ...]):
    if not items:
        return items
    scope = items[0].scope
    if any(item.scope != scope for item in items):
        raise ValueError("recording labels must be read within one scope")
    connection = connection_factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT r.recording_id,t.task_code,robot.display_name,
                          COALESCE(m.source_path,source.asset_path)
                   FROM ingest.continuous_recordings r
                   LEFT JOIN collection_tasks.collection_tasks t
                     ON t.organization_id=r.organization_id AND t.project_id=r.project_id
                    AND t.collection_task_id=r.collection_task_id
                   LEFT JOIN robotics.robot_assets robot
                     ON robot.organization_id=r.organization_id AND robot.robot_id=r.robot_id
                   LEFT JOIN LATERAL (
                       SELECT source_path FROM ingest.mcap_processing_files m
                       WHERE m.organization_id=r.organization_id AND m.project_id=r.project_id
                         AND m.region_code=r.region_code AND m.recording_id=r.recording_id
                       ORDER BY m.created_at DESC,m.processing_id DESC LIMIT 1
                   ) m ON true
                   LEFT JOIN LATERAL (
                       SELECT asset_path FROM ingest.recording_upload_assets a
                       WHERE a.organization_id=r.organization_id AND a.project_id=r.project_id
                         AND a.region_code=r.region_code AND a.upload_id=r.recording_upload_id
                         AND lower(a.asset_path) LIKE '%%.mcap'
                       ORDER BY (a.asset_path LIKE 'source/original/%%') DESC,a.asset_path
                       LIMIT 1
                   ) source ON true
                   WHERE r.organization_id=%s AND r.project_id=%s AND r.region_code=%s
                     AND r.recording_id=ANY(%s)""",
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    [r.recording_id for r in items],
                ),
            )
            labels = {row[0]: row[1:] for row in cursor.fetchall()}
    finally:
        connection.close()
    return tuple(
        _present(item, *labels.get(item.recording_id, (None, None, None))) for item in items
    )


def _present(recording, task_code, robot_name, source):
    return recording.model_copy(
        update={
            "file_name": recording_filename(recording, task_code, source),
            "source_file_name": decoded_filename(source),
            "robot_name": robot_name,
            "task_code": task_code,
        }
    )

"""Readable download names, resolved from the selected versions' frozen source identities."""

import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .scope import assert_scope

LOCAL_TIME = ZoneInfo("Asia/Shanghai")


def _component(value, limit):
    value = unicodedata.normalize("NFC", str(value or "")).strip()
    value = "".join("_" if unicodedata.category(c).startswith("C") else c for c in value)
    value = re.sub(r'[<>:"/\\|?*]', "_", value)
    value = re.sub(r"\s+", "_", value).strip(" .") or "unknown"
    if len(value.encode("utf-8")) > limit:
        value = value.encode("utf-8")[: limit - 3].decode("utf-8", errors="ignore") + "…"
    return value


def _summary(values, limit):
    # Keys are source identities: distinct robots/tasks may share a display name.
    labels = [values[key] for key in sorted(values)]
    suffix = "" if len(labels) == 1 else f"+{len(labels) - 1}"
    return _component(labels[0], limit - len(suffix.encode("utf-8"))) + suffix


def archive_name(sources, exported_at, variant):
    """Keep the whole UTF-8 filename below 240 bytes, including multi-task exports."""
    robots, tasks, codes, created = {}, {}, {}, set()
    for source in sources:
        robot_id, task_id = source["robot_id"], source["task_id"]
        robots[robot_id] = source["robot_name"] or robot_id
        tasks[task_id] = source["task_name"] or "unknown"
        codes[task_id] = (source["task_code"] or task_id).strip()
        value = source["task_created_at"]
        created.add(value.astimezone(LOCAL_TIME).strftime("%Y%m%d-%H%M%S") if value else "unknown")
    if not robots:
        raise ValueError("download naming requires at least one source")
    dates = sorted(created)
    creation = dates[0] if len(dates) == 1 else f"{dates[0]}--{dates[-1]}"
    if "unknown" in created:
        creation = "unknown"
    parts = (
        _component(variant, 24),
        _summary(robots, 48),
        _summary(tasks, 64),
        _summary(codes, 26),
        creation,
        exported_at.astimezone(LOCAL_TIME).strftime("%Y%m%d-%H%M%S"),
    )
    return "_".join(parts) + ".zip"


def episode_download_name(repository, data, *, exported_at=None, variant="LeRobot"):
    """Use scoped business labels without changing immutable archive/cache identities."""
    data = tuple(data)
    if not data:
        raise ValueError("download naming requires at least one data version")
    for ref in data:
        assert_scope(ref.identity)
    with repository.transaction() as cursor:
        cursor.execute(
            """SELECT DISTINCT
                COALESCE(v.input_document->>'robot_id', 'unknown') AS robot_id,
                COALESCE(r.display_name, h.snapshot->>'display_name') AS robot_name,
                COALESCE(v.input_document->>'task_id', 'unknown') AS task_id,
                t.name AS task_name, t.task_code, t.created_at AS task_created_at
            FROM episode_store.versions v
            LEFT JOIN collection_tasks.collection_tasks t
              ON t.organization_id=v.organization_id AND t.project_id=v.project_id
              AND t.collection_task_id=v.input_document->>'task_id'
            LEFT JOIN robotics.robot_assets r
              ON r.organization_id=v.organization_id AND r.robot_id=v.input_document->>'robot_id'
            LEFT JOIN robotics.robot_history_anchors h
              ON h.organization_id=v.organization_id AND h.robot_id=v.input_document->>'robot_id'
            WHERE v.organization_id=%s AND v.project_id=%s AND v.region_code=%s
              AND v.data_version_id=ANY(%s) AND v.status='COMMITTED'""",
            (*data[0].identity.scope, [ref.data_version_id for ref in data]),
        )
        columns = [column.name for column in cursor.description]
        sources = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    return archive_name(sources, exported_at or datetime.now(timezone.utc), variant)

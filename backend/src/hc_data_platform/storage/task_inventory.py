"""Resolve scoped business labels and ownership for every inventoried file."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from hc_data_platform.core.context import current_request_context
from hc_data_platform.episode_store.models import digest

from .inventory import InventoryCatalogEntry
from .models import BusinessCapacityCategory, ObjectRole
from .task_usage_models import UsageCategory


@dataclass(frozen=True)
class FileOwner:
    task_ids: tuple[str, ...] = ()
    category: UsageCategory = "OTHER"
    record_name: str = "历史文件"
    robot_name: str = "—"
    status: str = "已保存"
    display_name: str | None = None


def _stamp(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M:%S")


class TaskInventoryLabels:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]):
        self.rows = rows
        self.tasks = {r["collection_task_id"]: r for r in rows["tasks"]}
        self.robots = {r["robot_id"]: r["display_name"] for r in rows["robots"]}
        self.by_dataset: dict[str, set[str]] = defaultdict(set)
        for task_id, row in self.tasks.items():
            if row.get("dataset_id"):
                self.by_dataset[row["dataset_id"]].add(task_id)
        self.owners: dict[str, FileOwner] = {}
        self.extra_entries: list[InventoryCatalogEntry] = []
        self.raw_counts: dict[str, int] = defaultdict(int)
        self.episodes: dict[str, set[str]] = defaultdict(set)
        self.completed: dict[str, set[str]] = defaultdict(set)
        self._build()

    def task_ids(self, row: dict[str, Any]) -> tuple[str, ...]:
        task_id = row.get("collection_task_id") or row.get("task_id")
        if task_id in self.tasks:
            return (task_id,)
        return tuple(sorted(self.by_dataset.get(row.get("dataset_id", ""), ())))

    def _extra(self, identity: str, prefix: str, owner: FileOwner) -> None:
        self.owners[identity] = owner
        self.extra_entries.append(
            InventoryCatalogEntry(
                identity=identity,
                object_key=prefix.rstrip("/") + "/",
                business_category=BusinessCapacityCategory.PENDING_ANNOTATION,
                object_role=ObjectRole.REBUILDABLE_DERIVATIVE,
                observed_at=datetime.now(timezone.utc),
                prefix=True,
                required=False,
                priority=20,
            )
        )

    def _build(self) -> None:
        context = current_request_context()
        org, project, region = context.organization_id, context.project_id, context.region_code
        for task_id in self.tasks:
            self._extra(
                f"task-upload:{task_id}",
                f"raw/{org}/{project}/{task_id}/",
                FileOwner((task_id,), "RAW", "上传文件", status="原始文件"),
            )
        raw_owners = {}
        for raw in self.rows["raw"]:
            owner = FileOwner(
                self.task_ids(raw),
                "RAW",
                f"{raw['source_format']} 原始录制 · {_stamp(raw['committed_at'])}",
                self.robots.get(raw.get("robot_id"), "未登记机器人"),
                "原始文件",
            )
            raw_owners[raw["raw_source_id"]] = owner
            self.owners[f"raw-source:{raw['raw_source_id']}"] = owner
            self.owners[f"raw-manifest:{raw['raw_source_id']}"] = owner
            for task in owner.task_ids:
                self.raw_counts[task] += 1
            self._extra(
                f"derived-recording:{raw['raw_source_id']}",
                f"derived-recordings/{org}/{project}/{region}/{raw['raw_source_id']}/",
                FileOwner(
                    owner.task_ids, "PROCESSING", owner.record_name, owner.robot_name, "处理文件"
                ),
            )
        recordings = {r.get("recording_upload_id"): r for r in self.rows["recordings"]}
        from hc_data_platform.continuous_recordings.asset_models import (
            RecordingAsset,
            RecordingUpload,
        )
        from hc_data_platform.continuous_recordings.previews import preview_key

        assets_by_upload = defaultdict(list)
        for asset in self.rows["assets"]:
            assets_by_upload[asset["upload_id"]].append(asset)
        for row in self.rows["uploads"]:
            recording = recordings.get(row["upload_id"], {})
            upload = RecordingUpload.model_validate(row["upload_document"])
            assets = [
                RecordingAsset.model_validate(a["asset_document"])
                for a in assets_by_upload[row["upload_id"]]
            ]
            owner = FileOwner(
                self.task_ids(recording),
                "PROCESSING",
                f"录制 · {_stamp(recording.get('capture_started_at'))}",
                self.robots.get(recording.get("robot_id"), "未登记机器人"),
                "预览文件",
            )
            for asset in assets:
                if asset.manifest.role != "RAW_VIDEO":
                    continue
                for selected in (True, False):
                    key = preview_key(upload, asset, assets, selected=selected)
                    self._extra(f"recording-preview:{key}", key.rsplit("/", 1)[0], owner)
        for asset in self.rows["assets"]:
            recording = recordings.get(asset["upload_id"], {})
            derived = asset.get("multipart_upload_id") == "platform-derived"
            self.owners[f"recording-asset:{asset['asset_id']}"] = FileOwner(
                self.task_ids(recording),
                "PROCESSING" if derived else "RAW",
                f"录制 · {_stamp(recording.get('capture_started_at', asset['committed_at']))}",
                self.robots.get(recording.get("robot_id"), "未登记机器人"),
                "处理文件" if derived else "原始文件",
            )
        for processing in self.rows["processing"]:
            owner = raw_owners.get(processing["raw_source_id"], FileOwner())
            self._extra(
                f"mcap-processing:{processing['processing_id']}",
                f"derived-mcap/{org}/{project}/{region}/{processing['processing_id']}/",
                FileOwner(
                    owner.task_ids, "PROCESSING", owner.record_name, owner.robot_name, "处理文件"
                ),
            )
        bindings = {r["current_data_version_id"] for r in self.rows["bindings"]}
        approved = {
            r["data_ref"]["data_version_id"]
            for r in self.rows["annotations"]
            if r.get("data_ref") and r["status"] == "APPROVED" and r.get("task_kind") == "TAGGING"
        }
        versions = {}
        for index, version in enumerate(self.rows["versions"], 1):
            source = version["input_document"]
            identity = source["identity"]
            task_ids = self.task_ids({**identity, "task_id": source.get("task_id")})
            current = version["data_version_id"] in bindings
            complete = version["data_version_id"] in approved
            name = f"成片 {index:03d} · 版本 {version['version']}"
            owner = FileOwner(
                task_ids,
                "EPISODE",
                name,
                self.robots.get(source.get("robot_id"), "未登记机器人"),
                "历史版本" if not current else ("已标注" if complete else "待标注"),
            )
            versions[version["data_version_id"]] = owner
            self.owners[f"episode:{version['data_version_id']}"] = owner
            for task in task_ids:
                if current:
                    self.episodes[task].add(version["data_version_id"])
                    if complete:
                        self.completed[task].add(version["data_version_id"])
            key = tuple(
                identity[k]
                for k in (
                    "organization_id",
                    "project_id",
                    "region_code",
                    "dataset_id",
                    "episode_id",
                )
            )
            self._extra(
                f"episode-preview-cache:{version['data_version_id']}",
                f"episode-previews/{digest(key)}/{version['data_version_id']}/",
                FileOwner(task_ids, "PROCESSING", name, owner.robot_name, "预览文件"),
            )
        for preview in self.rows["previews"]:
            owner = versions.get(preview["data_version_id"], FileOwner())
            identity = (
                f"episode-preview:{preview['data_version_id']}:"
                f"{preview['feature']}:{preview['profile_id']}"
            )
            self.owners[identity] = FileOwner(
                owner.task_ids,
                "PROCESSING",
                owner.record_name,
                owner.robot_name,
                "预览文件",
            )
        for collection in self.rows["collections"]:
            self.owners[
                f"collection:{collection['dataset_id']}:{collection['collection_version_id']}"
            ] = FileOwner(
                self.task_ids(collection),
                "EPISODE",
                f"数据集目录 · 版本 {collection.get('version') or '—'}",
                status="入库目录",
            )
        annotations = {r["task_id"]: r for r in self.rows["annotations"]}
        for file in self.rows["annotation_files"]:
            task = annotations.get(file["task_id"], {})
            owner = versions.get((task.get("data_ref") or {}).get("data_version_id"), FileOwner())
            self.owners[
                f"annotation-file:{file['task_id']}:{file['revision']}:{file['format_version']}"
            ] = FileOwner(
                owner.task_ids or self.task_ids(task),
                "ANNOTATION",
                owner.record_name,
                owner.robot_name,
                "标注文件",
                f"标注结果 · 第 {file['revision']} 版.json",
            )
        for table, prefix in (
            ("exports", "episode-delivery"),
            ("annotation_exports", "annotation-delivery"),
        ):
            for job in self.rows[table]:
                plan = job["plan"]
                data = [m["data"] for m in plan.get("members", ())]
                if plan.get("binding"):
                    data.append(plan["binding"]["data"])
                owners = [versions.get(d["data_version_id"], FileOwner()) for d in data]
                tasks = tuple(sorted({t for o in owners for t in o.task_ids}))
                archive = plan.get("archive_format", "annotation_zip")
                label = (
                    "HoloBrain"
                    if "holobrain" in archive
                    else ("原始数据" if "original" in archive else "LeRobot")
                )
                modality = (
                    "RGB-D"
                    if plan.get("modalities") == "rgbd"
                    else "RGB"
                    if plan.get("modalities") == "rgb"
                    else ""
                )
                name = f"{label} {modality} · {len(data)} 条成片 · {_stamp(job['created_at'])}.zip"
                self.owners[f"{prefix}:{job['job_id']}"] = FileOwner(
                    tasks,
                    "EXPORT",
                    "导出包",
                    "、".join(sorted({o.robot_name for o in owners})) or "—",
                    "已生成",
                    name,
                )


def load_task_inventory(connection_factory, project_id: str) -> TaskInventoryLabels:
    queries = {
        "tasks": "SELECT to_jsonb(t) FROM collection_tasks.collection_tasks t "
        "WHERE project_id=%s ORDER BY task_code",
        "raw": "SELECT to_jsonb(t) FROM ingest.raw_sources t "
        "WHERE project_id=%s AND raw_status='COMMITTED' ORDER BY committed_at,raw_source_id",
        "recordings": "SELECT to_jsonb(t) FROM ingest.continuous_recordings t WHERE project_id=%s",
        "uploads": "SELECT to_jsonb(t) FROM ingest.recording_uploads t "
        "WHERE project_id=%s AND status='COMMITTED'",
        "assets": "SELECT to_jsonb(t) FROM ingest.recording_upload_assets t "
        "WHERE project_id=%s AND status='COMMITTED'",
        "processing": "SELECT to_jsonb(t) FROM ingest.mcap_processing_files t WHERE project_id=%s",
        "versions": "SELECT to_jsonb(t) FROM episode_store.versions t "
        "WHERE project_id=%s AND status='COMMITTED' ORDER BY dataset_id,episode_id,version",
        "bindings": "SELECT to_jsonb(t) FROM episode_store.bindings t WHERE project_id=%s",
        "previews": "SELECT to_jsonb(t) FROM episode_store.media_previews t "
        "WHERE project_id=%s AND status='READY'",
        "collections": "SELECT to_jsonb(t) FROM episode_store.collections t WHERE project_id=%s",
        "annotations": "SELECT to_jsonb(t) FROM annotation.annotation_tasks t WHERE project_id=%s",
        "annotation_files": "SELECT to_jsonb(t) FROM annotation.revision_files t "
        "WHERE project_id=%s AND status='READY'",
        "exports": "SELECT to_jsonb(t) FROM episode_store.delivery_jobs t "
        "WHERE project_id=%s AND status='READY'",
        "annotation_exports": "SELECT to_jsonb(t) FROM annotation.delivery_jobs t "
        "WHERE project_id=%s AND status='READY'",
    }
    connection = connection_factory()
    try:
        cursor = connection.cursor()
        rows = {}
        for name, query in queries.items():
            cursor.execute(query, (project_id,))
            rows[name] = [r[0] for r in cursor.fetchall()]
        cursor.execute(
            "SELECT to_jsonb(r) FROM robotics.robot_assets r WHERE organization_id=%s",
            (current_request_context().organization_id,),
        )
        rows["robots"] = [r[0] for r in cursor.fetchall()]
        labels = TaskInventoryLabels(rows)
        cursor.execute("SELECT pg_database_size(current_database())")
        labels.database_bytes = cursor.fetchone()[0]
        return labels
    finally:
        connection.close()

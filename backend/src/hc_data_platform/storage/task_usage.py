"""Read-only provider accounting with task ownership and a short scoped cache."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timezone
from pathlib import PurePosixPath
from threading import Lock

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem

from .inventory import PostgresStorageInventoryCatalog, StorageInventoryProvider
from .location_usage import storage_locations
from .task_inventory import FileOwner, load_task_inventory
from .task_usage_models import (
    USAGE_CATEGORIES,
    ProjectTaskStorage,
    StorageDiskUsage,
    TaskStorageCategory,
    TaskStorageFile,
    TaskStorageFilePage,
    TaskStorageSummary,
    UsageCategory,
)


def category_totals(files):
    sizes = defaultdict(int)
    counts = defaultdict(int)
    for file in files:
        sizes[file.category] += int(file.bytes)
        counts[file.category] += 1
    return tuple(
        TaskStorageCategory(category=category, bytes=str(sizes[category]), file_count=counts[category])
        for category in USAGE_CATEGORIES
    )


def local_disk_usage(disk_path, local_roots):
    if not disk_path:
        return None, {}
    labels = {
        "workspace": "平台工作盘",
        "system": "系统盘（/）",
        "alignment": "对齐缓存盘",
        "media": "媒体暂存盘",
        "models": "模型数据盘",
        "oss_cache": "预览与导出缓存盘",
        "minio": "本地对象存储盘",
        "legacy_alignment": "历史对齐缓存盘",
        "legacy_media": "历史媒体暂存盘",
    }
    root_device = os.stat("/").st_dev
    disks = {}
    devices = set()
    primary = None
    paths = [("workspace", disk_path), ("system", "/"), *local_roots.items()]
    for name, path in paths:
        if name not in labels:
            continue
        try:
            device = os.stat(path).st_dev
            if device in devices:
                continue
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        measurement = StorageDiskUsage(
            total_bytes=str(usage.total),
            used_bytes=str(usage.used),
            available_bytes=str(usage.free),
            reserved_bytes=str(max(0, usage.total - usage.used - usage.free)),
        )
        devices.add(device)
        disks[labels["system" if device == root_device else name]] = measurement
        if name == "workspace":
            primary = measurement
    return primary, disks


class TaskStorageUsageReader:
    def __init__(
        self,
        connections,
        catalog: PostgresStorageInventoryCatalog,
        provider: StorageInventoryProvider,
        *,
        disk_path: str | None = None,
        bucket_usage_provider=None,
        inventory_snapshot=None,
        report_root=None,
        report_namespace=None,
        object_store_provider="s3",
        object_scope="",
        local_usage_roots=None,
        extra_location_provider=None,
        clock=time.monotonic,
    ):
        self.connections, self.catalog, self.provider = connections, catalog, provider
        self.clock = clock
        self.disk_path = disk_path
        self.bucket_usage_provider = bucket_usage_provider
        self.object_store_provider = object_store_provider
        self.object_scope = object_scope
        self.extra_location_provider = extra_location_provider
        self.local_usage_roots = local_usage_roots or {}
        self.inventory_snapshot = inventory_snapshot
        self.reports = None
        if report_root:
            from .usage_reports import UsageReports
            self.reports = UsageReports(report_root, report_namespace, self._scan)
        self.cache = {}
        self.lock = Lock()

    def load(self, project_id: str, *, refresh=False):
        scope = current_request_context()
        key = (scope.organization_id, project_id, scope.region_code)
        with self.lock:
            cached = self.cache.get(key)
            if not refresh and cached and self.clock() - cached[0] < 60:
                return cached[1]
            value = self._scan(project_id)
            if len(self.cache) >= 16:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (self.clock(), value)
            return value

    def _scan(self, project_id):
        provider, bucket_objects = self.provider, None
        with ThreadPoolExecutor(max_workers=1) as pool:
            snapshot = pool.submit(copy_context().run, self.inventory_snapshot) if self.inventory_snapshot else None
            labels = load_task_inventory(self.connections, project_id)
            entries = self.catalog.entries(project_id=project_id)
            if snapshot is not None:
                provider, bucket_objects = snapshot.result()
        entries = (*entries, *labels.extra_entries)
        selected = {}
        missing = Counter()
        unknown_owner = FileOwner()
        for entry in entries:
            lookup = self.provider if "://" in entry.object_key else provider
            objects = (
                lookup.list_prefix(entry.object_key)
                if entry.prefix
                else (tuple(filter(None, (lookup.head(entry.object_key),))))
            )
            owner = labels.owners.get(entry.identity, unknown_owner)
            if not objects and entry.required:
                kind = {"episode": "成片目录", "episode-preview": "成片预览", "annotation-file": "标注文件",
                        "episode-delivery": "导出包"}.get(entry.identity.split(":")[0], "登记文件或目录")
                task = "、".join(labels.tasks[t]["task_code"].strip().lstrip("0") or "0" for t in owner.task_ids)
                missing[(task, kind)] += 1
            for obj in objects:
                previous = selected.get(obj.object_key)
                if previous is None:
                    selected[obj.object_key] = (entry, obj, owner, owner.task_ids)
                    continue
                prev_entry, prev_obj, prev_owner, task_ids = previous
                if owner.task_ids and owner.task_ids != task_ids:
                    task_ids = tuple(sorted(set(task_ids).union(owner.task_ids)))
                if entry.priority > prev_entry.priority:
                    selected[obj.object_key] = (entry, obj, owner, task_ids)
                elif task_ids != previous[3]:
                    selected[obj.object_key] = (prev_entry, prev_obj, prev_owner, task_ids)
        grouped, related = defaultdict(list), defaultdict(list)
        for key, (entry, obj, owner, task_ids) in selected.items():
            group = (
                "shared"
                if len(task_ids) > 1
                else (f"task:{task_ids[0]}" if task_ids else "unassigned")
            )
            basename = key.rpartition("/")[2]
            if basename in ("", "."):
                basename = PurePosixPath(key).name
            relative = (
                key.removeprefix(entry.object_key) if entry.prefix else basename
            )
            file = TaskStorageFile(
                file_id=hashlib.sha256(key.encode()).hexdigest(),
                category=owner.category,
                name=owner.display_name or basename,
                relative_path=relative,
                record_name=owner.record_name,
                robot_name=owner.robot_name,
                status=owner.status,
                bytes=str(obj.physical_bytes),
                updated_at=obj.observed_at,
                shared=len(task_ids) > 1,
                related_task_names=tuple(labels.tasks[t]["name"] for t in task_ids),
            )
            grouped[group].append(file)
            for task in task_ids:
                related[f"task:{task}"].append(file)
        for group in ("unassigned", "shared"):
            related[group] = grouped[group]
        tasks = []
        groups = [
            (f"task:{task_id}", row["name"], row["task_code"].strip(), task_id)
            for task_id, row in labels.tasks.items()
        ]
        groups += [
            (group, name, None, None)
            for group, name in (
                ("shared", "跨任务共享文件"),
                ("unassigned", "未关联任务的文件"),
            )
            if grouped[group]
        ]
        for group, name, code, task_id in groups:
            files = grouped[group]
            tasks.append(
                TaskStorageSummary(
                    group_id=group,
                    task_code=code,
                    task_name=name,
                    total_bytes=str(sum(int(f.bytes) for f in files)),
                    file_count=len(files),
                    categories=category_totals(files),
                    raw_count=labels.raw_counts.get(task_id, 0),
                    episode_count=len(labels.episodes.get(task_id, ())),
                    annotation_complete_count=len(labels.completed.get(task_id, ())),
                    export_count=sum(f.category == "EXPORT" for f in files),
                    shared_bytes=str(sum(int(f.bytes) for f in related[group] if f.shared))
                    if task_id
                    else "0",
                )
            )
        tasks.sort(key=lambda t: (-int(t.total_bytes), t.task_code or "", t.task_name))
        files = [file for group in grouped.values() for file in group]
        observed = datetime.now(timezone.utc)
        if bucket_objects is None and self.bucket_usage_provider:
            bucket_objects = self.bucket_usage_provider()
        disk, disks = local_disk_usage(self.disk_path, self.local_usage_roots)
        overview = ProjectTaskStorage(
            project_id=project_id,
            observed_at=observed,
            total_bytes=str(sum(int(f.bytes) for f in files)),
            file_count=len(files),
            categories=category_totals(files),
            tasks=tuple(tasks),
            disk=disk,
            disks=disks,
            database_bytes=str(labels.database_bytes)
            if hasattr(labels, "database_bytes")
            else None,
            object_store_bytes=str(sum(o.physical_bytes for o in bucket_objects))
            if bucket_objects is not None
            else None,
            object_store_file_count=len(bucket_objects) if bucket_objects is not None else None,
            storage_locations=storage_locations(
                objects=bucket_objects,
                provider=self.object_store_provider,
                object_scope=self.object_scope,
                local_roots=self.local_usage_roots,
                database_bytes=getattr(labels, "database_bytes", None),
            )
            + (self.extra_location_provider() if self.extra_location_provider else ()),
            warnings=tuple(
                f"{'任务 ' + task if task else '项目'}有 {count} 项{kind}在登记路径未找到。"
                for (task, kind), count in sorted(missing.items())
            ),
        )
        return overview, related

    def overview(self, project_id, *, refresh=False):
        if self.reports:
            return self.reports.overview(project_id, refresh=refresh)
        return self.load(project_id, refresh=refresh)[0]

    def files(
        self,
        project_id: str,
        *,
        group_id: str,
        category: UsageCategory | None = None,
        search: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> TaskStorageFilePage:
        if self.reports:
            return self.reports.files(project_id, group_id=group_id, category=category, search=search, offset=offset, limit=limit)
        overview, grouped = self.load(project_id)
        if group_id not in {t.group_id for t in overview.tasks}:
            raise problem(
                status=404,
                code="STORAGE_TASK_NOT_FOUND",
                title="Task not found",
                detail="The selected task does not exist in this project.",
            )
        needle = search.strip().casefold()
        files = [
            f
            for f in grouped.get(group_id, ())
            if (category is None or f.category == category)
            and (
                not needle
                or needle
                in " ".join(
                    (f.name, f.relative_path, f.record_name, f.robot_name, f.status)
                ).casefold()
            )
        ]
        files.sort(key=lambda f: (-int(f.bytes), f.name, f.file_id))
        return TaskStorageFilePage(
            project_id=project_id,
            group_id=group_id,
            observed_at=overview.observed_at,
            items=tuple(files[offset : offset + limit]),
            total_count=len(files),
            total_bytes=str(sum(int(f.bytes) for f in files)),
            offset=offset,
            limit=limit,
        )

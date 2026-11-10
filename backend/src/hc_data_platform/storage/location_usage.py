"""Aggregate storage by location and purpose without exposing individual paths."""

from __future__ import annotations

import os
import stat
from collections import defaultdict
from pathlib import Path

from .task_usage_models import StorageLocationUsage, StoragePurposeUsage

# Descriptive policies, never deletion authorizations.
PURPOSES = {
    "raw": ("原始采集数据", "KEEP", "原始 MCAP、上传清单；用于追溯和重新处理，长期保留。"),
    "episodes": ("正式入库数据", "KEEP", "成片视频、传感器数据、元数据及保留版本；长期保留。"),
    "collections": ("数据集版本清单", "KEEP", "记录数据集版本与数据引用，长期保留。"),
    "derived-mcap": (
        "解析中间缓存", "CONDITIONAL",
        "选段 MCAP 与解析视频保存在本地；入库完成并通过重建核验后，按生命周期配置回收。",
    ),
    "derived-evidence": (
        "解析必要标记与凭据", "KEEP",
        "深度有效性标记、传感器、帧映射和处理配置，长期保留，用于校验与重建。",
    ),
    "recording-previews": (
        "原始录制预览",
        "CONDITIONAL",
        "供录制播放和切片使用；仍被引用时保留，过期且可重建后按策略回收。",
    ),
    "episode-previews": (
        "成片播放预览",
        "CONDITIONAL",
        "供成片页面播放使用；仍被引用时保留，过期且可重建后按策略回收。",
    ),
    "episode-deliveries": (
        "数据导出包",
        "CONDITIONAL",
        "可在数据版本和标注保留时重新生成；下载或交付仍需使用时保留。",
    ),
    "oss-smoke": (
        "接入测试副本",
        "CONDITIONAL",
        "验证正式原件完整且无引用后可清理；不是正式数据的唯一副本。",
    ),
    "annotations": (
        "标注与标注交付",
        "KEEP",
        "标注结果及历史修订长期保留；交付副本需单独核对保留要求。",
    ),
    "other": (
        "其他对象与存储元数据",
        "REVIEW",
        "尚未归类的产物、测试对象或存储服务元数据；核对用途后再决定。",
    ),
}
LOCAL_PURPOSES = {
    "alignment": (
        "当前版本处理缓存",
        "CONDITIONAL",
        "下载、解析、数据包读取和对齐暂存；按生命周期期限回收。任务锁定的缓存保留；共享缓存须所有所属项目均启用回收策略。",
    ),
    "media": (
        "当前版本媒体暂存",
        "CONDITIONAL",
        "视频生成暂存；须确认发布完成且无运行任务，由缓存策略回收。",
    ),
    "legacy_alignment": (
        "本地版处理缓存",
        "CONDITIONAL",
        "保留的本地版本处理缓存；不属于 OSS 文件，不能在本地任务运行时清理。",
    ),
    "legacy_media": (
        "本地版媒体暂存",
        "CONDITIONAL",
        "保留的本地版本媒体暂存；由本地版本缓存策略管理。",
    ),
    "models": (
        "机器人模型预览资源",
        "KEEP",
        "本地共享的模型展示资源；正在使用的资源保留。模型原始内容另计入数据库。",
    ),
    "database": (
        "当前版本数据库",
        "KEEP",
        "任务、索引、标注、配置和模型内容（含数据库索引）；长期保留并备份。",
    ),
}


def purpose(key: str) -> str:
    root = key.split("/", 1)[0]
    if root == "derived-mcap" and "/" in key:
        relative = key.split("/", 1)[1]
        return "derived-mcap" if "/source/selected.mcap/" in relative + "/" or ("/videos/" in relative and any(part.endswith(".mp4") for part in relative.split("/"))) else "derived-evidence"
    if root.startswith("annotation"):
        return "annotations"
    return root if root in PURPOSES else "other"


def row(key, size, *, local=False):
    label, retention, description = (LOCAL_PURPOSES if local else PURPOSES)[key]
    return StoragePurposeUsage(
        category=key,
        label=label,
        bytes=str(size) if size is not None else None,
        retention=retention,
        description=description,
    )


def scan_local(root, *, minio=False, cache=False, seen=None):
    """Allocated bytes including directories; ignore symlink targets, dedup links.

    MinIO layout is bucket/object/physical-files. Include storage overhead in its
    purpose. An inaccessible subtree makes this root unknown, never silently zero.
    """
    seen = set() if seen is None else seen
    totals = defaultdict(int)
    base = Path(root)
    pending = [base]
    while pending:
        path = pending.pop()
        info = path.lstat()
        identity = (info.st_dev, info.st_ino)
        if identity in seen:
            continue
        seen.add(identity)
        parts = path.relative_to(base).parts
        category = (
            purpose("/".join(parts[1:-1]) if len(parts) > 2 else parts[1])
            if minio and len(parts) > 1 and not parts[0].startswith(".")
            else "other"
        )
        if cache and parts:
            category = purpose(parts[0])
        totals[category] += getattr(info, "st_blocks", (info.st_size + 511) // 512) * 512
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as children:
                pending.extend(Path(child.path) for child in children)
    return totals


def storage_locations(*, objects, provider, object_scope, local_roots, database_bytes):
    local_rows = []
    seen = set()
    for name, path in local_roots.items():
        if name not in (*LOCAL_PURPOSES, "minio", "oss_cache") or name == "database":
            continue
        try:
            sizes = scan_local(path, minio=name == "minio", cache=name == "oss_cache", seen=seen)
        except OSError:
            if name in {"minio", "oss_cache"}:
                local_rows.append(
                    StoragePurposeUsage(
                        category=name,
                        label="本地对象存储" if name == "minio" else "本地预览与导出缓存",
                        bytes=None,
                        retention="KEEP",
                        description="读取未完成，容量未知；本地版数据仍需保留。",
                    )
                )
            else:
                local_rows.append(row(name, None, local=True))
            continue
        if name in {"minio", "oss_cache"}:
            for category in PURPOSES:
                if category not in sizes:
                    continue
                item = row(category, sizes[category])
                local_rows.append(
                    item.model_copy(
                        update={
                            "category": "minio-" + category,
                            "label": "本地对象 · " + item.label,
                        }
                    )
                )
        else:
            local_rows.append(row(name, sum(sizes.values()), local=True))
    merged = {}
    for item in local_rows:
        previous = merged.get(item.category)
        if previous is not None and previous.bytes is not None and item.bytes is not None:
            item = item.model_copy(update={"bytes": str(int(previous.bytes) + int(item.bytes))})
        merged[item.category] = item
    local_rows = list(merged.values())
    if "oss_cache" in local_roots:
        for index, item in enumerate(local_rows):
            if item.category in {
                "minio-recording-previews",
                "minio-episode-previews",
                "minio-episode-deliveries",
            }:
                local_rows[index] = item.model_copy(
                    update={
                        "description": "OSS 版本的预览与导出缓存保存在本地；保留时间在生命周期页面配置，"
                        "过期后再次访问会重新生成。"
                        "本地版和 OSS 版分别读取各自的生命周期策略。"
                    }
                )
    if database_bytes is not None:
        local_rows.append(row("database", database_bytes, local=True))
    locations = [
        StorageLocationUsage(
            location="LOCAL",
            label="本地服务器",
            scope="已接入统计的本地平台数据目录与当前版本数据库",
            total_bytes=str(sum(int(r.bytes) for r in local_rows if r.bytes is not None)),
            complete=bool(local_roots) and all(r.bytes is not None for r in local_rows),
            categories=tuple(local_rows),
            note="目录按实际占盘空间统计；数据库按数据库大小统计，已含模型原始内容，避免重复相加。整盘已用还包含系统、容器、日志及其他版本数据库等；本表不是整盘文件清单。",
        )
    ]
    if provider == "oss":
        totals = defaultdict(int)
        for obj in objects or ():
            totals[purpose(obj.object_key)] += obj.physical_bytes
        cloud_rows = tuple(row(key, totals[key]) for key in PURPOSES if key in totals)
        locations.append(
            StorageLocationUsage(
                location="OSS",
                label="阿里云 OSS",
                scope=object_scope,
                total_bytes=str(sum(totals.values())),
                complete=objects is not None,
                categories=cloud_rows,
                note="仅统计当前配置目录内的现存对象，包含测试副本；不代表整个 Bucket。"
                "按对象大小计量，不含历史版本和未完成的分片。"
                "OSS 不占用本地磁盘，无本地磁盘式的剩余容量。",
            )
        )
    return tuple(locations)


def saved_oss_location(settings, client_factory):
    """Read the explicitly scoped saved OSS configuration without switching storage."""
    from hc_data_platform.platform_ops.object_store_config import (
        PostgresObjectStoreConfigurationRepository,
    )

    from .inventory import S3StorageInventoryProvider

    scope = "平台设置中保存的 OSS 目录"
    objects = None
    try:
        repository = PostgresObjectStoreConfigurationRepository.from_dsn(
            settings.postgres_dsn,
            encryption_key=settings.data_source_credential_key.get_secret_value(),
        )
        stored = repository.current(settings.platform_environment_id)
        if stored is not None:
            configured = settings.model_copy(
                update={
                    **stored.settings_updates(),
                    "object_store_prefix": settings.storage_oss_usage_prefix,
                    "object_store_cache_root": None,
                }
            )
            scope = f"oss://{stored.bucket}/{settings.storage_oss_usage_prefix.strip('/')}/"
            client, _, _ = client_factory(configured)
            objects = S3StorageInventoryProvider(client, stored.bucket).list_prefix("")
    except Exception:
        # Capacity failure must neither hide local capacity nor reveal credential errors.
        pass
    return storage_locations(
        objects=objects, provider="oss", object_scope=scope, local_roots={}, database_bytes=None
    )[1:]

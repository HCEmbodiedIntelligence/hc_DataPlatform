"""Read-only, local-operator directory index over committed platform files.

Run inside the local API container. This is an OS operator tool, not an HTTP API:
it uses the existing local deployment's database connection in a READ ONLY
transaction. Object bytes are never copied, renamed, deleted, or published here.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

LOCAL_TIME = ZoneInfo("Asia/Shanghai")
STATUS = {
    "READY": "处理完成",
    "PARTIALLY_FAILED": "部分处理失败",
    "FAILED": "处理失败",
    "PROCESSING": "正在处理",
    "PENDING": "等待处理",
    "NOT_REQUESTED": "尚未处理",
    "DISCOVERING_EPISODES": "正在识别片段",
    "SLICED": "已确认切片",
    "COMMITTED": "已保存",
}


def component(value: Any, fallback: str = "未命名") -> str:
    """Readable, bounded names that also copy cleanly to Windows/exFAT disks."""
    value = unicodedata.normalize("NFC", str(value or "").strip())
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .") or fallback
    while len(value.encode("utf-8")) > 150:
        value = value[:-1]
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value):
        value = "名称_" + value
    return value


def short_id(value: Any) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:8]


def relative(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ValueError("invalid relative file path")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("invalid relative file path")
    return value


def timestamp(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(LOCAL_TIME)


def scope(row: dict) -> tuple:
    return row["organization_id"], row["project_id"], row.get("region_code", "global")


def clock_name(ns: int) -> str:
    milliseconds = int(ns) // 1_000_000
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}分{seconds:02d}.{ms:03d}秒"


def snapshot(connection) -> dict[str, list[dict]]:
    queries = {
        "projects": "SELECT * FROM registry.organization_projects",
        "tasks": "SELECT organization_id,project_id,collection_task_id,task_code,name,dataset_id "
        "FROM collection_tasks.collection_tasks",
        "robots": "SELECT organization_id,robot_id,display_name FROM robotics.robot_assets",
        "datasets": "SELECT organization_id,project_id,region_code,dataset_id,name "
        "FROM dataset_registry.datasets",
        "raw": "SELECT * FROM ingest.raw_sources WHERE raw_status='COMMITTED'",
        "uploads": "SELECT organization_id,project_id,region_code,raw_source_id,"
        "upload_document->>'capture_started_at' AS capture_started_at,"
        "upload_document->>'capture_ended_at' AS capture_ended_at "
        "FROM ingest.robot_ingest_uploads WHERE state='COMMITTED'",
        "recordings": "SELECT * FROM ingest.continuous_recordings",
        "assets": "SELECT organization_id,project_id,region_code,upload_id,asset_path,"
        "object_key,expected_size,expected_sha256,committed_at "
        "FROM ingest.recording_upload_assets WHERE status='COMMITTED'",
        "episodes": "SELECT * FROM ingest.raw_source_episodes",
        "versions": "SELECT * FROM episode_store.versions WHERE status='COMMITTED'",
    }
    return {name: list(connection.execute(sql).fetchall()) for name, sql in queries.items()}


class Catalog:
    def __init__(self, rows: dict[str, list[dict]], read_json, raw_manifest):
        self.rows, self.read_json, self.raw_manifest = rows, read_json, raw_manifest
        self.files: dict[str, dict] = {}
        self.warnings: list[str] = []
        self.projects = {(r["organization_id"], r["project_id"]): r for r in rows["projects"]}
        self.tasks = {
            (r["organization_id"], r["project_id"], r["collection_task_id"]): r
            for r in rows["tasks"]
        }
        self.robots = {(r["organization_id"], r["robot_id"]): r for r in rows["robots"]}
        self.datasets = {(*scope(r), r["dataset_id"]): r for r in rows["datasets"]}
        self.raw_paths: dict[tuple, str] = {}
        self.recording_paths: dict[tuple, str] = {}
        self.raw_files: dict[tuple, list[dict]] = {}
        self.original_owners: dict[tuple, tuple] = {}
        self.raw_recordings: dict[tuple, list[dict]] = defaultdict(list)
        self.raw_episodes: dict[tuple, dict] = {}
        self.assets: dict[tuple, list[dict]] = defaultdict(list)
        for row in rows["assets"]:
            self.assets[(*scope(row), str(row["upload_id"]))].append(row)
        for row in rows["episodes"]:
            self.raw_episodes[(*scope(row), row["episode_id"])] = row
            ref = row.get("data_ref") or {}
            if ref.get("identity", {}).get("episode_id"):
                self.raw_episodes[(*scope(row), ref["identity"]["episode_id"])] = row

    def text(self, path: str, content: str, modified: Any = None):
        data = content.encode("utf-8")
        self.add(
            path,
            {
                "text": content,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "modified": modified,
            },
        )

    def add(self, path: str, file: dict):
        relative(path)
        if "key" in file:
            relative(file["key"])
        value = {"path": path, **file}
        if path in self.files and self.files[path] != value:
            raise ValueError(f"directory name collision: {path}")
        self.files[path] = value

    def object(self, path: str, key: str, size: int, sha: str, modified: Any):
        self.add(path, {"key": key, "size": int(size), "sha256": sha, "modified": modified})

    def task_path(self, row: dict, task_id: str | None = None) -> str:
        project = self.projects[scope(row)[:2]]
        pid = project["project_id"]
        # Include organization in the suffix only when project IDs collide.
        repeated = sum(p["project_id"] == pid for p in self.projects.values()) > 1
        suffix = pid if len(pid) <= 12 else short_id((*scope(row)[:2],))
        if repeated:
            suffix += "-" + short_id(row["organization_id"])
        parent = component(project["display_name"]) + f"（项目{component(suffix)}）"
        task = self.tasks.get((*scope(row)[:2], task_id or row.get("collection_task_id")))
        if task:
            return f"{parent}/{component(task['name'])}（任务{task['task_code'].strip()}）"
        dataset = self.datasets.get((*scope(row), row.get("dataset_id")), {})
        label = component(dataset.get("name"), "未关联任务")
        return f"{parent}/导入数据_{label}（{short_id(row.get('dataset_id') or scope(row))}）"

    def recording_name(self, row: dict, when: Any, identity: str) -> str:
        date = timestamp(when)
        if date is not None:
            date_part = date.strftime("%Y-%m-%d_%H-%M-%S")
        else:
            imported = timestamp(row.get("committed_at") or row.get("created_at"))
            date_part = "导入于_" + (
                imported.strftime("%Y-%m-%d_%H-%M-%S") if imported else "时间未知"
            )
        robot = self.robots.get((row["organization_id"], row.get("robot_id")), {})
        robot_name = component(robot.get("display_name"), "未登记机器人")
        return f"{date_part}_{robot_name}（录制{short_id((*scope(row), identity))}）"

    def inventory(self):
        for row in self.rows["raw"]:
            if scope(row)[:2] not in self.projects:
                continue
            identity = (*scope(row), row["raw_source_id"])
            try:
                self.raw_files[identity] = self.raw_manifest(row)["files"]
                for file in self.raw_files[identity]:
                    key = file.get("object_key") or f"{row['storage_prefix']}/{file['path']}"
                    owner_key = (*scope(row), key)
                    if (
                        owner_key in self.original_owners
                        and self.original_owners[owner_key] != identity
                    ):
                        # Shared sources are valid; do not invent a unique owner for them.
                        self.original_owners[owner_key] = ()
                    else:
                        self.original_owners[owner_key] = identity
            except Exception as exc:
                self.warnings.append(
                    f"{self.task_path(row)} / 原始记录{short_id(identity[-1])}："
                    f"原始清单暂不可读（{type(exc).__name__}）"
                )
        self.recording_owners = {}
        for row in self.rows["recordings"]:
            owners = {
                self.original_owners.get((*scope(row), a["object_key"]))
                for a in self.assets[(*scope(row), str(row.get("recording_upload_id")))]
            } - {None, ()}
            if len(owners) == 1:
                owner = owners.pop()
                self.recording_owners[(*scope(row), row["recording_id"])] = owner
                self.raw_recordings[owner].append(row)

    def originals(self):
        uploads = {(*scope(r), r["raw_source_id"]): r for r in self.rows["uploads"]}
        for row in self.rows["raw"]:
            if scope(row)[:2] not in self.projects:
                continue
            identity = (*scope(row), row["raw_source_id"])
            if identity not in self.raw_files:
                continue
            recordings = self.raw_recordings[identity]
            when = uploads.get(identity, {}).get("capture_started_at")
            if not when and recordings:
                when = min(r["capture_started_at"] for r in recordings)
            base = self.task_path(row) + "/" + self.recording_name(row, when, identity[-1])
            self.raw_paths[identity] = base
            date = timestamp(when)
            lines = [
                "录制说明",
                "",
                "时间使用北京时间（Asia/Shanghai，UTC+8）。",
                "录制时间："
                + (
                    date.strftime("%Y-%m-%d %H:%M:%S") if date else "未提供；文件夹标注的是导入时间"
                ),
                f"原始格式：{row['source_format']}",
                f"处理状态：{STATUS.get(row['processing_status'], row['processing_status'])}",
                f"原始文件：{row['file_count']} 个，{row['total_bytes']} 字节",
                "",
                "原始文件保留上传时的文件名和目录结构。",
                "处理后的文件与切片保留各版本的数据包结构。",
                "选中文件或整个录制文件夹即可复制；复制后可以独立使用。",
                "",
                f"录制标识：{row['raw_source_id']}",
            ]
            self.text(base + "/录制说明.txt", "\n".join(lines) + "\n", row["updated_at"])
            for file in self.raw_files.get(identity, []):
                key = file.get("object_key") or f"{row['storage_prefix']}/{file['path']}"
                self.object(
                    base + "/原始文件/" + relative(file["path"]),
                    key,
                    file["size"],
                    file["sha256"],
                    row["committed_at"],
                )

    def prepared_recordings(self):
        for row in self.rows["recordings"]:
            if scope(row)[:2] not in self.projects:
                continue
            rid = (*scope(row), row["recording_id"])
            owner = self.recording_owners.get(rid)
            base = self.raw_paths.get(owner)
            if base is None:
                base = (
                    self.task_path(row)
                    + "/"
                    + self.recording_name(row, row["capture_started_at"], row["recording_id"])
                )
                self.text(
                    base + "/录制说明.txt",
                    f"录制时间使用北京时间。\n录制标识：{row['recording_id']}\n",
                    row["updated_at"],
                )
            self.recording_paths[rid] = base
            assets = self.assets[(*scope(row), str(row.get("recording_upload_id")))]
            for asset in assets:
                key = asset["object_key"]
                original = (*scope(row), key) in self.original_owners
                if original and owner:
                    continue
                category = "原始文件" if key.startswith("raw/") else "处理后的文件/整段解析"
                # Different derivations must not silently overwrite one another.
                if owner and len(self.raw_recordings[owner]) > 1 and not original:
                    category += "（批次" + short_id(row["recording_id"]) + "）"
                self.object(
                    base + "/" + category + "/" + relative(asset["asset_path"]),
                    key,
                    asset["expected_size"],
                    asset["expected_sha256"],
                    asset["committed_at"],
                )

    def packages(self):
        self.version_paths = {}
        for row in self.rows["versions"]:
            if scope(row)[:2] not in self.projects:
                continue
            data = row["receipt_document"]["data"]
            identity = data["identity"]
            rollout = identity["rollout_id"]
            original = self.raw_episodes.get((*scope(row), rollout)) or self.raw_episodes.get(
                (*scope(row), identity["episode_id"])
            )
            base = (
                self.raw_paths.get((*scope(row), original["raw_source_id"])) if original else None
            )
            ordinal = int(original["source_episode_index"]) + 1 if original else None
            if ":" in rollout:
                recording_id, _ = rollout.rsplit(":", 1)
                rid = (*scope(row), recording_id)
                base = self.recording_paths.get(rid, base)
            if base is None:
                source = row["input_document"]
                base = self.task_path(row, source.get("task_id")) + "/独立处理结果"
                ordinal = None
            label = f"片段{ordinal:03d}" if ordinal else "片段_" + short_id(identity["episode_id"])
            folder = base + "/处理后的文件/" + label
            path = folder + f"/版本{int(row['version']):03d}"
            try:
                prefix = data["package_prefix"]
                manifest, body = self.read_json(prefix + "/hc/manifest.json")
                if hashlib.sha256(body).hexdigest() != row["receipt_document"]["manifest_sha256"]:
                    raise ValueError("package manifest checksum mismatch")
                if manifest["input"]["identity"] != identity:
                    raise ValueError("package identity mismatch")
                source_entry = next(
                    (f for f in manifest["files"] if f["path"] == "hc/source.json"), None
                )
                if source_entry:
                    frozen, source_body = self.read_json(prefix + "/hc/source.json")
                    if (
                        len(source_body) != source_entry["size"]
                        or hashlib.sha256(source_body).hexdigest() != source_entry["sha256"]
                    ):
                        raise ValueError("source lineage checksum mismatch")
                    if all(
                        k in frozen
                        for k in ("recording_id", "slice_id", "start_offset_ns", "end_offset_ns")
                    ):
                        start, end = int(frozen["start_offset_ns"]), int(frozen["end_offset_ns"])
                        if not 0 <= start < end:
                            raise ValueError("invalid frozen slice window")
                        frozen_base = self.recording_paths.get(
                            (*scope(row), frozen["recording_id"])
                        )
                        if frozen_base:
                            base = frozen_base
                        number = re.fullmatch(r"(?:episode_|slice[_-]?)(\d+)", frozen["slice_id"])
                        label = (
                            f"片段{int(number[1]):03d}"
                            if number
                            else "片段_" + short_id(frozen["slice_id"])
                        )
                        label += f"_{clock_name(start)}至{clock_name(end)}"
                        path = base + "/切片/" + label + f"/版本{int(row['version']):03d}"
                for file in manifest["files"]:
                    self.object(
                        path + "/" + relative(file["path"]),
                        prefix + "/" + file["path"],
                        file["size"],
                        file["sha256"],
                        row["committed_at"],
                    )
                for name in ("manifest.json", "commit.json"):
                    document, content = self.read_json(prefix + "/hc/" + name)
                    if name == "commit.json" and document != row["receipt_document"]:
                        raise ValueError("commit receipt differs from version index")
                    self.object(
                        path + "/hc/" + name,
                        prefix + "/hc/" + name,
                        len(content),
                        hashlib.sha256(content).hexdigest(),
                        row["committed_at"],
                    )
                self.version_paths[row["data_version_id"]] = path
            except Exception as exc:
                # A partial package must not appear to be a complete copyable package.
                for key in list(self.files):
                    if key.startswith(path + "/"):
                        del self.files[key]
                self.warnings.append(f"{path}：处理结果清单暂不可读（{type(exc).__name__}）")

    def build(self) -> dict:
        self.inventory()
        self.originals()
        self.prepared_recordings()
        self.packages()
        if self.warnings:
            self.text(
                "目录状态说明.txt",
                "以下历史记录的文件清单暂不可读，未作为完整文件夹展示。\n"
                "其余目录可以正常查看和复制。\n\n" + "\n".join(self.warnings) + "\n",
            )
        self.text(
            "使用说明.txt",
            "平台数据\n\n"
            "按项目名称 → 任务名称和任务号 → 录制时间与机器人查找。\n"
            "录制时间使用北京时间；没有录制时间的导入批次会明确标注“导入于”。\n"
            "括号中的 8 位录制编号用于区分同一秒开始的录制。\n"
            "原始文件：保留上传时的名称和格式。\n"
            "处理后的文件：整段解析或已处理的原有片段，各版本分别保存。\n"
            "切片：文件夹上的时间范围相对于原录制开头，从 0 开始。\n\n"
            "双击文件可用对应软件打开；选中文件或文件夹，复制粘贴到目标位置。\n"
            "这里是只读视图，复制出的文件可以独立使用和修改。\n"
            "新上传、处理完成的文件会自动出现。\n",
        )
        return {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": sorted(self.files.values(), key=lambda f: f["path"]),
            "warnings": self.warnings,
            "raw_locations": [
                {"scope": list(key[:3]), "raw_source_id": key[3], "path": value + "/原始文件"}
                for key, value in self.raw_paths.items()
            ],
            "recording_count": len(self.raw_paths),
            "version_count": len(self.version_paths),
        }


def main():
    import boto3
    import psycopg
    from botocore.config import Config
    from psycopg.rows import dict_row

    from hc_data_platform.core.config import get_settings
    from hc_data_platform.core.dbapi import normalize_postgres_dsn
    from hc_data_platform.ingest.raw_sources import RawSource
    from hc_data_platform.lerobot_imports.source_browser import OriginalSourceBrowser
    from hc_data_platform.object_storage.providers import S3ObjectStorage

    settings = get_settings()
    if settings.environment != "local" or settings.object_store_provider != "s3":
        raise RuntimeError("local file view requires the local S3 deployment")
    with psycopg.connect(
        normalize_postgres_dsn(settings.postgres_dsn),
        row_factory=dict_row,
        options="-c default_transaction_read_only=on -c statement_timeout=15000 "
        "-c default_transaction_isolation=repeatable\\ read",
    ) as connection:
        rows = snapshot(connection)
    client = boto3.client(
        "s3",
        endpoint_url=settings.object_store_endpoint,
        aws_access_key_id=settings.object_store_access_key,
        aws_secret_access_key=settings.object_store_secret_key,
        region_name=settings.object_store_region,
        config=Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=20,
            retries={"max_attempts": 2},
            s3={"addressing_style": "path"},
        ),
    )
    storage = S3ObjectStorage(client, settings.object_store_bucket)
    browser = OriginalSourceBrowser(storage, None)

    @lru_cache(maxsize=1024)
    def read_json(key):
        response = client.get_object(Bucket=settings.object_store_bucket, Key=key)
        with response["Body"] as stream:
            body = stream.read(16 * 1024**2 + 1)
        if len(body) > 16 * 1024**2:
            raise ValueError("metadata size limit")
        return json.loads(body), body

    def raw_manifest(row):
        raw = RawSource.model_validate(
            {k: v for k, v in row.items() if k in RawSource.model_fields}
        )
        return browser.manifest(raw)

    print(
        json.dumps(Catalog(rows, read_json, raw_manifest).build(), ensure_ascii=False, default=str)
    )


if __name__ == "__main__":
    main()

"""Register existing OSS objects through the normal native import commit path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.capture_schemes.ports import unresolved_receipt
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.ingest.models import Identifier

from .models import CommitLeRobotImportV1, CreateLeRobotImportV1, lerobot_part_plan
from .oss_archive import publish_archive, stage_archive
from .service import _canonical_manifest, read_bounded


class InspectOssImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_uri: str = Field(min_length=1, max_length=2048)


class CommitOssImport(InspectOssImport):
    recording_path: str | None = Field(default=None, max_length=1024)
    inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_id: Identifier
    collection_task_id: Identifier
    robot_id: Identifier
    processing_mode: Literal["STORE_ONLY", "PROCESS"] = "STORE_ONLY"
    processing_capability_revision: str | None = None


def failure(code: str, detail: str, status: int = 422):
    return problem(status=status, code=code, title="OSS 导入未完成", detail=detail)


def inspect_source(storage, source_uri: str) -> dict[str, Any]:
    with TemporaryDirectory(prefix="oss-inspect-") as directory:
        return _inspect_source(storage, source_uri, Path(directory))


def _inspect_source(storage, source_uri: str, directory: Path) -> dict[str, Any]:
    inspect = getattr(storage, "inspect_reference", None)
    if inspect is None:
        raise failure("OSS_IMPORT_UNAVAILABLE", "请先在平台运维中配置 OSS 存储连接。", 503)
    try:
        inventory = inspect(source_uri.strip())
        files = inventory["files"]
        if not inventory.get("archive_members") and len(files) == 1 and files[0]["path"].lower().endswith(".zip"):
            inventory = stage_archive(storage, inventory, directory)
            files = inventory["files"]
        paths = {item["path"] for item in files}
        info = inventory.get("info", {})
        if "meta/info.json" in paths:
            if "archive" not in inventory:
                descriptor = next(f for f in files if f["path"] == "meta/info.json")
                info = json.loads(read_bounded(storage, descriptor["object_key"], 1024**2))
            source_format = "LEROBOT_V3"
        elif any(p.lower().endswith(".bag") for p in paths) or (
            any(p.endswith("metadata.yaml") for p in paths)
            and any(p.lower().endswith((".db3", ".mcap")) for p in paths)
        ):
            source_format = "ROSBAG"
        elif any(p.lower().endswith(".mcap") for p in paths):
            source_format = "MCAP"
        else:
            raise ValueError(
                "未识别到可导入的数据。请选择 MCAP、ROS bag 或 LeRobot v3 根目录。"
                "LeRobot 目录需包含 meta/info.json；压缩包请直接选择单个 ZIP 文件地址。"
            )
        CreateLeRobotImportV1(
            dataset_id="inspection",
            source_format=source_format,
            info=info,
            files=[
                dict(path=f["path"], size=f["size"], part_count=lerobot_part_plan(f["size"])[1])
                for f in files
            ],
        )
        inventory.update(source_format=source_format, info=info)
        inventory["inventory_sha256"] = hashlib.sha256(
            json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        inventory["total_bytes"] = sum(f["size"] for f in files)
        return inventory
    except ProblemException:
        raise
    except (ValueError, UnicodeError) as exc:
        raise failure("OSS_IMPORT_SOURCE_INVALID", str(exc)) from exc
    except Exception as exc:
        raise failure(
            "OSS_IMPORT_READ_FAILED",
            "无法读取 OSS，请检查连接、Bucket、目录以及 List/Get 权限后重试。",
            503,
        ) from exc


def commit_source(
    service, *, auth, organization_id, project_id, region_code, command: CommitOssImport
):
    scope = dict(organization_id=organization_id, project_id=project_id, region_code=region_code)
    service._authorize(auth, **scope)
    with TemporaryDirectory(prefix="oss-import-") as directory:
        return _commit_source(service, auth, scope, command, Path(directory))


def _commit_source(service, auth, scope, command, directory):
    organization_id, project_id, region_code = (
        scope["organization_id"],
        scope["project_id"],
        scope["region_code"],
    )
    inventory = _inspect_source(service.storage, command.source_uri, directory)
    if inventory["inventory_sha256"] != command.inventory_sha256:
        raise failure(
            "OSS_IMPORT_SOURCE_CHANGED", "OSS 文件在检查后发生变化，请重新检查并确认。", 409
        )
    if inventory.get("archive_members"):
        selected = command.recording_path
        if not selected or not any(f["path"] == selected and selected.lower().endswith(".mcap") for f in inventory["files"]):
            raise failure("OSS_IMPORT_SELECT_RECORDING", "请选择压缩包中的一条录制。")
        paths = {selected, str(Path(selected).with_suffix(".takeovers.json"))}
        inventory["files"] = [f for f in inventory["files"] if f["path"] in paths]
        inventory["total_bytes"] = sum(f["size"] for f in inventory["files"])
    elif command.recording_path is not None:
        raise failure("OSS_IMPORT_SELECT_RECORDING", "该数据源不支持选择录制。")
    manifest = CreateLeRobotImportV1(
        dataset_id=command.dataset_id,
        collection_task_id=command.collection_task_id,
        robot_id=command.robot_id,
        processing_mode=command.processing_mode,
        processing_capability_revision=command.processing_capability_revision,
        source_format=inventory["source_format"],
        info=inventory["info"],
        files=[
            dict(path=f["path"], size=f["size"], part_count=lerobot_part_plan(f["size"])[1])
            for f in inventory["files"]
        ],
    )
    if service._target_validator is not None:
        service._target_validator(organization_id, project_id, region_code, manifest)
    canonical = _canonical_manifest(manifest)
    import_id = hashlib.sha256(
        json.dumps(
            dict(
                scope=scope,
                manifest=canonical,
                inventory=command.inventory_sha256,
                kind="oss-reference-v1",
            ),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:32]
    root = service._root(organization_id, manifest.dataset_id, import_id)
    session_key = f"{root}/upload-session.json"
    if "archive" in inventory and not inventory.get("archive_members"):
        publish_archive(service.storage, inventory, directory, root)
    if service.storage.head(session_key) is None:
        snapshot = None
        if manifest.source_format in {"MCAP", "LEROBOT_V3"}:
            snapshot = (
                service._capture_plans.prepare(
                    (organization_id, project_id, region_code),
                    import_id,
                    dataset_id=manifest.dataset_id,
                    task_id=manifest.collection_task_id,
                    robot_id=manifest.robot_id,
                    source_format=manifest.source_format,
                    actor=auth.subject_id,
                )
                if service._capture_plans
                else unresolved_receipt("CAPTURE_STORE_UNAVAILABLE", "采集方案服务未配置。")
            )
        session = dict(
            schema_version="lerobot-web-upload-session/v1",
            **scope,
            manifest=canonical,
            capture_snapshot=snapshot,
            assets=[],
        )
        if "archive" in inventory:
            session["oss_archive"] = dict(
                source_uri=inventory["source_uri"], **inventory["archive"]
            )
        if "archive" not in inventory or inventory.get("archive_members"):
            session["oss_reference"] = inventory
        try:
            service.storage.put_json(session_key, session, if_none_match=True)
        except Exception:
            if service.storage.head(session_key) is None:
                raise
    return service.commit(
        auth=auth, **scope, import_id=import_id, command=CommitLeRobotImportV1(manifest=manifest)
    )

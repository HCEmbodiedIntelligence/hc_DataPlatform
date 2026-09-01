"""Upload a native Unitree G1 LeRobot v3 directory through the platform API."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from hc_data_platform.ingest.cli import (
    DEFAULT_MAX_PART_RETRIES,
    DEFAULT_RETRY_BASE_SECONDS,
    HttpResponse,
    UploadHttpPort,
    UrllibUploadHttpClient,
    _json_request,
    _raise_for_status,
    _request_with_retry,
)
from hc_data_platform.lerobot_imports.models import (
    CreateLeRobotImportV1,
    LeRobotSourceFileV1,
)
from hc_data_platform.lerobot_imports.source_profile import find_local_source_root

PROJECT_ID = "be22-hf-g1-video-20260819-02-p1"
COLLECTION_TASK_ID = "14d16ba1-d95a-5ee3-aaa7-7b7d78091b52"
ROBOT_ID = "robot-d1a17126-b495-59b8-bf48-0ccce0a6ffe7"
PLATFORM_REGION_CODE = "be22-hf-g1-video-20260819-02-cn"
TOKEN_ENV = "HC_DATA_ACCESS_TOKEN"
ORGANIZATION_ENV = "HC_ORGANIZATION_ID"
LEROBOT_MULTIPART_BYTES = 32 * 1024**2
MAX_MULTIPART_PARTS = 10_000


@dataclass(frozen=True, slots=True)
class NativeLeRobotSource:
    root: Path
    manifest: CreateLeRobotImportV1
    files: Mapping[str, Path]


def _normalize_api_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized.endswith("/api/v1"):
        normalized = normalized[: -len("/api/v1")]
    if not normalized.startswith(("http://", "https://")):
        raise ValueError("platform address must start with http:// or https://")
    return normalized


def platform_login(
    *,
    api_base_url: str,
    username: str,
    password: str,
    http: UploadHttpPort | None = None,
) -> str:
    client = http or UrllibUploadHttpClient()
    response = client.request(
        "POST",
        f"{_normalize_api_base_url(api_base_url)}/api/v1/auth/sessions",
        headers={"Content-Type": "application/json"},
        body=json.dumps(
            {"username": username, "password": password}, separators=(",", ":")
        ).encode(),
    )
    if response.status != 201:
        try:
            payload = cast(dict[str, Any], response.json())
            detail = str(payload.get("detail") or payload.get("title") or "login failed")
        except (TypeError, ValueError, json.JSONDecodeError):
            detail = "login failed"
        raise RuntimeError(f"platform login failed ({response.status}): {detail}")
    try:
        token = str(cast(dict[str, Any], response.json())["access_token"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("platform login response omitted access_token") from exc
    if not token:
        raise RuntimeError("platform login returned an empty access_token")
    return token


def build_native_source(
    source_dir: Path,
    *,
    dataset_id: str,
    collection_task_id: str,
    robot_id: str,
) -> NativeLeRobotSource:
    """Inspect one LeRobot tree while preserving every original source object."""

    root = find_local_source_root(source_dir)
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"LeRobot source cannot contain symbolic links: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if size < 1:
            raise ValueError(f"LeRobot source object is empty: {relative}")
        files[relative] = path
    try:
        info = json.loads(files["meta/info.json"].read_text(encoding="utf-8"))
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("LeRobot meta/info.json is missing or invalid") from exc
    declarations = tuple(
        LeRobotSourceFileV1(
            path=relative,
            size=path.stat().st_size,
            part_count=_part_count(path.stat().st_size),
        )
        for relative, path in files.items()
    )
    manifest = CreateLeRobotImportV1(
        dataset_id=dataset_id,
        collection_task_id=collection_task_id,
        robot_id=robot_id,
        info=info,
        files=declarations,
    )
    return NativeLeRobotSource(root=root, manifest=manifest, files=files)


def upload_native_lerobot(
    source_dir: Path,
    *,
    organization_id: str,
    project_id: str,
    region_code: str,
    dataset_id: str,
    collection_task_id: str,
    robot_id: str,
    api_base_url: str,
    access_token: str,
    max_part_retries: int = DEFAULT_MAX_PART_RETRIES,
    retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    http: UploadHttpPort | None = None,
) -> dict[str, Any]:
    source = build_native_source(
        source_dir,
        dataset_id=dataset_id,
        collection_task_id=collection_task_id,
        robot_id=robot_id,
    )
    client = http or UrllibUploadHttpClient()
    root = (
        f"{_normalize_api_base_url(api_base_url)}/api/v1/projects/"
        f"{quote(project_id, safe='')}/regions/{quote(region_code, safe='')}"
        "/lerobot-imports"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Organization-Id": organization_id,
    }
    manifest_payload = source.manifest.model_dump(mode="json")
    grant = cast(
        dict[str, Any],
        _json_request(
            client,
            "POST",
            root,
            headers=headers,
            payload=manifest_payload,
            expected={200, 201},
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        ),
    )
    import_id = str(grant["import_id"])
    grants_by_path = {
        str(item["path"]): item for item in cast(list[dict[str, Any]], grant["assets"])
    }
    declarations = {item.path: item for item in source.manifest.files}
    ordered_paths = sorted(source.files, key=lambda path: (path == "meta/info.json", path))
    for relative in ordered_paths:
        declaration = declarations[relative]
        asset = grants_by_path.get(relative)
        if asset is None or not asset.get("multipart_upload_id"):
            raise RuntimeError(f"platform omitted upload grant for {relative}")
        multipart_upload_id = str(asset["multipart_upload_id"])
        with source.files[relative].open("rb") as stream:
            for part_number in range(1, declaration.part_count + 1):
                body = stream.read(_part_size(declaration.size))
                if not body:
                    raise RuntimeError(f"source ended before part {part_number}: {relative}")
                authorization = cast(
                    dict[str, Any],
                    _json_request(
                        client,
                        "POST",
                        f"{root}/{quote(import_id, safe='')}/assets:authorize-parts",
                        headers=headers,
                        payload={
                            "dataset_id": dataset_id,
                            "path": relative,
                            "multipart_upload_id": multipart_upload_id,
                            "part_numbers": [part_number],
                        },
                        expected={200},
                        max_retries=max_part_retries,
                        retry_base_seconds=retry_base_seconds,
                        sleep=sleep,
                    ),
                )
                parts = cast(list[dict[str, Any]], authorization["parts"])
                if len(parts) != 1 or int(parts[0]["part_number"]) != part_number:
                    raise RuntimeError("platform returned the wrong part authorization")
                print(
                    f"upload {relative} part {part_number}/{declaration.part_count}",
                    file=sys.stderr,
                )
                _put_part(
                    client,
                    url=str(parts[0]["url"]),
                    body=body,
                    max_retries=max_part_retries,
                    retry_base_seconds=retry_base_seconds,
                    sleep=sleep,
                )
            if stream.read(1):
                raise RuntimeError(f"source exceeds its declared part count: {relative}")
        _json_request(
            client,
            "POST",
            f"{root}/{quote(import_id, safe='')}/assets:complete",
            headers=headers,
            payload={
                "dataset_id": dataset_id,
                "path": relative,
                "multipart_upload_id": multipart_upload_id,
                "size": declaration.size,
                "part_count": declaration.part_count,
            },
            expected={200},
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )
    return cast(
        dict[str, Any],
        _json_request(
            client,
            "POST",
            f"{root}/{quote(import_id, safe='')}:commit",
            headers=headers,
            payload={"manifest": manifest_payload},
            expected={200},
            max_retries=max_part_retries,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        ),
    )


def _part_count(size: int) -> int:
    return math.ceil(size / _part_size(size))


def _part_size(size: int) -> int:
    if not 1 <= size <= 5 * 1024**4:
        raise ValueError("LeRobot source objects must be between 1 byte and 5 TiB")
    return max(LEROBOT_MULTIPART_BYTES, math.ceil(size / MAX_MULTIPART_PARTS))


def _put_part(
    client: UploadHttpPort,
    *,
    url: str,
    body: bytes,
    max_retries: int,
    retry_base_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    response: HttpResponse = _request_with_retry(
        client,
        "PUT",
        url,
        headers={"Content-Length": str(len(body))},
        body=body,
        max_retries=max_retries,
        retry_base_seconds=retry_base_seconds,
        sleep=sleep,
    )
    _raise_for_status(response, {200, 201, 204})


def _quoted_path(value: str) -> Path:
    normalized = value.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
        normalized = normalized[1:-1]
    if not normalized:
        raise ValueError("local LeRobot path is required")
    return Path(normalized)


def _prompt(
    label: str,
    *,
    default: str | None = None,
    reader: Callable[[str], str] = input,
) -> str:
    suffix = f"（默认 {default}）" if default is not None else ""
    value = reader(f"{label}{suffix}: ").strip()
    if value:
        return value
    if default is not None:
        return default
    raise ValueError(f"{label}不能为空")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--api-base-url")
    parser.add_argument("--username")
    parser.add_argument("--organization-id")
    parser.add_argument("--project-id", default=PROJECT_ID)
    parser.add_argument("--region-code", default=PLATFORM_REGION_CODE)
    parser.add_argument("--dataset-id", default=PROJECT_ID)
    parser.add_argument("--collection-task-id", default=COLLECTION_TASK_ID)
    parser.add_argument("--robot-id", default=ROBOT_ID)
    parser.add_argument("--access-token-env", default=TOKEN_ENV)
    parser.add_argument("--max-part-retries", type=int, default=DEFAULT_MAX_PART_RETRIES)
    return parser


def run_interactive(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = cast(Path | None, args.source_dir)
    if source_dir is None:
        source_dir = _quoted_path(_prompt("请输入本地 LeRobot 文件夹路径"))
    api_base_url = cast(str | None, args.api_base_url) or _prompt(
        "请输入平台地址", default="http://127.0.0.1:8000"
    )
    organization_id = cast(str | None, args.organization_id)
    organization_id = organization_id or os.environ.get(ORGANIZATION_ENV, "").strip()
    organization_id = organization_id or _prompt("请输入 Organization ID")
    token_env = cast(str, args.access_token_env)
    token = os.environ.get(token_env, "").strip()
    if not token:
        username = cast(str | None, args.username) or _prompt("请输入平台用户名")
        password = getpass("请输入平台密码（输入不显示）: ")
        if not password:
            raise ValueError("平台密码不能为空")
        token = platform_login(
            api_base_url=api_base_url,
            username=username,
            password=password,
        )

    source = build_native_source(
        source_dir,
        dataset_id=cast(str, args.dataset_id),
        collection_task_id=cast(str, args.collection_task_id),
        robot_id=cast(str, args.robot_id),
    )
    print("\n将通过平台原样上传 LeRobot Raw（不会生成 MCAP）：")
    print(f"  Project : {args.project_id}")
    print(f"  Region  : {args.region_code}")
    print(f"  Dataset : {args.dataset_id}")
    print(f"  Task    : {args.collection_task_id}")
    print(f"  Robot   : {args.robot_id}")
    print(f"  Episodes: {source.manifest.episode_count}")
    print(f"  Files   : {len(source.files)}")
    return upload_native_lerobot(
        source.root,
        organization_id=organization_id,
        project_id=cast(str, args.project_id),
        region_code=cast(str, args.region_code),
        dataset_id=cast(str, args.dataset_id),
        collection_task_id=cast(str, args.collection_task_id),
        robot_id=cast(str, args.robot_id),
        api_base_url=api_base_url,
        access_token=token,
        max_part_retries=int(args.max_part_retries),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_interactive(args)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消上传。", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print("\n平台上传完成：")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

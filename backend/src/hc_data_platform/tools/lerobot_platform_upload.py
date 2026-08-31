"""Convert a local Unitree G1 LeRobot folder and upload it through the platform."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from getpass import getpass
from pathlib import Path
from typing import Any, cast

from hc_data_platform.ingest.cli import UploadHttpPort, UrllibUploadHttpClient

from . import lerobot_unitree_g1_import as importer

PROJECT_ID = "be22-hf-g1-video-20260819-02-p1"
COLLECTION_TASK_ID = "14d16ba1-d95a-5ee3-aaa7-7b7d78091b52"
ROBOT_ID = "robot-d1a17126-b495-59b8-bf48-0ccce0a6ffe7"
PLATFORM_REGION_CODE = "be22-hf-g1-video-20260819-02-cn"
TOKEN_ENV = "HC_DATA_ACCESS_TOKEN"


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
            problem = cast(dict[str, Any], response.json())
            detail = str(problem.get("detail") or problem.get("title") or "login failed")
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


def _quoted_path(value: str) -> Path:
    normalized = value.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
        normalized = normalized[1:-1]
    if not normalized:
        raise ValueError("local LeRobot path is required")
    return Path(normalized)


def build_import_arguments(
    *,
    source_dir: Path,
    api_base_url: str,
    episode_count: int,
    output_dir: Path,
    cache_dir: Path,
) -> argparse.Namespace:
    return importer.build_parser().parse_args(
        [
            "--source-dir",
            str(source_dir),
            "--project-id",
            PROJECT_ID,
            "--collection-task-id",
            COLLECTION_TASK_ID,
            "--robot-id",
            ROBOT_ID,
            "--all-episodes",
            "--episode-count",
            str(episode_count),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
            "--api-base-url",
            _normalize_api_base_url(api_base_url),
            "--region-code",
            PLATFORM_REGION_CODE,
            "--access-token-env",
            TOKEN_ENV,
            "--env-file",
            "",
        ]
    )


def upload(
    *,
    source_dir: Path,
    api_base_url: str,
    token: str,
    episode_count: int,
    output_dir: Path,
    cache_dir: Path,
) -> dict[str, Any]:
    args = build_import_arguments(
        source_dir=source_dir,
        api_base_url=api_base_url,
        episode_count=episode_count,
        output_dir=output_dir,
        cache_dir=cache_dir,
    )
    previous = os.environ.get(TOKEN_ENV)
    os.environ[TOKEN_ENV] = token
    try:
        return importer.run(args)
    finally:
        if previous is None:
            os.environ.pop(TOKEN_ENV, None)
        else:
            os.environ[TOKEN_ENV] = previous


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
    parser.add_argument("--access-token-env", default=TOKEN_ENV)
    parser.add_argument("--episode-count", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/hf-unitree-g1-mcap"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/lerobot-platform-upload"))
    return parser


def run_interactive(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= args.episode_count <= 100:
        raise ValueError("--episode-count must be between 1 and 100")
    source_dir = cast(Path | None, args.source_dir)
    if source_dir is None:
        source_dir = _quoted_path(_prompt("请输入本地 LeRobot 文件夹路径"))
    api_base_url = cast(str | None, args.api_base_url) or _prompt(
        "请输入平台地址", default="http://127.0.0.1:8000"
    )
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

    print("\n将通过平台上传以下数据：")
    print(f"  Project : {PROJECT_ID}")
    print(f"  Region  : {PLATFORM_REGION_CODE}")
    print(f"  Task    : {COLLECTION_TASK_ID}")
    print(f"  Robot   : {ROBOT_ID}")
    print(f"  Episodes: 0-{int(args.episode_count) - 1}")
    return upload(
        source_dir=source_dir,
        api_base_url=api_base_url,
        token=token,
        episode_count=int(args.episode_count),
        output_dir=cast(Path, args.output_dir),
        cache_dir=cast(Path, args.cache_dir),
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

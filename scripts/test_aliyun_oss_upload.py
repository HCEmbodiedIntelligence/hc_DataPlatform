#!/usr/bin/env python3
"""手动上传单个文件或完整 LeRobot 目录到阿里云 OSS。

Bucket、地域和 Endpoint 已按当前项目的 OSS 配置填写。AccessKey ID 和
AccessKey Secret、本地路径和 OSS 保存路径均在运行时手动输入，不会保存到磁盘。
目录上传会忽略下载缓存/临时文件，并将 ``meta/info.json`` 最后上传，供平台自动
发现器把它当作 LeRobot 根目录的完成标志。
"""

from __future__ import annotations

import re
import sys
from getpass import getpass
from hashlib import sha256
from mimetypes import guess_type
from pathlib import Path, PurePosixPath

ENDPOINT = "https://oss-cn-beijing.aliyuncs.com"
BUCKET_NAME = "humanoid-robot-embodied-operation"
REGION = "cn-beijing"
OBJECT_PREFIX = "lerobot-inbox"
TRANSIENT_SUFFIXES = (".part", ".lock", ".incomplete", ".oss-download")


def require_value(prompt: str, *, hidden: bool = False) -> str:
    """Read one required credential without persisting it."""
    reader = getpass if hidden else input
    value = reader(prompt).strip()
    if not value:
        raise ValueError("输入不能为空")
    return value


def read_local_path() -> Path:
    """Read and validate the local file or directory selected by the operator."""
    raw_path = require_value("请输入要上传的本地文件或文件夹路径: ")
    if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] and raw_path[0] in {'"', "'"}:
        raw_path = raw_path[1:-1]
    windows_match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw_path)
    path = (
        Path("/mnt")
        / windows_match.group(1).lower()
        / windows_match.group(2).replace("\\", "/")
        if windows_match
        else Path(raw_path).expanduser()
    )
    if not path.is_file() and not path.is_dir():
        raise ValueError(f"本地路径不存在或类型不受支持：{path}")
    return path.resolve()


def upload_files(source: Path) -> tuple[tuple[Path, PurePosixPath], ...]:
    if source.is_file():
        return ((source, PurePosixPath(source.name)),)
    selected: list[tuple[Path, PurePosixPath]] = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = PurePosixPath(path.relative_to(source).as_posix())
        if ".cache" in relative.parts or path.name.endswith(TRANSIENT_SUFFIXES):
            continue
        selected.append((path, relative))
    # LeRobot discovery only starts after the root metadata marker is visible.
    selected.sort(key=lambda item: (item[1].as_posix().endswith("meta/info.json"), item[1]))
    if not selected:
        raise ValueError(f"文件夹中没有可上传的正式文件：{source}")
    return tuple(selected)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    try:
        import oss2
    except ModuleNotFoundError:
        print("错误：当前 Python 环境没有安装 oss2。", file=sys.stderr)
        print(
            "请使用项目虚拟环境运行："
            " backend/.venv/bin/python scripts/test_aliyun_oss_upload.py",
            file=sys.stderr,
        )
        return 2

    print("阿里云 OSS 手动上传测试")
    print(f"  Bucket : {BUCKET_NAME}")
    print(f"  Region : {REGION}")
    print(f"  Endpoint: {ENDPOINT}")
    print()

    try:
        access_key_id = require_value("请输入 AccessKey ID: ")
        access_key_secret = require_value("请输入 AccessKey Secret（输入不显示）: ", hidden=True)
        local_path = read_local_path()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消测试。", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    default_object_key = f"{OBJECT_PREFIX}/{local_path.name}"
    try:
        object_key = input(
            f"请输入 OSS 保存路径/目录前缀（留空使用 {default_object_key}）: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消测试。", file=sys.stderr)
        return 130
    object_prefix = (object_key or default_object_key).strip("/")
    if not object_prefix:
        print("错误：OSS 保存路径不能为空。", file=sys.stderr)
        return 2
    if object_key.startswith("/"):
        print("错误：OSS 保存路径不能以 / 开头。", file=sys.stderr)
        return 2
    if len(object_prefix.encode("utf-8")) > 900:
        print("错误：OSS 保存路径的 UTF-8 长度不能超过 1023 字节。", file=sys.stderr)
        return 2
    try:
        files = upload_files(local_path)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    bucket = oss2.Bucket(
        oss2.Auth(access_key_id, access_key_secret),
        ENDPOINT,
        BUCKET_NAME,
        connect_timeout=15,
        is_cname=False,
    )

    total_size = sum(path.stat().st_size for path, _relative in files)
    print(f"\n本地路径：{local_path}")
    print(f"正式文件：{len(files)} 个，共 {total_size} bytes")
    print(f"OSS 前缀：{object_prefix}")
    for index, (local_file, relative) in enumerate(files, start=1):
        object_key = (
            object_prefix
            if local_path.is_file()
            else f"{object_prefix}/{relative.as_posix()}"
        )
        if len(object_key.encode("utf-8")) > 1023:
            print(f"错误：OSS 对象路径过长：{object_key}", file=sys.stderr)
            return 2
        content_type = guess_type(local_file.name)[0] or "application/octet-stream"
        print(f"[{index}/{len(files)}] {relative} -> {object_key}")
        try:
            bucket.put_object_from_file(
                object_key,
                str(local_file),
                headers={
                    "Content-Type": content_type,
                    "x-oss-meta-source": "hc-data-platform-manual-upload",
                    "x-oss-meta-sha256": file_sha256(local_file),
                },
            )
            metadata = bucket.get_object_meta(object_key)
        except (oss2.exceptions.OssError, OSError) as exc:
            print("\n上传或读取校验失败。", file=sys.stderr)
            print_oss_error(exc)
            print_console_hint(object_prefix)
            return 1
        if int(metadata.content_length) != local_file.stat().st_size:
            print(f"错误：OSS 对象大小不一致：{object_key}", file=sys.stderr)
            return 1

    print("\n全部上传成功，且 OSS 对象大小校验通过。")
    if local_path.is_dir():
        print("平台自动发现器会在对象列表稳定后读取 meta/info.json 并开始导入。")
    print_console_hint(object_prefix)
    return 0


def print_oss_error(exc: Exception) -> None:
    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    request_id = getattr(exc, "request_id", None)
    message = getattr(exc, "message", None) or str(exc)
    if status is not None:
        print(f"  HTTP 状态: {status}", file=sys.stderr)
    if code:
        print(f"  OSS 错误码: {code}", file=sys.stderr)
    if request_id:
        print(f"  Request ID: {request_id}", file=sys.stderr)
    print(f"  详情: {message}", file=sys.stderr)


def print_console_hint(object_key: str) -> None:
    print("\n上传内容会保留在 OSS 中，不会自动删除。")
    print("请到 OSS 控制台打开以下位置核对：")
    print(f"  Bucket   : {BUCKET_NAME}")
    print(f"  文件路径 : {object_key}")


if __name__ == "__main__":
    raise SystemExit(main())

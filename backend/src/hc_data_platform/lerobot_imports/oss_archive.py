"""Bounded ZIP staging for OSS imports; inspection never publishes objects."""

from __future__ import annotations

import hashlib
import json
import stat
import zipfile
import zlib
from pathlib import Path

from hc_data_platform.object_storage.immutable import MultipartImmutableWriter

MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_ARCHIVE_ENTRIES = 10_000


def stage_archive(storage, inventory: dict, directory: Path) -> dict:
    descriptor = inventory["files"][0]
    key = descriptor["object_key"]
    before = storage.head(key)
    if before is None or before.size != descriptor["size"] or before.etag != descriptor["etag"]:
        raise ValueError("OSS 压缩包已变化，请重新检查。")
    if before.size > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP 压缩包最多支持 2 GiB，请解压后选择 OSS 目录。")
    archive_path = directory / "source.zip"
    received, digest = 0, hashlib.sha256()
    with archive_path.open("wb") as target:
        for chunk in storage.read_chunks(key):
            received += len(chunk)
            if received > before.size:
                raise ValueError("OSS 压缩包大小已变化，请重新检查。")
            digest.update(chunk)
            target.write(chunk)
    if received != before.size or storage.head(key) != before:
        raise ValueError("OSS 压缩包在读取期间发生变化，请重新检查。")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise ValueError("ZIP 最多支持 10000 个条目，请拆分后导入。")
            names, total, selected = set(), 0, []
            for entry in entries:
                name = entry.orig_filename.replace("\\", "/")
                path = name.rstrip("/") if entry.is_dir() else name
                if (
                    not path
                    or path.startswith("/")
                    or ":" in path
                    or any(ord(c) < 32 for c in path)
                    or any(p in {"", ".", ".."} for p in path.split("/"))
                ):
                    raise ValueError("ZIP 包含不安全的文件路径，请重新打包。")
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if entry.flag_bits & 1 or kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ValueError("ZIP 不支持加密文件、符号链接或特殊文件。")
                if path in names:
                    raise ValueError("ZIP 内存在重复路径，请重新打包。")
                names.add(path)
                if entry.is_dir():
                    continue
                total += entry.file_size
                if total > MAX_ARCHIVE_BYTES:
                    raise ValueError("ZIP 解压后最多支持 2 GiB，请解压后选择 OSS 目录。")
                if entry.file_size == 0:
                    raise ValueError("ZIP 包含空文件，请移除空文件后重新导入。")
                selected.append((path, entry))
            if not selected:
                raise ValueError("ZIP 内没有可导入的文件。")
            file_paths = {path for path, _ in selected}
            if any(
                parent.as_posix() in file_paths
                for path in file_paths
                for parent in Path(path).parents
                if parent.as_posix() != "."
            ):
                raise ValueError("ZIP 存在文件与目录路径冲突，请重新打包。")
            while all("/" in path for path, _ in selected):
                roots = {path.split("/", 1)[0] for path, _ in selected}
                if len(roots) != 1:
                    break
                selected = [(path.split("/", 1)[1], entry) for path, entry in selected]
            files = []
            for path, entry in selected:
                target = directory / "files" / path
                target.parent.mkdir(parents=True, exist_ok=True)
                count, checksum = 0, hashlib.sha256()
                with archive.open(entry) as source, target.open("wb") as output:
                    while chunk := source.read(1024**2):
                        count += len(chunk)
                        if count > entry.file_size:
                            raise ValueError("ZIP 文件大小与清单不一致。")
                        checksum.update(chunk)
                        output.write(chunk)
                if count != entry.file_size:
                    raise ValueError("ZIP 文件内容不完整。")
                files.append(dict(path=path, size=count, sha256=checksum.hexdigest()))
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as exc:
        raise ValueError("ZIP 读取或校验失败，请检查压缩包后重试。") from exc
    info_path = directory / "files/meta/info.json"
    if info_path.exists():
        if info_path.stat().st_size > 1024**2:
            raise ValueError("meta/info.json 不能超过 1 MiB。")
        inventory["info"] = json.loads(info_path.read_bytes())
    inventory["archive"] = dict(descriptor, sha256=digest.hexdigest())
    inventory["files"] = sorted(files, key=lambda item: item["path"])
    return inventory


def publish_archive(storage, inventory: dict, directory: Path, root: str) -> None:
    writer = MultipartImmutableWriter(storage)
    for item in inventory["files"]:
        with (directory / "files" / item["path"]).open("rb") as source:
            writer.write_immutable(
                f"{root}/source/{item['path']}",
                source,
                size=item["size"],
                sha256=item["sha256"],
            )

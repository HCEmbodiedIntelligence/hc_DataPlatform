"""Folder navigation over a committed source manifest, never arbitrary disk paths."""

from __future__ import annotations

import codecs
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel

from hc_data_platform.core.errors import problem
from hc_data_platform.ingest.raw_sources import RawSource

from .local_files import safe_relative
from .source_browser import OriginalSourceBrowser

TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".ndjson",
    ".yaml",
    ".yml",
    ".txt",
    ".log",
    ".csv",
    ".toml",
    ".urdf",
    ".xml",
    ".ini",
}
TEXT_PREVIEW_BYTES = 256 * 1024


class OriginalDirectoryEntry(BaseModel):
    name: str
    path: str
    kind: Literal["DIRECTORY", "FILE"]
    size: int
    file_count: int
    sha256: str | None = None
    storage_key: str | None = None
    local_path: str | None = None
    text_preview: bool = False


class OriginalDirectory(BaseModel):
    import_id: str
    path: str
    storage_prefix: str
    local_path: str | None
    entries: list[OriginalDirectoryEntry]
    total: int
    offset: int
    file_count: int
    total_bytes: int


class OriginalTextPreview(BaseModel):
    path: str
    text: str
    truncated: bool
    preview_bytes: int


def _safe_path(path: str) -> str:
    try:
        return safe_relative(path)
    except ValueError as exc:
        raise problem(
            status=422,
            code="RAW_PATH_INVALID",
            title="文件路径无效",
            detail="请从当前原始数据的目录中选择文件。",
        ) from exc


def _local_path(root: str | None, key: str) -> str | None:
    return f"{root.rstrip('/')}/{_safe_path(key)}" if root else None


def directory(
    browser: OriginalSourceBrowser,
    raw: RawSource,
    *,
    path: str = "",
    offset: int = 0,
    limit: int = 50,
    local_root: str | None = None,
) -> OriginalDirectory:
    prefix = f"{_safe_path(path)}/" if path else ""
    children: dict[str, OriginalDirectoryEntry] = {}
    count = size = 0
    direct_layout = True
    for file in browser.manifest(raw)["files"]:
        relative = _safe_path(file["path"])
        if not relative.startswith(prefix):
            continue
        count += 1
        size += file["size"]
        key = file.get("object_key") or f"{raw.storage_prefix}/{relative}"
        direct_layout &= key == f"{raw.storage_prefix}/{relative}"
        name, separator, _ = relative[len(prefix) :].partition("/")
        if separator:
            child = children.setdefault(
                name,
                OriginalDirectoryEntry(
                    name=name,
                    path=prefix + name,
                    kind="DIRECTORY",
                    size=0,
                    file_count=0,
                ),
            )
            child.size += file["size"]
            child.file_count += 1
        else:
            children[name] = OriginalDirectoryEntry(
                name=name,
                path=relative,
                kind="FILE",
                size=file["size"],
                file_count=1,
                sha256=file["sha256"],
                storage_key=key,
                local_path=_local_path(local_root, key),
                text_preview=PurePosixPath(relative).suffix.lower() in TEXT_SUFFIXES,
            )
    if path and not count:
        raise problem(
            status=404,
            code="RAW_DIRECTORY_NOT_FOUND",
            title="文件夹不存在",
            detail="此文件夹不在当前原始数据清单中。",
        )
    entries = sorted(children.values(), key=lambda item: (item.kind != "DIRECTORY", item.name))
    return OriginalDirectory(
        import_id=raw.raw_source_id,
        path=path,
        storage_prefix=raw.storage_prefix,
        local_path=_local_path(local_root, raw.storage_prefix + (f"/{path}" if path else ""))
        if direct_layout
        else None,
        entries=entries[offset : offset + limit],
        total=len(entries),
        offset=offset,
        file_count=count,
        total_bytes=size,
    )


def text_preview(browser: OriginalSourceBrowser, raw: RawSource, path: str) -> OriginalTextPreview:
    _safe_path(path)
    descriptor = next((f for f in browser.manifest(raw)["files"] if f["path"] == path), None)
    if descriptor is None:
        raise problem(
            status=404,
            code="RAW_OBJECT_NOT_FOUND",
            title="原文件不存在",
            detail="该路径不属于已提交的原始数据。",
        )
    if PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES:
        raise problem(
            status=415,
            code="RAW_TEXT_UNSUPPORTED",
            title="此文件不支持文本预览",
            detail="请下载原文件后使用相应工具打开。",
        )
    size = descriptor["size"]
    key = descriptor.get("object_key") or f"{raw.storage_prefix}/{path}"
    body = browser.storage.read_range(key, 0, min(size, TEXT_PREVIEW_BYTES)) if size else b""
    truncated = size > TEXT_PREVIEW_BYTES
    try:
        text = codecs.getincrementaldecoder("utf-8-sig")().decode(body, final=not truncated)
        if "\x00" in text:
            raise ValueError("binary content")
    except (UnicodeError, ValueError) as exc:
        raise problem(
            status=415,
            code="RAW_TEXT_ENCODING_UNSUPPORTED",
            title="无法按 UTF-8 预览",
            detail="请下载原文件查看；文件内容未做转换。",
        ) from exc
    return OriginalTextPreview(path=path, text=text, truncated=truncated, preview_bytes=len(body))

"""Stream selected committed sources as one ZIP without staging or converting bytes."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.core.errors import problem
from hc_data_platform.ingest.models import Identifier

from .local_files import safe_relative
from .source_browser import OriginalSourceBrowser


class OriginalArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=1000)


@dataclass(frozen=True)
class ArchiveFile:
    path: str
    key: str
    size: int
    sha256: str


def archive_files(
    browser: OriginalSourceBrowser,
    organization_id: str,
    project_id: str,
    region_code: str,
    command: OriginalArchiveRequest,
) -> tuple[ArchiveFile, ...]:
    """Authorize every batch before sending any bytes, including unprocessed sources."""
    files: list[ArchiveFile] = []
    paths: set[str] = set()
    for import_id in dict.fromkeys(command.import_ids):
        raw = browser.source(organization_id, project_id, region_code, import_id)
        try:
            root = safe_relative(raw.raw_source_id)
            for entry in browser.manifest(raw)["files"]:
                path = f"{root}/{safe_relative(entry['path'])}"
                size, checksum = entry["size"], entry["sha256"]
                if (
                    path in paths
                    or "\x00" in path
                    or not isinstance(size, int)
                    or size < 0
                    or not re.fullmatch(r"[0-9a-f]{64}", checksum)
                ):
                    raise ValueError("invalid original inventory")
                paths.add(path)
                files.append(
                    ArchiveFile(
                        path,
                        entry.get("object_key") or f"{raw.storage_prefix}/{entry['path']}",
                        size,
                        checksum,
                    )
                )
                if len(files) > 100_000:
                    raise problem(
                        status=422,
                        code="RAW_ARCHIVE_TOO_MANY_FILES",
                        title="所选文件过多",
                        detail="一次最多下载 100000 个原始文件，请减少所选批次。",
                    )
        except (ValueError, KeyError, TypeError) as exc:
            raise problem(
                status=409,
                code="RAW_ARCHIVE_INVALID",
                title="原始文件清单无效",
                detail="无法读取所选批次的原始文件清单，请刷新后重试。",
            ) from exc
    if not files:
        raise problem(
            status=422,
            code="RAW_ARCHIVE_EMPTY",
            title="没有可下载的原始文件",
            detail="请选择包含原始文件的批次。",
        )
    return tuple(files)


class _ZipBuffer(io.RawIOBase):
    """Non-seekable ZIP output drained after each bounded source chunk."""

    def __init__(self) -> None:
        super().__init__()
        self.pending = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        self.pending.extend(data)
        return len(data)

    def drain(self) -> bytes:
        data = bytes(self.pending)
        self.pending.clear()
        return data


def stream_archive(
    browser: OriginalSourceBrowser, files: tuple[ArchiveFile, ...]
) -> Iterator[bytes]:
    buffer = _ZipBuffer()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for file in files:
            entry = zipfile.ZipInfo(file.path)
            entry.external_attr = 0o100644 << 16
            entry.file_size = file.size
            checksum, size = hashlib.sha256(), 0
            with archive.open(entry, "w", force_zip64=True) as output:
                yield buffer.drain()
                chunks = iter(browser.storage.read_chunks(file.key, chunk_size=1024**2))
                try:
                    for chunk in chunks:
                        size += len(chunk)
                        if size > file.size:
                            raise ValueError("original file exceeds committed size")
                        checksum.update(chunk)
                        output.write(chunk)
                        yield buffer.drain()
                finally:
                    close = getattr(chunks, "close", None)
                    if close is not None:
                        close()
                if size != file.size or checksum.hexdigest() != file.sha256:
                    raise ValueError("original file differs from committed content")
            yield buffer.drain()
    yield buffer.drain()

"""Read an explicitly configured source ZIP without publishing its members."""

from __future__ import annotations

import io
import stat
import zipfile
from contextlib import contextmanager
from urllib.parse import quote, unquote, urlsplit

from hc_data_platform.core.context import current_request_context
from hc_data_platform.object_storage.models import ObjectMetadata
from hc_data_platform.storage.oss_reference import reference_key

MAX_MEMBER = 2 * 1024**3
MAX_TOTAL = 32 * 1024**3
BLOCK = 2 * 1024**2


class RangeFile(io.RawIOBase):
    def __init__(self, bucket, key, size, etag):
        self.bucket, self.key, self.size, self.etag = bucket, key, size, etag
        self.pos, self.start, self.block = 0, 0, b""

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        pos = (0, self.pos, self.size)[whence] + offset
        if pos < 0:
            raise OSError("negative ZIP offset")
        self.pos = pos
        return pos

    def read(self, size=-1):
        length = min(self.size - self.pos, size if size >= 0 else self.size)
        if length <= 0:
            return b""
        if length > 16 * 1024**2:
            raise ValueError("ZIP 目录或单次读取超过限制。")
        if not (self.start <= self.pos and self.pos + length <= self.start + len(self.block)):
            end = min(self.size, self.pos + max(length, BLOCK))
            response = self.bucket.get_object(
                self.key, byte_range=(self.pos, end - 1), headers={"If-Match": f'"{self.etag}"'}
            )
            try:
                self.block = response.read()
            finally:
                if close := getattr(response, "close", None):
                    close()
            if len(self.block) != end - self.pos:
                raise OSError("OSS byte range length mismatch")
            self.start = self.pos
        offset = self.pos - self.start
        self.pos += length
        return self.block[offset : offset + length]


def is_member(key):
    return key.startswith("oss://") and "#zip=" in key


class OssZipSources:
    def __init__(self, allowed, bucket_factory):
        self.allowed, self.bucket_factory = allowed, bucket_factory

    def allows(self, uri):
        ctx = current_request_context()
        scope = f"{ctx.organization_id}/{ctx.project_id}/{ctx.region_code}"
        return uri in self.allowed.get(scope, ())

    @contextmanager
    def opened(self, uri, expected=None):
        if not self.allows(uri):
            raise ValueError("该项目未配置此只读 OSS 压缩包。")
        bucket_name = urlsplit(uri).netloc
        key = reference_key(uri, bucket_name)
        bucket = self.bucket_factory(bucket_name)
        meta = bucket.get_object_meta(key)
        etag = str(meta.etag).strip('"')
        size = int(meta.headers["Content-Length"])
        if expected is not None and etag != expected:
            raise ValueError("源 ZIP 已变化，请重新检查。")
        if size > MAX_TOTAL:
            raise ValueError("源 ZIP 超过 32 GiB。")
        with RangeFile(bucket, key, size, etag) as reader, zipfile.ZipFile(reader) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_TOTAL:
                raise ValueError("ZIP 超出条目数或解压容量限制。")
            yield archive, etag, size

    def inventory(self, uri):
        with self.opened(uri) as (archive, etag, size):
            files, seen = [], set()
            for entry in archive.infolist():
                path = entry.orig_filename.rstrip("/")
                if (
                    not path
                    or path in seen
                    or any(c in path for c in ("\\", ":"))
                    or any(ord(c) < 32 for c in path)
                    or any(p in {"", ".", ".."} for p in path.split("/"))
                    or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFDIR, stat.S_IFREG)
                    or entry.flag_bits & 1
                ):
                    raise ValueError("ZIP 包含重复、不安全或加密的条目。")
                seen.add(path)
                if entry.is_dir():
                    continue
                if not path.lower().endswith((".mcap", ".takeovers.json")):
                    continue
                if not 0 < entry.file_size <= MAX_MEMBER:
                    raise ValueError("单条录制或配套标记必须大于 0 且不超过 2 GiB。")
                locator = f"{uri}#zip={quote(path, safe='')}&etag={quote(etag, safe='')}"
                files.append(dict(path=path, size=entry.file_size, object_key=locator, etag=etag))
            if not any(f["path"].lower().endswith(".mcap") for f in files):
                raise ValueError("只读录制压缩包中没有 MCAP 文件。")
            return dict(
                source_uri=uri,
                source_prefix=uri,
                archive_members=True,
                archive=dict(path=uri.rsplit("/", 1)[-1], object_key=uri, size=size, etag=etag),
                files=sorted(files, key=lambda f: f["path"]),
            )

    @contextmanager
    def member(self, locator):
        uri, suffix = locator.split("#zip=", 1)
        name, etag = (unquote(p) for p in suffix.rsplit("&etag=", 1))
        with self.opened(uri, etag) as (archive, _, _):
            info = archive.getinfo(name)
            if not 0 < info.file_size <= MAX_MEMBER or info.is_dir():
                raise ValueError("无效的 ZIP 成员。")
            yield archive, info, etag

    def head(self, locator):
        with self.member(locator) as (_, info, etag):
            return ObjectMetadata(key=locator, size=info.file_size, etag=etag, crc64=None)

    def chunks(self, locator, chunk_size, start=0, end=None):
        with self.member(locator) as (archive, info, _), archive.open(info) as src:
            remaining = (info.file_size if end is None else min(end, info.file_size)) - start
            if start:
                src.seek(start)
            while remaining > 0:
                chunk = src.read(min(remaining, chunk_size))
                if not chunk:
                    raise OSError("unexpected end of ZIP member")
                yield chunk
                remaining -= len(chunk)

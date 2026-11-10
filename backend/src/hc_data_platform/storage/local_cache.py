"""Local, expiring storage for regenerable previews and delivery archives."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlencode

PREVIEW_CACHE_KINDS = frozenset({"recording-previews", "episode-previews", "episode-deliveries"})
CACHE_KINDS = PREVIEW_CACHE_KINDS | {"derived-mcap"}


def processing_cache_key(key):
    parts = key.split("/")
    if len(parts) < 7 or parts[0] != "derived-mcap":
        return False
    from hc_data_platform.continuous_recordings.derived_cache import evictable

    return bool(re.fullmatch("[a-f0-9]{64}", parts[5])) and evictable("/".join(parts[6:]))


class CacheError(RuntimeError):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code, self.message = status, code, code


class CacheBody:
    def __init__(self, stream, meta, byte_range=None):
        self.stream = stream
        start, end = byte_range or (0, meta["size"] - 1)
        start = start or 0
        end = meta["size"] - 1 if end is None else min(end, meta["size"] - 1)
        self.stream.seek(start)
        self.remaining = max(0, end - start + 1)
        self.content_length = self.remaining
        self.headers = {"content-length": str(self.content_length)}

    def read(self, size=-1):
        value = self.stream.read(self.remaining if size < 0 else min(size, self.remaining))
        self.remaining -= len(value)
        return value

    def close(self):
        self.stream.close()


class LocalCacheBucket:
    def __init__(self, root, signing_key, *, ttl_days=None, clock=time.time):
        self.root = Path(root).resolve()
        self.signing_key = signing_key.encode()
        self.ttl = None if ttl_days is None else ttl_days * 86400
        self.clock = clock
        self._last_sweep = 0.0
        self.root.mkdir(parents=True, exist_ok=True)

    def identity(self, key):
        parts = key.split("/")
        if (
            parts[0] not in CACHE_KINDS
            or (parts[0] == "derived-mcap" and not processing_cache_key(key))
            or any(p in {"", ".", ".."} for p in parts)
            or "\\" in key
            or "\x00" in key
        ):
            raise ValueError("invalid local cache key")
        return parts[0], hashlib.sha256(key.encode()).hexdigest()

    def directory(self, key):
        kind, identity = self.identity(key)
        return self.root / kind / identity

    @contextmanager
    def lock(self, key):
        kind, identity = self.identity(key)
        locks = self.root / ".locks" / kind
        locks.mkdir(parents=True, exist_ok=True)
        with (locks / identity).open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def rebuild_lock(self, key):
        kind, identity = self.identity(key)
        directory = self.root / ".rebuild" / kind
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / identity).open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def metadata(self, key):
        try:
            data = json.loads((self.directory(key) / "meta.json").read_text())
            if data["key"] != key or not (self.directory(key) / "payload").is_file():
                raise CacheError(404, "NoSuchKey")
            return data
        except FileNotFoundError:
            raise CacheError(404, "NoSuchKey") from None

    def head_object(self, key):
        with self.lock(key):
            meta = self.metadata(key)
            return SimpleNamespace(
                content_length=meta["size"],
                etag=meta["etag"],
                last_modified=meta["modified"],
                headers={
                    **meta["headers"],
                    "content-length": str(meta["size"]),
                    "etag": meta["etag"],
                    "x-oss-meta-sha256": meta["sha256"],
                },
            )

    get_object_meta = head_object

    def get_object(self, key, *, byte_range=None, **kwargs):
        with self.lock(key):
            meta = self.metadata(key)
            directory = self.directory(key)
            body = CacheBody((directory / "payload").open("rb"), meta, byte_range)
            os.utime(directory / "meta.json", (self.clock(), self.clock()))
            return body

    def put_object(self, key, body, headers=None):
        self.sweep()
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        directory = self.directory(key)
        directory.parent.mkdir(parents=True, exist_ok=True)
        stream = io.BytesIO(body) if isinstance(body, (bytes, bytearray)) else body
        with (
            self.lock(key),
            tempfile.TemporaryDirectory(prefix=".write-", dir=directory.parent) as temp,
        ):
            staging = Path(temp)
            md5, sha, size = hashlib.md5(), hashlib.sha256(), 0
            with (staging / "payload").open("wb") as output:
                while block := stream.read(1024**2):
                    output.write(block)
                    md5.update(block)
                    sha.update(block)
                    size += len(block)
                output.flush()
                os.fsync(output.fileno())
            from hc_data_platform.core.context import current_request_context

            try:
                context = current_request_context()
                scope = [context.organization_id, context.project_id, context.region_code]
            except RuntimeError:
                scope = None
            meta = dict(
                scope=scope,
                key=key,
                size=size,
                etag=md5.hexdigest(),
                sha256=sha.hexdigest(),
                modified=self.clock(),
                headers=headers,
            )
            if directory.exists():
                if headers.get("x-oss-forbid-overwrite") == "true":
                    raise CacheError(409, "FileAlreadyExists")
                shutil.rmtree(directory)
            (staging / "meta.json").write_text(json.dumps(meta))
            os.utime(staging / "meta.json", (self.clock(), self.clock()))
            staging.rename(directory)
            return SimpleNamespace(etag=meta["etag"], headers={})

    def delete_object(self, key):
        with self.lock(key):
            directory = self.directory(key)
            if directory.exists():
                shutil.rmtree(directory)
        return SimpleNamespace(request_id="local-cache")

    def upload_directory(self, key, upload_id):
        self.identity(key)
        if not re.fullmatch("[a-f0-9]{32}", upload_id):
            raise ValueError("invalid upload ID")
        directory = self.root / ".uploads" / upload_id
        try:
            saved = json.loads((directory / "owner.json").read_text())
        except FileNotFoundError:
            raise CacheError(404, "NoSuchUpload") from None
        if saved["key"] != key:
            raise CacheError(403, "AccessDenied")
        return directory

    def init_multipart_upload(self, key, headers=None):
        self.identity(key)
        upload = uuid.uuid4().hex
        directory = self.root / ".uploads" / upload
        directory.mkdir(parents=True)
        (directory / "owner.json").write_text(json.dumps({"key": key, "headers": headers or {}}))
        return SimpleNamespace(upload_id=upload)

    def upload_part(self, key, upload_id, part_number, body, headers=None):
        if not 1 <= int(part_number) <= 10000:
            raise ValueError("invalid part number")
        directory = self.upload_directory(key, upload_id)
        part = directory / str(int(part_number))
        stream = io.BytesIO(body) if isinstance(body, bytes) else body
        digest = hashlib.md5()
        size = 0
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            temp = Path(output.name)
            try:
                while block := stream.read(1024**2):
                    output.write(block)
                    digest.update(block)
                    size += len(block)
            except BaseException:
                temp.unlink(missing_ok=True)
                raise
        expected = (headers or {}).get("Content-Length")
        if expected is not None and size != int(expected):
            temp.unlink(missing_ok=True)
            raise ValueError("part length differs")
        temp.replace(part)
        return SimpleNamespace(etag=digest.hexdigest(), headers={})

    def list_parts(self, key, upload_id, **kwargs):
        directory = self.upload_directory(key, upload_id)
        parts = []
        for path in sorted(
            (p for p in directory.iterdir() if p.name.isdigit()), key=lambda p: int(p.name)
        ):
            digest = hashlib.md5()
            with path.open("rb") as stream:
                while block := stream.read(1024**2):
                    digest.update(block)
            parts.append(
                SimpleNamespace(
                    part_number=int(path.name),
                    size=path.stat().st_size,
                    etag=digest.hexdigest(),
                    headers={},
                )
            )
        return SimpleNamespace(parts=parts, is_truncated=False, next_part_number_marker="")

    def complete_multipart_upload(self, key, upload_id, parts, headers=None):
        directory = self.upload_directory(key, upload_id)
        found = self.list_parts(key, upload_id).parts
        if [(p.part_number, p.etag.strip('"')) for p in parts] != [
            (p.part_number, p.etag) for p in found
        ]:
            raise ValueError("multipart inventory differs")
        with tempfile.TemporaryFile(dir=directory) as joined:
            for part in found:
                with (directory / str(part.part_number)).open("rb") as source:
                    shutil.copyfileobj(source, joined, 1024**2)
            joined.seek(0)
            result = self.put_object(key, joined, headers=headers)
        shutil.rmtree(directory)
        return result

    def abort_multipart_upload(self, key, upload_id):
        try:
            directory = self.upload_directory(key, upload_id)
        except CacheError as exc:
            if exc.status == 404:
                return SimpleNamespace(request_id="local-cache")
            raise
        shutil.rmtree(directory)
        return SimpleNamespace(request_id="local-cache")

    def signature(self, kind, identity, expires, name=""):
        return hmac.new(
            self.signing_key,
            (f"{kind}/{identity}:{expires}" + (":" + name if name else "")).encode(),
            hashlib.sha256,
        ).hexdigest()

    def sign_url(self, method, key, expires, **kwargs):
        if method.upper() not in {"GET", "HEAD"}:
            raise ValueError("local cache upload is server-side only")
        if not 1 <= expires <= 86400:
            raise ValueError("invalid cache URL lifetime")
        self.sweep()
        with self.lock(key):
            self.metadata(key)
            os.utime(self.directory(key) / "meta.json", (self.clock(), self.clock()))
        kind, identity = self.identity(key)
        expiry = int(self.clock()) + expires
        disposition = (kwargs.get("params") or {}).get("response-content-disposition", "")
        name = (
            unquote(disposition.split("filename*=UTF-8''", 1)[1])
            if "filename*=UTF-8''" in disposition
            else ""
        )
        if len(name) > 240 or any(char in name for char in "\r\n/\\"):
            raise ValueError("invalid download filename")
        return f"/api/v1/local-cache/{kind}/{identity}?" + urlencode(
            {
                "expires": expiry,
                "signature": self.signature(kind, identity, expiry, name),
                **({"name": name} if name else {}),
            }
        )

    def signed_file(self, kind, identity, expires, signature, name=""):
        if kind not in CACHE_KINDS or not re.fullmatch("[a-f0-9]{64}", identity):
            raise CacheError(404, "NoSuchKey")
        if (
            not re.fullmatch("[a-f0-9]{64}", signature)
            or expires < int(self.clock())
            or not hmac.compare_digest(signature, self.signature(kind, identity, expires, name))
        ):
            raise CacheError(403, "AccessDenied")
        directory = self.root / kind / identity
        try:
            meta = json.loads((directory / "meta.json").read_text())
        except FileNotFoundError:
            raise CacheError(404, "NoSuchKey") from None
        if self.identity(meta["key"]) != (kind, identity):
            raise CacheError(403, "AccessDenied")
        with self.lock(meta["key"]):
            self.metadata(meta["key"])
            os.utime(directory / "meta.json", (self.clock(), self.clock()))
            return directory / "payload", meta

    def listing(self, prefix=""):
        self.sweep()
        items = []
        for kind in sorted(CACHE_KINDS):
            if prefix and not (
                prefix.startswith(kind + "/") or kind.startswith(prefix) or prefix == kind
            ):
                continue
            for path in (self.root / kind).glob("*/meta.json"):
                try:
                    meta = json.loads(path.read_text())
                except FileNotFoundError:
                    continue
                if meta["key"].startswith(prefix):
                    items.append(
                        SimpleNamespace(
                            key=meta["key"],
                            size=meta["size"],
                            etag=meta["etag"],
                            last_modified=meta["modified"],
                        )
                    )
        return sorted(items, key=lambda item: item.key)

    def sweep(self, *, force=False):
        if self.ttl is None:
            return 0
        now = self.clock()
        if not force and now - self._last_sweep < 3600:
            return 0
        self._last_sweep = now
        removed = 0
        for kind in PREVIEW_CACHE_KINDS:
            for path in (self.root / kind).glob("*/meta.json"):
                try:
                    if now - path.stat().st_mtime < self.ttl:
                        continue
                    meta = json.loads(path.read_text())
                    directory = self.directory(meta["key"])
                    if directory != path.parent:
                        raise ValueError("cache metadata owner mismatch")
                    with self.lock(meta["key"]):
                        if now - path.stat().st_mtime >= self.ttl:
                            shutil.rmtree(directory)
                            removed += 1
                except FileNotFoundError:
                    continue
        return removed


class CacheRoutingBucket:
    def __init__(self, remote, cache, *, local_processing=False):
        self.remote, self.cache = remote, cache
        self.local_processing = local_processing
        self.bucket_name, self.key_prefix = remote.bucket_name, remote.key_prefix

    def local_key(self, key):
        if key.startswith("oss://"):
            from .oss_reference import reference_key

            value = reference_key(key, self.bucket_name, self.key_prefix)[len(self.key_prefix) :]
            if self.remote.key_layout is not None:
                value = self.remote.key_layout.logical(value)
        else:
            value = key
        if processing_cache_key(value):
            return value if self.local_processing else None
        return value if value.split("/", 1)[0] in PREVIEW_CACHE_KINDS else None

    def __getattr__(self, name):
        from .oss_prefix import PrefixedOssBucket

        if name not in PrefixedOssBucket._OBJECT_METHODS:
            return getattr(self.remote, name)

        def invoke(key, *args, **kwargs):
            local = self.local_key(key)
            return (
                getattr(self.cache, name)(local, *args, **kwargs)
                if local
                else getattr(self.remote, name)(key, *args, **kwargs)
            )

        return invoke

    def sign_url(self, method, key, *args, **kwargs):
        local = self.local_key(key)
        return (
            self.cache.sign_url(method, local, *args, **kwargs)
            if local
            else self.remote.sign_url(method, key, *args, **kwargs)
        )

    def copy_object(self, bucket, source, target, **kwargs):
        source_local, target_local = self.local_key(source), self.local_key(target)
        if not source_local and not target_local:
            return self.remote.copy_object(bucket, source, target, **kwargs)
        if bucket != self.bucket_name or not (source_local and target_local):
            raise ValueError("cache copies must remain within local cache storage")
        source_body = self.cache.get_object(source_local)
        try:
            return self.cache.put_object(
                target_local, source_body, headers={"x-oss-forbid-overwrite": "true"}
            )
        finally:
            source_body.close()

    def list_objects_v2(self, prefix="", **kwargs):
        self.cache.sweep()
        cursor = kwargs.get("continuation_token") or ""
        if prefix.split("/", 1)[0] in PREVIEW_CACHE_KINDS or cursor.startswith("hc-local:"):
            items = self.cache.listing(prefix)
            token = cursor.removeprefix("hc-local:") or "0"
            if not re.fullmatch("[0-9]+", token):
                raise ValueError("invalid local cache cursor")
            offset = int(token)
            limit = max(1, min(int(kwargs.get("max_keys", 1000)), 1000))
            page = items[offset : offset + limit]
            more = offset + limit < len(items)
            return SimpleNamespace(
                object_list=page,
                prefix_list=[],
                is_truncated=more,
                next_continuation_token="hc-local:" + str(offset + limit) if more else "",
            )
        page = self.remote.list_objects_v2(prefix=prefix, **kwargs)
        page.object_list = [item for item in page.object_list if not self.local_key(item.key)]
        if not page.is_truncated and self.cache.listing(prefix):
            page.is_truncated = True
            page.next_continuation_token = "hc-local:0"
        return page


def local_cache_for(storage):
    bucket = getattr(storage, "_bucket", None)
    return (
        bucket.cache
        if isinstance(bucket, CacheRoutingBucket)
        else getattr(getattr(storage, "_client", None), "lifecycle_cache", None)
    )


def remote_inventory_client(client):
    bucket = getattr(client, "_bucket", None)
    if not isinstance(bucket, CacheRoutingBucket):
        return client
    from .oss_client import OssBotoCompatClient

    return OssBotoCompatClient(bucket.remote)

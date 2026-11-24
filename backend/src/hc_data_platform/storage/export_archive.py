"""Publish a ZIP stream to the platform's persistent OSS namespace."""

import hashlib
import json
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from contextvars import copy_context
from tempfile import SpooledTemporaryFile
from uuid import uuid4

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from hc_data_platform.core.errors import problem
from hc_data_platform.object_storage.checksums import crc64_ecma
from hc_data_platform.object_storage.models import CompletedPart

logger = logging.getLogger(__name__)
PART_SIZE = 16 * 1024**2


def publish_archive(storage, scope, name, chunks, *, item_count=1, expected_sha256=None, on_complete=None):
    uri_for = getattr(storage, "persistent_oss_uri", None)
    if uri_for is None:
        raise problem(
            status=503,
            code="OSS_EXPORT_UNAVAILABLE",
            title="OSS 导出不可用",
            detail="当前平台尚未配置 OSS 存储。",
        )
    if any(
        not part or part in {".", ".."} or "/" in part or "\\" in part for part in (*scope, name)
    ):
        raise ValueError("Invalid export path")
    key = "/".join(("exports", *scope, uuid4().hex, name))
    uri = uri_for(key)
    events = upload_events(storage, key, uri, chunks, expected_sha256, on_complete, item_count=item_count)
    return StreamingResponse(
        events,
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        background=BackgroundTask(events.close),
    )


def event(**fields):
    return json.dumps(fields, ensure_ascii=False) + "\n"


def upload_events(storage, key, uri, chunks, expected_sha256=None, on_complete=None, *, item_count=1):
    upload = None
    try:
        workers = 4 if item_count > 50 else 1
        with closing(chunks), ExitStack() as stack:
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=workers))
            part = SpooledTemporaryFile(max_size=PART_SIZE)
            stack.callback(lambda: part.close())
            pending = deque()
            submitted = 0
            upload = storage.create_multipart(key)
            parts = []
            total = 0
            crc = 0
            checksum = hashlib.sha256() if expected_sha256 else None

            def send(number, stream, size):
                with stream:
                    result = storage.upload_part_stream(key, upload, number, stream, size)
                    return CompletedPart(number, result.etag), size

            def submit():
                nonlocal part, submitted
                size = part.tell()
                part.seek(0)
                submitted += 1
                stream = part
                part = SpooledTemporaryFile(max_size=PART_SIZE)
                try:
                    pending.append(pool.submit(copy_context().run, send, submitted, stream, size))
                except BaseException:
                    stream.close()
                    raise

            def collect():
                nonlocal total
                receipt, size = pending.popleft().result()
                parts.append(receipt)
                total += size
                return event(uploaded_bytes=total)

            for chunk in chunks:
                crc = crc64_ecma(chunk, crc)
                if checksum is not None:
                    checksum.update(chunk)
                view = memoryview(chunk)
                while view:
                    # Increase part sizes for large archives within OSS's 10,000-part limit.
                    limit = min(PART_SIZE << (submitted // 1000), 5 * 1024**3)
                    length = min(len(view), limit - part.tell())
                    part.write(view[:length])
                    view = view[length:]
                    if part.tell() == limit:
                        submit()
                        if len(pending) >= workers:
                            yield collect()
            if checksum is not None and checksum.hexdigest() != expected_sha256:
                raise ValueError("Archive checksum mismatch")
            if part.tell():
                submit()
            while pending:
                yield collect()
            metadata = storage.complete_multipart(key, upload, parts)
            upload = None
            if metadata.size != total or (metadata.crc64 is not None and metadata.crc64 != crc):
                storage.delete_object(key)
                raise ValueError("Uploaded archive integrity mismatch")
            if on_complete:
                on_complete()
            yield event(uploaded_bytes=total, oss_uri=uri)
    except Exception:
        logger.exception("OSS archive export failed for %s", key)
        yield event(error="写入 OSS 未完成，请重试。")
    finally:
        if upload is not None:
            storage.abort_multipart(key, upload)

"""Recreate evicted archives from frozen plans without mutating READY jobs."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from .collection_delivery import CollectionDelivery
from .collection_delivery_models import CollectionDeliveryPlan
from .delivery_repository import failure, public_job, scope_key
from .errors import PackageConflict

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="delivery-cache")
_guard = Lock()
_jobs = {}


def cache_for(service):
    from hc_data_platform.storage.local_cache import local_cache_for
    return local_cache_for(service.storage)


def ensure_cached(service, row):
    cache = cache_for(service)
    if cache is None or service.storage.head(row["artifact_key"]) is not None:
        return
    identity = (str(cache.root), *scope_key(), row["job_id"])
    with _guard:
        task = _jobs.get(identity)
        if task is not None and task.done():
            del _jobs[identity]
            if task.exception() is not None:
                raise failure(
                    "CACHE_REBUILD_FAILED",
                    "缓存重建未完成，请重试；原始数据和导出记录仍保留。",
                    503,
                )
            if service.storage.head(row["artifact_key"]) is not None:
                return
        if len(_jobs) >= 8 and identity not in _jobs:
            # Drop completed entries first; bound both memory and queued rebuilds.
            for key in list(_jobs):
                if _jobs[key].done():
                    del _jobs[key]
            if len(_jobs) >= 8:
                raise failure("CACHE_REBUILDING", "本地导出缓存正在排队生成，请稍候。")
        if identity not in _jobs:
            _jobs[identity] = _pool.submit(copy_context().run, rebuild_archive, service, dict(row))
    raise failure("CACHE_REBUILDING", "本地导出包已过期，正在按原数据版本重新生成。")


class FrozenProgress:
    def phase(self, *args):
        pass

    def progress(self, *args):
        pass

    def pin_totals(self, row, files, size):
        if files != row["total_files"] or size != row["total_bytes"]:
            raise PackageConflict("rebuilt delivery inventory differs from frozen totals")


def rebuild_archive(service, row):
    from .delivery import archive_packages

    cache = cache_for(service)
    if cache is None:
        raise ValueError("delivery cache is not configured")
    plan = public_job(row).plan
    # Separate lock from payload lock: readers and immutable writers use that lock.
    with cache.rebuild_lock(row["artifact_key"]):
        if service.storage.head(row["artifact_key"]) is not None:
            service._verify_artifact(row)
            return
        for member in plan.members:
            if member.data.identity.scope != scope_key():
                raise PermissionError("cache rebuild is outside the authorized scope")
        directory = service.packages.directory / "complete-deliveries"
        directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="cache-", dir=directory) as temp:
            archive = Path(temp) / "delivery.zip"
            owner = copy(service)
            owner.repository = FrozenProgress()
            if isinstance(plan, CollectionDeliveryPlan):
                checksum, size = CollectionDelivery(owner).build(
                    plan, Path(temp), archive, row, lambda: None
                )
            else:
                checksum, size = archive_packages(
                    service.packages,
                    plan,
                    archive,
                    lambda: None,
                    lambda *_: None,
                    pin_totals=lambda n, size: owner.repository.pin_totals(row, n, size),
                )
            if checksum != row["artifact_sha256"] or size != row["size_bytes"]:
                raise PackageConflict("rebuilt archive differs from the original delivery")
            with archive.open("rb") as source:
                service.writer.write_immutable(
                    row["artifact_key"], source, size=size, sha256=checksum
                )
        service._verify_artifact(row)

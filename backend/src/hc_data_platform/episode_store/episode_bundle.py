"""Prepare individual archives for a scoped, quality-checked ZIP download."""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import closing, nullcontext
from contextvars import copy_context
from copy import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from hc_data_platform.lerobot_imports.source_archive import ArchiveFile

from .collection_delivery import CollectionDelivery
from .collection_records import CollectionRecord
from .collection_delivery_models import CollectionDeliveryPlan, RequestCollectionDelivery
from .delivery_cache import cache_for
from .delivery_models import PreparedEpisodePart, RequestEpisodeDelivery
from .delivery_repository import SCOPE, failure, scope_key
from .files import PackageFiles, VerifiedFileCache
from .models import DataVersionRef, digest

PART_WORKERS = 6
_pool = ThreadPoolExecutor(max_workers=PART_WORKERS, thread_name_prefix="episode-part")
_guard = Lock()
_active = {}


def prefix():
    return f"episode-deliveries/{digest(scope_key())}/parts"


def check_filter(selection):
    if selection.stage not in {"qc_passed", "unannotated"}:
        raise ValueError("请选择质检通过且已入库的数据。")


def read_part(service, part_id):
    key = f"{prefix()}/{part_id}.json"
    meta = service.storage.head(key)
    if meta is None:
        return None
    if meta.size > 65536:
        raise ValueError("无效的 Episode 缓存记录。")
    info = json.loads(b"".join(service.storage.read_chunks(key)))
    if info["part_id"] != part_id or not info["key"].startswith(f"episode-deliveries/{digest(scope_key())}/"):
        raise ValueError("缓存超出所选范围。")
    artifact = service.storage.head(info["key"])
    return info if artifact and artifact.size == info["size"] else None


def freeze(service, body, auth):
    if body.mode == "independent":
        service.verify_export_filter(body.export_filter, [body.data], auth)
        return service._freeze(RequestEpisodeDelivery(data=(body.data,), export_filter=body.export_filter))
    lerobot = body.mode in {"rgb", "rgbd"}
    request = RequestCollectionDelivery(members=({"data": body.data},),
                                        output_format="lerobot" if lerobot else body.mode,
                                        modalities=body.mode if lerobot else "rgbd", export_filter=body.export_filter)
    return service.collection.freeze(request, auth)[0]


class PartProgress:
    def phase(self, *args):
        pass

    def progress(self, *args):
        pass

    def pin_totals(self, *args):
        pass


def existing_delivery(service, plan):
    job_ids = [plan.job_id]
    if not isinstance(plan, CollectionDeliveryPlan):
        member = plan.members[0].model_copy(update={"root": "episodes/000000"})
        batch = plan.model_copy(update={"members": (member,), "archive_format": "independent_episode_batch_zip/1"})
        job_ids.append(batch.job_id)
    with service.repository.transaction() as cursor:
        cursor.execute(f"""SELECT artifact_key,artifact_sha256,size_bytes
                           FROM episode_store.delivery_jobs WHERE {SCOPE} AND job_id=ANY(%s)
                           AND status='READY' ORDER BY updated_at DESC""", (*scope_key(), job_ids))
        rows = cursor.fetchall()
    for key, checksum, size in rows:
        head = service.storage.head(key)
        if head and head.size == size:
            return key, checksum, size
    return None


def build_part(service, plan, part_id):
    from .delivery import archive_packages

    key = f"{prefix()}/{part_id}.zip"
    cache = cache_for(service)
    with cache.rebuild_lock(key) if cache else nullcontext():
        if read_part(service, part_id):
            return True
        found = existing_delivery(service, plan)
        reused = True
        if found:
            key, checksum, size = found
        elif (head := service.storage.head(key)) is not None:
            checksum_hash = hashlib.sha256()
            for chunk in service.storage.read_chunks(key):
                checksum_hash.update(chunk)
            checksum, size = checksum_hash.hexdigest(), head.size
        else:
            reused = False
            directory = cache.root / ".work" if cache else service.packages.directory / "complete-deliveries"
            directory.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="episode-", dir=directory) as temp:
                root = Path(temp)
                archive = root / "episode.zip"
                # A reader holds its lock throughout video decoding. Give each build
                # its own bounded reader so Episodes can encode concurrently.
                packages = copy(service.packages)
                limits = packages.files.cache
                with closing(VerifiedFileCache(root / "reader", capacity_bytes=limits.capacity,
                                               max_object_bytes=limits.max_object)) as reader:
                    packages.files = PackageFiles(packages.files.repository, service.storage, reader)
                    if isinstance(plan, CollectionDeliveryPlan):
                        owner = copy(service)
                        owner.packages = packages
                        owner.repository = PartProgress()
                        checksum, size = CollectionDelivery(owner).build(
                            plan, root, archive, {}, lambda: None, cache_receipt=cache is not None
                        )
                    else:
                        checksum, size = archive_packages(packages, plan, archive, lambda: None, lambda *_: None)
                with archive.open("rb") as stream:
                    if cache:
                        headers = {"content-length": str(size), "x-oss-forbid-overwrite": "true"}
                        if checksum is not None:
                            headers["x-oss-meta-sha256"] = checksum
                        receipt = cache.put_object(key, stream, headers=headers)
                        checksum = receipt.headers["x-oss-meta-sha256"]
                    else:
                        service.writer.write_immutable(key, stream, size=size, sha256=checksum)
        selected = plan.members[0].collection_record
        record_hash = None
        if selected is not None:
            document = selected.model_dump(mode="json")
            record_hash = digest(document)
            service.storage.put_json(f"{prefix()}/{part_id}.record.json", document, if_none_match=False)
        service.storage.put_json(f"{prefix()}/{part_id}.json", {
            "part_id": part_id, "data": plan.members[0].data.model_dump(mode="json"),
            "key": key, "sha256": checksum, "size": size, "record_sha256": record_hash,
        }, if_none_match=False)
        return reused


def prepare_part(service, body, auth):
    from .files import reuse_export_reads

    service.authorize(auth, command=True)
    check_filter(body.export_filter)
    with reuse_export_reads(body.data.package_prefix, service.packages.files.cache):
        if (processes := getattr(service, "part_processes", None)) is not None:
            return processes.prepare(service, body, auth)
        return _prepare_authorized_part(service, body, auth)


def cached_part(service, plan, part_id):
    if read_part(service, part_id):
        return PreparedEpisodePart(part_id=part_id, status="ready", reused=True)
    # Both encodings satisfy the same MCAP delivery contract. Keep the existing
    # archive under its own identity when every other frozen field still matches.
    if isinstance(plan, CollectionDeliveryPlan) and plan.archive_format == "mcap_collection_zip/9":
        older = plan.model_copy(update={"archive_format": "mcap_collection_zip/8"})
        old_id = digest(older.model_dump(mode="json"))
        if read_part(service, old_id):
            return PreparedEpisodePart(part_id=old_id, status="ready", reused=True)
    return None


def _prepare_authorized_part(service, body, auth):
    plan = freeze(service, body, auth)
    part_id = digest(plan.model_dump(mode="json"))
    identity = (scope_key(), part_id)
    with _guard:
        future = _active.get(identity)
        if future and future.done():
            del _active[identity]
            return PreparedEpisodePart(part_id=part_id, status="ready", reused=future.result())
    if ready := cached_part(service, plan, part_id):
        return ready
    with _guard:
        for other, future in list(_active.items()):
            if other != identity and future.done():
                del _active[other]
        future = _active.get(identity)
        if future is None and len(_active) < PART_WORKERS:
            future = _active[identity] = _pool.submit(copy_context().run, build_part, service, plan, part_id)
        else:
            future = None
    # Only the request that submitted work waits for it.
    if future is not None and wait([future], timeout=20).done:
        with _guard:
            if _active.get(identity) is future:
                del _active[identity]
        return PreparedEpisodePart(part_id=part_id, status="ready", reused=future.result())
    return PreparedEpisodePart(part_id=part_id, status="preparing", reused=False)


def bundle_files(service, body, auth, *, records=None, versions=None):
    check_filter(body.export_filter)
    parts, refs, seen = [], [], set()
    for part_id in body.part_ids:
        info = read_part(service, part_id)
        if not info:
            raise failure("CACHE_MISSING", "部分 Episode 缓存已清理，请重新点击导出。")
        data = DataVersionRef.model_validate(info["data"])
        if data.identity.scope != scope_key() or data.identity.key in seen:
            raise ValueError("Episode 范围或选择重复。")
        service.require_download(auth, data)
        seen.add(data.identity.key)
        refs.append(data)
        label = re.sub(r"[^\w.-]", "_", data.identity.episode_id)[:96]
        path = f"{len(parts)+1:06d}-{label}.zip"
        parts.append(ArchiveFile(path, info["key"], info["size"], info["sha256"]))
        if versions is not None:
            versions[path] = data.data_version_id
        if records is not None and info.get("record_sha256"):
            document = json.loads(b"".join(service.storage.read_chunks(f"{prefix()}/{part_id}.record.json")))
            if digest(document) != info["record_sha256"]:
                raise failure("INTEGRITY_FAILED", "缓存中的采集记录与导出时的记录不符。")
            records[path] = CollectionRecord.model_validate(document)
    for start in range(0, len(refs), 100):
        service.verify_export_filter(body.export_filter, refs[start:start+100], auth)
    return tuple(parts)

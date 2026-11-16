"""Prepare individual archives for a scoped, quality-checked ZIP download."""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
from copy import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from hc_data_platform.lerobot_imports.source_archive import ArchiveFile

from .collection_delivery import CollectionDelivery
from .collection_delivery_models import CollectionDeliveryPlan, RequestCollectionDelivery
from .delivery_cache import cache_for
from .delivery_models import PreparedEpisodePart, RequestEpisodeDelivery
from .delivery_repository import SCOPE, failure, scope_key
from .models import DataVersionRef, digest

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="episode-part")
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
        return service._freeze(RequestEpisodeDelivery(data=(body.data,)))
    lerobot = body.mode in {"rgb", "rgbd"}
    request = RequestCollectionDelivery(members=({"data": body.data},),
                                        output_format="lerobot" if lerobot else body.mode,
                                        modalities=body.mode if lerobot else "rgbd")
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
            directory = service.packages.directory / "complete-deliveries"
            directory.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="episode-", dir=directory) as temp:
                archive = Path(temp) / "episode.zip"
                if isinstance(plan, CollectionDeliveryPlan):
                    owner = copy(service)
                    owner.repository = PartProgress()
                    checksum, size = CollectionDelivery(owner).build(plan, Path(temp), archive, {}, lambda: None)
                else:
                    checksum, size = archive_packages(service.packages, plan, archive, lambda: None, lambda *_: None)
                with archive.open("rb") as stream:
                    service.writer.write_immutable(key, stream, size=size, sha256=checksum)
        service.storage.put_json(f"{prefix()}/{part_id}.json", {
            "part_id": part_id, "data": plan.members[0].data.model_dump(mode="json"),
            "key": key, "sha256": checksum, "size": size,
        }, if_none_match=False)
        return reused


def prepare_part(service, body, auth):
    service.authorize(auth, command=True)
    check_filter(body.export_filter)
    service.verify_export_filter(body.export_filter, [body.data], auth)
    plan = freeze(service, body, auth)
    part_id = digest(plan.model_dump(mode="json"))
    identity = (scope_key(), part_id)
    with _guard:
        future = _active.get(identity)
        if future and future.done():
            del _active[identity]
            return PreparedEpisodePart(part_id=part_id, status="ready", reused=future.result())
    if read_part(service, part_id):
        return PreparedEpisodePart(part_id=part_id, status="ready", reused=True)
    with _guard:
        for other, future in list(_active.items()):
            if other != identity and future.done():
                del _active[other]
        if not _active:
            _active[identity] = _pool.submit(copy_context().run, build_part, service, plan, part_id)
    return PreparedEpisodePart(part_id=part_id, status="preparing", reused=False)


def bundle_files(service, body, auth):
    service.authorize(auth)
    check_filter(body.export_filter)
    parts, refs, seen = [], [], set()
    for part_id in body.part_ids:
        info = read_part(service, part_id)
        if not info:
            raise failure("CACHE_MISSING", "部分 Episode 缓存已清理，请重新点击导出。")
        data = DataVersionRef.model_validate(info["data"])
        if data.identity.scope != scope_key() or data.identity.key in seen:
            raise ValueError("Episode 范围或选择重复。")
        seen.add(data.identity.key)
        refs.append(data)
        label = re.sub(r"[^\w.-]", "_", data.identity.episode_id)[:96]
        parts.append(ArchiveFile(f"{len(parts)+1:06d}-{label}.zip", info["key"], info["size"], info["sha256"]))
    for start in range(0, len(refs), 100):
        service.verify_export_filter(body.export_filter, refs[start:start+100], auth)
    return tuple(parts)

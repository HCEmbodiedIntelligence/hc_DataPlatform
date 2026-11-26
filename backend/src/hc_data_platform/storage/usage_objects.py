"""Index one OSS listing and the routed cache for a capacity observation."""
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timezone
from itertools import islice
from urllib.parse import unquote, urlsplit

from .inventory import ProviderInventoryObject
from .local_cache import CacheRoutingBucket


class UsageObjectIndex:
    def __init__(self, objects):
        self.objects = {obj.object_key: obj for obj in objects}
        self.keys = sorted(self.objects)

    def head(self, key):
        return self.objects.get(key)

    def list_prefix(self, prefix):
        result = []
        for i in range(bisect_left(self.keys, prefix), len(self.keys)):
            key = self.keys[i]
            if not key.startswith(prefix):
                break
            result.append(self.objects[key])
        return result


def logical_name(key, physical_aliases):
    parent = key
    while parent:
        if parent in physical_aliases:
            return physical_aliases[parent] + key[len(parent):]
        parent = parent.rpartition("/")[0]
    return key


def overflow_prefix(remote, uri):
    if not uri:
        return None
    location = urlsplit(uri)
    prefix = unquote(location.path.lstrip("/")).rstrip("/") + "/"
    if (location.scheme != "oss" or location.netloc != remote.bucket_name or location.query
            or location.fragment or not prefix.startswith(remote.key_prefix)
            or any(part in ("", ".", "..") for part in prefix.rstrip("/").split("/"))):
        return None
    return prefix


def iter_oss_objects(bucket, prefix, physical_prefixes=()):
    """List complete intervals. Aliases only select boundaries, never membership."""
    from oss2 import ObjectIteratorV2

    children = list(islice(ObjectIteratorV2(bucket, prefix=prefix, delimiter="/", max_keys=33), 33))
    directories = {item.key: [] for item in children if item.is_prefix()}
    if len(children) == 33 or not directories:
        if len(children) == 33:
            yield from ObjectIteratorV2(bucket, prefix=prefix, max_keys=1000)
        else:
            yield from children
        return
    for physical in physical_prefixes:
        parent = prefix + physical.partition("/")[0] + "/"
        if parent in directories:
            directories[parent].append(prefix + physical)

    def list_interval(directory, after, through):
        rows = []
        for obj in ObjectIteratorV2(bucket, prefix=directory, start_after=after, max_keys=1000):
            if through and obj.key > through:
                break
            rows.append(obj)
        return rows

    jobs = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for directory, hints in sorted(directories.items(), key=lambda x: -len(x[1])):
            hints.sort()
            parts = min(6, max(1, len(hints) // 512))
            cuts = [hints[len(hints) * i // parts] for i in range(1, parts)]
            jobs[directory] = [pool.submit(list_interval, directory, lo, hi)
                               for lo, hi in zip(["", *cuts], [*cuts, ""])]
        for child in children:
            if not child.is_prefix():
                yield child
            else:
                for job in jobs.pop(child.key):
                    yield from job.result()


def _cloud_objects(remote, bucket, overflow):
    aliases = remote.key_layout.all() if remote.key_layout is not None else {}
    reverse = {}
    for logical, physical in aliases.items():
        reverse.setdefault(physical, logical)
    overflow_sizes = {}
    cloud, accessible = [], []
    for item in iter_oss_objects(remote._delegate, remote.key_prefix, reverse):
        if not item.key.startswith(remote.key_prefix):
            raise ValueError("OSS listing escaped the configured root")
        if item.key.endswith("/"):
            continue
        if overflow and item.key.startswith(overflow):
            overflow_sizes[item.key[len(overflow):]] = item.size
        key = logical_name(item.key[len(remote.key_prefix):], reverse)
        obj = ProviderInventoryObject(key, item.size, datetime.fromtimestamp(item.last_modified, timezone.utc))
        cloud.append(obj)
        if bucket is None or not bucket.local_key(key):
            accessible.append(obj)
    return accessible, cloud, overflow_sizes


def _cache_objects(bucket, overflow):
    cache = bucket.cache
    if cache.overflow.root:
        cache.overflow.check_mount()
    local, spilled = [], []
    for item in cache.listing(sweep=False):
        if not bucket.local_key(item.key):
            continue
        payload = cache.directory(item.key) / "payload"
        target = cache.overflow.target(payload)
        modified = datetime.fromtimestamp(item.last_modified, timezone.utc)
        if target is not None and overflow is not None:
            spilled.append((item.key, target.relative_to(cache.overflow.root).as_posix(), modified))
            continue
        try:
            size = payload.stat().st_size
        except FileNotFoundError:
            continue
        local.append(ProviderInventoryObject(item.key, size, modified))
    return local, spilled


def oss_usage_objects(client, *, overflow_uri=None):
    bucket = client._bucket
    if not isinstance(bucket, CacheRoutingBucket):
        accessible, cloud, _ = _cloud_objects(bucket, None, None)
        return UsageObjectIndex(accessible), cloud

    overflow = overflow_prefix(bucket.remote, overflow_uri) if bucket.cache.overflow.root else None
    with ThreadPoolExecutor(max_workers=1) as pool:
        listing = pool.submit(copy_context().run, _cloud_objects, bucket.remote, bucket, overflow)
        local, spilled = _cache_objects(bucket, overflow)
        accessible, cloud, sizes = listing.result()
    accessible.extend(local)
    for key, path, modified in spilled:
        if path in sizes:
            accessible.append(ProviderInventoryObject(key, sizes[path], modified))
    return UsageObjectIndex(accessible), cloud

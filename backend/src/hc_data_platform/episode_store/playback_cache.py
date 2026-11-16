"""Persist evidence of a verified local playback copy next to its immutable payload."""

import hmac
import json

from hc_data_platform.data_formats.depth_evidence import file_hash
from hc_data_platform.storage.local_cache import CacheError

from .errors import PackageConflict
from .models import canonical


def _signature(cache, key, commit, receipt, frame_count, stat):
    fields = [
        "episode-playback-v1", key, commit.manifest_sha256, receipt.model_dump(mode="json"),
        frame_count, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
    ]
    return hmac.digest(cache.signing_key, canonical(fields), "sha256").hex()


def verified_frames(cache, key, commit, receipt):
    directory = cache.directory(key)
    with cache.lock(key):
        try:
            marker = json.loads((directory / "playback.json").read_bytes())
            count, proof = marker["frames"], marker["proof"]
            if type(count) is not int or count < 1 or not isinstance(proof, str):
                return None
            stat = (directory / "payload").stat()
            expected = _signature(cache, key, commit, receipt, count, stat)
            return count if hmac.compare_digest(proof, expected) else None
        except (FileNotFoundError, ValueError, TypeError, KeyError):
            return None


def record_verified(cache, key, commit, receipt, frame_count):
    directory = cache.directory(key)
    with cache.lock(key):
        try:
            meta = cache.metadata(key)
        except CacheError as exc:
            raise PackageConflict("local playback copy is missing") from exc
        payload = directory / "payload"
        stat = payload.stat()
        if (
            stat.st_size != receipt.file.size or meta["sha256"] != receipt.file.sha256
            or file_hash(payload) != receipt.file.sha256
        ):
            raise PackageConflict("local playback copy differs from its verified receipt")
        proof = _signature(cache, key, commit, receipt, frame_count, stat)
        temp = directory / ".playback.tmp"
        try:
            temp.write_bytes(canonical({"frames": frame_count, "proof": proof}))
            temp.replace(directory / "playback.json")
        finally:
            temp.unlink(missing_ok=True)

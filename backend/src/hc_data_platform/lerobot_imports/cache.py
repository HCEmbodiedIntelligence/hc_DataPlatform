"""Bound the reconstructible local source cache; never delete object-store Raw."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class SourceCache:
    def __init__(self, root: Path, *, max_bytes: int, ttl_seconds: float) -> None:
        if max_bytes <= 0 or ttl_seconds <= 0:
            raise ValueError("source cache limits must be positive")
        self.root = root
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds

    @contextmanager
    def _lock(self, key: str, *, blocking: bool = True) -> Iterator[bool]:
        locks = self.root / ".locks"
        locks.mkdir(parents=True, exist_ok=True)
        # Keep lock inodes stable even after evicting the corresponding directory.
        with (locks / key).open("a+b") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                yield False
            else:
                try:
                    yield True
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def pin(self, content_hash: str) -> Iterator[None]:
        if not re.fullmatch(r"[a-f0-9]{64}", content_hash):
            raise ValueError("invalid source cache identity")
        with self._lock(content_hash):
            root = self.root / content_hash
            root.mkdir(parents=True, exist_ok=True)
            os.utime(root, None)
            try:
                yield
            finally:
                os.utime(root, None)

    @contextmanager
    def reserve(self, additional_bytes: int) -> Iterator[None]:
        """Serialize admission through the download so concurrent copies cannot overbook."""
        if additional_bytes < 0:
            raise ValueError("invalid source cache reservation")
        with self._lock("admission"):
            _, remaining = self._evict(additional_bytes)
            if remaining + additional_bytes > self.max_bytes:
                raise RuntimeError(
                    "SOURCE_CACHE_LIMIT_EXCEEDED: active source files exceed "
                    "HC_LEROBOT_CACHE_MAX_BYTES; originals remain in object storage"
                )
            yield

    def run_once(self) -> int:
        if not self.root.exists():
            return 0
        with self._lock("admission"):
            deleted, _ = self._evict(0)
            return deleted

    def _evict(self, additional_bytes: int) -> tuple[int, int]:
        entries: list[tuple[float, Path, int]] = []
        for path in self.root.iterdir():
            if (
                path.is_symlink()
                or not path.is_dir()
                or not re.fullmatch(r"[a-f0-9]{64}", path.name)
            ):
                continue
            size = sum(
                item.stat().st_size
                for item in path.rglob("*")
                if not item.is_symlink() and item.is_file()
            )
            entries.append((path.stat().st_mtime, path, size))
        total = sum(size for _, _, size in entries)
        cutoff = time.time() - self.ttl_seconds
        deleted = 0
        for modified, path, size in sorted(entries):
            if modified > cutoff and total + additional_bytes <= self.max_bytes:
                continue
            with self._lock(path.name, blocking=False) as acquired:
                if acquired:
                    shutil.rmtree(path)
                    total -= size
                    deleted += 1
        return deleted, total

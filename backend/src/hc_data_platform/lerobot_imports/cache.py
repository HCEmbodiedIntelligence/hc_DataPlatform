"""Bound the reconstructible local source cache; never delete object-store Raw."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path


class SourceCacheLimitExceeded(RuntimeError):
    code = "SOURCE_CACHE_LIMIT_EXCEEDED"


# A worker crash leaves its reservation file behind. Downloads never run this long,
# so older files are dropped instead of permanently reducing the usable cache.
_RESERVATION_MAX_AGE_SECONDS = 6 * 3600


class SourceCache:
    def __init__(self, root: Path, *, max_bytes: int, ttl_seconds: float, retention=None) -> None:
        if max_bytes <= 0 or ttl_seconds <= 0:
            raise ValueError("source cache limits must be positive")
        self.root = root
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self.retention = retention

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
    def object_lock(self, sha256: str) -> Iterator[None]:
        """Serialize copies of one immutable object so concurrent episodes share one download."""
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ValueError("invalid source cache object identity")
        with self._lock(f"object-{sha256}"):
            yield

    @contextmanager
    def reserve(
        self, additional_bytes: int
    ) -> Iterator[Callable[[], AbstractContextManager[None]]]:
        """Admit a download under the lock, then copy outside it.

        A durable reservation file keeps concurrent copies (also from other worker
        processes sharing this volume) from overbooking while their partial files
        are excluded from the measured total. Publish the finished file inside the
        yielded ``settle()`` block: it swaps the reservation for the real file
        under the admission lock, so no admission ever counts both or neither.
        """
        if additional_bytes < 0:
            raise ValueError("invalid source cache reservation")
        reservations = self.root / ".reservations"
        with self._lock("admission"):
            _, remaining = self._evict(additional_bytes)
            if remaining + self._reserved_bytes() + additional_bytes > self.max_bytes:
                raise SourceCacheLimitExceeded(
                    "SOURCE_CACHE_LIMIT_EXCEEDED: active source files exceed "
                    "HC_LEROBOT_CACHE_MAX_BYTES; originals remain in object storage"
                )
            reservations.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=reservations, suffix=".reservation")
            with os.fdopen(fd, "w") as handle:
                handle.write(str(additional_bytes))
        reservation = Path(name)

        @contextmanager
        def settle() -> Iterator[None]:
            with self._lock("admission"):
                yield
                reservation.unlink(missing_ok=True)

        try:
            yield settle
        finally:
            with self._lock("admission"):
                reservation.unlink(missing_ok=True)

    def _reserved_bytes(self) -> int:
        """Sum live reservations. Files left by a crashed worker expire after a bounded age."""
        reservations = self.root / ".reservations"
        if not reservations.is_dir():
            return 0
        cutoff = time.time() - _RESERVATION_MAX_AGE_SECONDS
        total = 0
        for path in reservations.glob("*.reservation"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    continue
                total += int(path.read_text() or 0)
            except (OSError, ValueError):
                continue
        return total

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
            # In-flight ``.part`` files are already counted by their reservation.
            size = sum(
                item.stat().st_size
                for item in path.rglob("*")
                if not item.is_symlink() and item.is_file() and item.suffix != ".part"
            )
            entries.append((path.stat().st_mtime, path, size))
        total = sum(size for _, _, size in entries)
        ttl = self.retention() if self.retention is not None else None
        if self.retention is not None and ttl is None:
            return 0, total
        cutoff = time.time() - (ttl.total_seconds() if ttl is not None else self.ttl_seconds)
        deleted = 0
        for modified, path, size in sorted(entries):
            if modified > cutoff and (self.retention is not None or total + additional_bytes <= self.max_bytes):
                continue
            with self._lock(path.name, blocking=False) as acquired:
                if acquired and self.retention is not None:
                    current = self.retention()
                    if current is None or path.stat().st_mtime > time.time() - current.total_seconds():
                        continue
                if acquired:
                    shutil.rmtree(path)
                    total -= size
                    deleted += 1
        if self.retention is not None:
            self.retention.audit(deleted)
        return deleted, total

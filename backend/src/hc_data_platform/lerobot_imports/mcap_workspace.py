"""Coordinate MCAP scratch estimates between workers sharing a Linux temp volume."""

import fcntl
import logging
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from time import sleep

_log = logging.getLogger(__name__)
_MIB = 1024**2


def _reserved_bytes(directory):
    reserved = 0
    for path in directory.glob("lease-*"):
        with path.open("rb") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                reserved += int(path.name.split("-")[1])
            else:
                # A dead worker has no kernel lock. disk_usage still includes any orphaned files.
                path.unlink()
    return reserved


@contextmanager
def mcap_workspace(source_bytes, checkpoint, *, needs_source):
    root = Path(tempfile.gettempdir())
    directory = root / f"hc-mcap-reservations-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    guard = os.open(directory, os.O_DIRECTORY | os.O_NOFOLLOW)
    lease = None
    announced = False
    try:
        stat = os.fstat(guard)
        if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            raise PermissionError(f"Expected private worker reservation directory: {directory}")
        # Concurrent conversions have used more than ten times their compressed source sizes.
        # An estimate controls concurrency only. A single large recording may still fit.
        estimate = max(64 * _MIB, source_bytes * 16)
        while lease is None:
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                reserved = _reserved_bytes(directory)
                free = shutil.disk_usage(root).free
                if not reserved and needs_source and free < source_bytes:
                    raise ValueError(
                        f"MCAP_WORKSPACE_FULL: free_bytes={free} source_bytes={source_bytes}"
                    )
                if not reserved or reserved + estimate + 512 * _MIB <= free:
                    lease = tempfile.mkstemp(prefix=f"lease-{estimate}-", dir=directory)
                    fcntl.flock(lease[0], fcntl.LOCK_EX)
            finally:
                fcntl.flock(guard, fcntl.LOCK_UN)
            if lease is None:
                if not announced:
                    _log.info(
                        "Waiting for MCAP scratch space: free=%d reserved=%d estimate=%d",
                        free,
                        reserved,
                        estimate,
                    )
                    announced = True
                checkpoint()
                sleep(1)
        yield
    finally:
        if lease is not None:
            # Serialize unlink with scans so another process cannot see a half-released lease.
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                try:
                    os.unlink(lease[1])
                finally:
                    os.close(lease[0])
            finally:
                fcntl.flock(guard, fcntl.LOCK_UN)
        os.close(guard)

"""Write immutable cache payloads to an OSS mount when local space is low."""

import errno
import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path


class CacheOverflow:
    def __init__(self, directory, reserve):
        self.root = Path(directory) if directory else None
        self.reserve = reserve

    def check_mount(self):
        path = self.root
        while not path.exists() and path != path.parent:
            path = path.parent
        dev = path.stat().st_dev
        device = f"{os.major(dev)}:{os.minor(dev)}"
        with open("/proc/self/mountinfo") as mounts:
            if any(line.split()[2] == device and line.partition(" - ")[2].split()[0]
                   in {"fuse.ossfs", "fuse.ossfs2"} for line in mounts):
                return
        raise OSError(errno.ENODEV, "OSS cache overflow mount is unavailable", str(self.root))

    def target(self, link):
        if not link.is_symlink():
            return None
        path = Path(os.readlink(link))
        if (not self.root or not re.fullmatch("[0-9a-f]{32}", path.name)
                or path != self.root / path.name[:2] / path.name):
            raise ValueError("unexpected cache payload link")
        return path

    def release(self, path):
        if path is not None:
            self.check_mount()
            path.unlink(missing_ok=True)

    def write(self, stream, staging, expected=0):
        md5, sha, size = hashlib.md5(), hashlib.sha256(), 0
        remote = None
        output = staging.open("xb", buffering=0)
        try:
            for block in iter(lambda: stream.read(1024**2), b""):
                spill = False
                if self.root and remote is None:
                    need = max(len(block), expected - size)
                    spill = shutil.disk_usage(staging.parent).free - need < self.reserve
                if not spill:
                    try:
                        if output.write(block) != len(block):
                            raise OSError(errno.ENOSPC, "incomplete cache write")
                    except OSError as exc:
                        if not self.root or remote or exc.errno not in (errno.ENOSPC, errno.EDQUOT):
                            raise
                        output.truncate(size)
                        spill = True
                if spill:
                    self.check_mount()
                    token = uuid.uuid4().hex
                    remote = self.root / token[:2] / token
                    remote.parent.mkdir(parents=True, exist_ok=True)
                    output.close()
                    output = remote.open("xb")
                    with staging.open("rb") as prefix:
                        shutil.copyfileobj(prefix, output, 1024**2)
                    staging.unlink()
                    staging.symlink_to(remote)
                    if output.write(block) != len(block):
                        raise OSError(errno.EIO, "incomplete OSS cache write")
                md5.update(block)
                sha.update(block)
                size += len(block)
            output.flush()
            os.fsync(output.fileno())
            output.close()
            return md5.hexdigest(), sha.hexdigest(), size
        except BaseException:
            try:
                output.close()
            finally:
                self.release(remote)
            raise


class PartStream:
    """Read consecutive upload parts without creating a second full-sized file."""

    def __init__(self, directory, parts):
        self.paths = iter(directory / str(part.part_number) for part in parts)
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.current:
            self.current.close()

    def read(self, size):
        while True:
            if self.current is None:
                path = next(self.paths, None)
                if path is None:
                    return b""
                self.current = path.open("rb")
            block = self.current.read(size)
            if block:
                return block
            self.current.close()
            self.current = None

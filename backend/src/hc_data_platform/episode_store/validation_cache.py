"""Reuse a successful local package validation while every attempt file is unchanged.

Encoding/writing and committing the same package previously decoded every video
twice. This process-local cache only skips the second decode; immutable upload
still verifies every byte against the already validated manifest's SHA-256.
"""

import stat
from collections import OrderedDict
from functools import wraps
from pathlib import Path
from threading import RLock


def _inventory(root: Path):
    values = []
    for path in [root, *sorted(root.rglob("*"))]:
        facts = path.stat(follow_symlinks=False)
        if not (stat.S_ISREG(facts.st_mode) or stat.S_ISDIR(facts.st_mode)):
            return None
        values.append((str(path.relative_to(root)), facts.st_dev, facts.st_ino,
                       facts.st_mode, facts.st_size, facts.st_mtime_ns, facts.st_ctime_ns))
    return tuple(values)


def reuse_verified_package(validate):
    entries, lock = OrderedDict(), RLock()

    @wraps(validate)
    def checked(root, source):
        root = Path(root).absolute()
        key = (str(root), source.model_dump_json())
        before = _inventory(root)
        with lock:
            cached = entries.get(key)
            if before is not None and cached is not None and cached[0] == before:
                entries.move_to_end(key)
                return cached[1].model_copy(deep=True)
        manifest = validate(root, source)
        after = _inventory(root)
        if after != before:
            raise ValueError("package changed during validation; retry the frozen attempt")
        if after is not None and len(after) <= 10_000:
            with lock:
                entries[key] = (after, manifest.model_copy(deep=True))
                entries.move_to_end(key)
                while len(entries) > 8:
                    entries.popitem(last=False)
        return manifest

    return checked

"""Cross-process pins for local staging directories."""
import fcntl
import os
from contextlib import contextmanager


@contextmanager
def staging_lease(path, *, blocking=True, touch=False):
    locks = path.parent / '.locks'
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / path.name).open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                if touch and path.exists():
                    os.utime(path, None)
                fcntl.flock(handle, fcntl.LOCK_UN)

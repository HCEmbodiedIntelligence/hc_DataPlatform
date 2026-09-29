"""Lease renewal shared by immutable artifact producers on the scoped outbox."""

import threading
from contextlib import contextmanager
from contextvars import copy_context


class DeliveryLeaseLost(RuntimeError):
    pass


@contextmanager
def renewing_lease(repository, row):
    stopped, lost = threading.Event(), threading.Event()

    def renew():
        while not stopped.wait(15):
            try:
                repository.heartbeat(row)
            except Exception:
                lost.set()
                return

    def checkpoint():
        if lost.is_set():
            raise DeliveryLeaseLost("artifact lease renewal stopped")

    thread = threading.Thread(target=copy_context().run, args=(renew,), daemon=True)
    thread.start()
    try:
        yield checkpoint
    finally:
        stopped.set()
        thread.join(timeout=1)

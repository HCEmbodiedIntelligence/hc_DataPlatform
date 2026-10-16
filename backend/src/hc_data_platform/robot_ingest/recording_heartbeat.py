"""Keep long recording adapters observable and stop them at safe checkpoints."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import copy_context
from threading import Event, Thread

from temporalio import activity
from temporalio.exceptions import ApplicationError, CancelledError

HEARTBEAT_INTERVAL_SECONDS = 5.0


@contextmanager
def recording_heartbeat() -> Iterator[Callable[[], None]]:
    """A live worker heartbeats even while a codec/storage call is blocking.

    Temporal detects process loss independently. Cancellation/shutdown is checked
    on the adapter thread, so its transaction rolls back before any retry writes.
    """
    stop = Event()
    failures: list[Exception] = []
    attempt = activity.info().attempt

    def checkpoint() -> None:
        if activity.is_cancelled():
            raise CancelledError("Recording preparation cancelled")
        if activity.is_worker_shutdown():
            raise ApplicationError(
                "Recording worker is shutting down; resume from the immutable source",
                type="RECORDING_WORKER_SHUTDOWN",
            )
        if failures:
            raise failures[0]

    def pulse() -> None:
        activity.heartbeat({"stage": "recording_preparation", "attempt": attempt})

    def keep_alive() -> None:
        while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                pulse()
            except Exception as exc:
                failures.append(exc)
                return

    checkpoint()
    pulse()
    thread = Thread(
        target=copy_context().run,
        args=(keep_alive,),
        name="recording-heartbeat",
        daemon=True,
    )
    thread.start()
    try:
        yield checkpoint
    finally:
        stop.set()
        thread.join(timeout=HEARTBEAT_INTERVAL_SECONDS + 1)


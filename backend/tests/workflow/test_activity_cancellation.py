import asyncio
import threading

import pytest

from hc_data_platform.workflow import activities


@pytest.mark.asyncio
async def test_cancellation_does_not_abandon_an_inflight_synchronous_commit(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    heartbeats = []
    monkeypatch.setattr(activities, "_heartbeat", lambda *args: heartbeats.append(args))
    monkeypatch.setattr(activities, "_HEARTBEAT_INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(activities.activity, "is_cancelled", lambda: False)

    def commit():
        started.set()
        release.wait(20)
        completed.set()

    task = asyncio.create_task(activities._with_heartbeats("commit", commit))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        # The previous implementation acknowledged cancellation at 10 seconds,
        # allowing cleanup and another writer while this thread still mutated storage.
        await asyncio.sleep(10.2)
        assert not task.done()
        assert not completed.is_set()
        assert ("commit", "cancelling") in heartbeats
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert completed.is_set()

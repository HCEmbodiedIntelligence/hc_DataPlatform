import os
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from hc_data_platform.lerobot_imports import mcap_workspace as scratch

MIB = 1024**2


@pytest.fixture
def volume(tmp_path, monkeypatch):
    monkeypatch.setattr(scratch.tempfile, "gettempdir", lambda: str(tmp_path))
    free = SimpleNamespace(free=2048 * MIB)
    monkeypatch.setattr(scratch.shutil, "disk_usage", lambda root: free)
    return free


def _crash_holding_lease(root, ready, release):
    scratch.tempfile.tempdir = str(root)
    with scratch.mcap_workspace(100 * MIB, lambda: None, needs_source=True):
        ready.set()
        if release.wait(10):
            os._exit(7)


def test_large_job_waits_and_resumes_after_release(volume):
    waiting = Event()

    def process():
        with scratch.mcap_workspace(100 * MIB, waiting.set, needs_source=True):
            return "admitted"

    with ThreadPoolExecutor() as executor:
        with scratch.mcap_workspace(100 * MIB, lambda: None, needs_source=True):
            future = executor.submit(process)
            assert waiting.wait(5) and not future.done()
        assert future.result(timeout=5) == "admitted"


def test_estimate_only_limits_concurrency_and_cancel_does_not_leak(volume):
    volume.free = 200 * MIB

    def cancel():
        raise InterruptedError

    with (
        scratch.mcap_workspace(100 * MIB, cancel, needs_source=True),
        pytest.raises(InterruptedError),
        scratch.mcap_workspace(100 * MIB, cancel, needs_source=True),
    ):
        pytest.fail("second conversion should wait")
    with scratch.mcap_workspace(100 * MIB, cancel, needs_source=True):
        pass


def test_cache_replay_and_room_for_source(volume):
    volume.free = 2 * MIB
    with (
        pytest.raises(ValueError, match="MCAP_WORKSPACE_FULL"),
        scratch.mcap_workspace(3 * MIB, lambda: None, needs_source=True),
    ):
        pytest.fail("source cannot fit")
    with scratch.mcap_workspace(3 * MIB, lambda: None, needs_source=False):
        pass


def test_small_jobs_share_disk_and_crashed_worker_lease_is_recovered(tmp_path, volume, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    def unexpected_wait():
        pytest.fail("admission should not block")

    ctx = get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_crash_holding_lease, args=(tmp_path, ready, release))
    child.start()

    def cancel():
        raise InterruptedError

    try:
        assert ready.wait(10)
        with (
            pytest.raises(InterruptedError),
            scratch.mcap_workspace(MIB, cancel, needs_source=True),
        ):
            pytest.fail("another process owns the reservation")
    finally:
        release.set()
        child.join(timeout=10)
    assert child.exitcode == 7
    with (
        scratch.mcap_workspace(MIB, unexpected_wait, needs_source=True),
        scratch.mcap_workspace(MIB, unexpected_wait, needs_source=True),
    ):
        pass

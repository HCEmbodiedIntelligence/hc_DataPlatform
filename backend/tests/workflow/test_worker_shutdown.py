from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hc_data_platform.workflow.worker import _run_until_shutdown, _worker_readiness


class _Worker:
    def __init__(self, *, services_stopped: asyncio.Event) -> None:
        self.started = asyncio.Event()
        self.completed = asyncio.Event()
        self.services_stopped = services_stopped
        self.shutdown_calls = 0

    async def run(self) -> None:
        self.started.set()
        await self.completed.wait()

    async def shutdown(self) -> None:
        assert self.services_stopped.is_set()
        self.shutdown_calls += 1
        self.completed.set()


@pytest.mark.asyncio
async def test_shutdown_stops_auxiliary_claims_before_temporal_activity_drain() -> None:
    shutdown_requested = asyncio.Event()
    service_started = asyncio.Event()
    service_stopped = asyncio.Event()

    async def claims() -> None:
        service_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            service_stopped.set()

    worker = _Worker(services_stopped=service_stopped)
    running = asyncio.create_task(
        _run_until_shutdown(worker, {"claims": claims()}, shutdown_requested)
    )
    await asyncio.wait_for(worker.started.wait(), timeout=1)
    await asyncio.wait_for(service_started.wait(), timeout=1)

    shutdown_requested.set()
    await asyncio.wait_for(running, timeout=1)

    assert service_stopped.is_set()
    assert worker.shutdown_calls == 1


@pytest.mark.asyncio
async def test_worker_readiness_changes_to_draining_on_shutdown_request() -> None:
    shutdown_requested = asyncio.Event()
    assert (await _worker_readiness(shutdown_requested)).status == "ready"
    shutdown_requested.set()
    assert (await _worker_readiness(shutdown_requested)).status == "draining"


@pytest.mark.asyncio
async def test_readiness_sentinel_exists_only_while_worker_accepts_work(
    tmp_path: Path,
) -> None:
    shutdown_requested = asyncio.Event()
    services_stopped = asyncio.Event()
    readiness_file = tmp_path / "worker-ready"

    async def claims() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            services_stopped.set()

    worker = _Worker(services_stopped=services_stopped)
    running = asyncio.create_task(
        _run_until_shutdown(
            worker,
            {"claims": claims()},
            shutdown_requested,
            readiness_file=readiness_file,
        )
    )
    await asyncio.wait_for(worker.started.wait(), timeout=1)
    for _attempt in range(10):
        if readiness_file.exists() and readiness_file.read_text(encoding="utf-8") == "ready\n":
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("worker readiness sentinel was not published")

    shutdown_requested.set()
    await asyncio.wait_for(running, timeout=1)

    assert not readiness_file.exists()


@pytest.mark.asyncio
async def test_unexpected_service_completion_stops_worker() -> None:
    services_stopped = asyncio.Event()
    services_stopped.set()
    worker = _Worker(services_stopped=services_stopped)

    async def completed() -> None:
        return None

    await asyncio.wait_for(
        _run_until_shutdown(worker, {"completed": completed()}, asyncio.Event()),
        timeout=1,
    )
    assert worker.shutdown_calls == 1


def test_worker_shutdown_setting_is_bounded() -> None:
    from pydantic import ValidationError

    from hc_data_platform.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(worker_graceful_shutdown_seconds=0, _env_file=None)
    settings = Settings(worker_graceful_shutdown_seconds=90, _env_file=None)
    assert settings.worker_graceful_shutdown_seconds == 90
    assert settings.worker_max_concurrent_workflow_tasks == 4
    assert settings.worker_max_cached_workflows == 16
    with pytest.raises(ValidationError, match="HC_WORKER_MAX_CACHED_WORKFLOWS"):
        Settings(
            worker_max_concurrent_workflow_tasks=17,
            worker_max_cached_workflows=16,
            _env_file=None,
        )

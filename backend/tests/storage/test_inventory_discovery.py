from __future__ import annotations

import asyncio

import pytest

from hc_data_platform.storage import inventory_worker


@pytest.mark.asyncio
async def test_discovery_starts_new_project_and_drains_removed_scopes(monkeypatch):
    first = "org/project-one/global"
    second = "org/project-two/global"
    discovered = [first]
    started = {scope: asyncio.Event() for scope in (first, second)}
    stopped = {scope: asyncio.Event() for scope in (first, second)}
    advance = asyncio.Event()

    async def scan(_runtime, *, raw_scope, **_kwargs):
        started[raw_scope].set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped[raw_scope].set()

    async def wait_for_next_discovery(_delay):
        await advance.wait()
        advance.clear()

    monkeypatch.setattr(inventory_worker, "_serve_singleton_scope", scan)
    monkeypatch.setattr(inventory_worker.asyncio, "sleep", wait_for_next_discovery)
    runtime = inventory_worker.StorageInventoryRuntime(
        producer=None,  # type: ignore[arg-type]
        scopes=(),
        interval_seconds=300,
        scope_provider=lambda: tuple(discovered),
    )
    task = asyncio.create_task(inventory_worker._serve_discovered_scopes(runtime))
    try:
        await asyncio.wait_for(started[first].wait(), timeout=2)
        discovered[:] = [second]
        advance.set()
        await asyncio.wait_for(started[second].wait(), timeout=2)
        await asyncio.wait_for(stopped[first].wait(), timeout=2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert stopped[second].is_set()


@pytest.mark.asyncio
async def test_discovery_merges_fixed_scopes_without_duplicate_scans(monkeypatch):
    scope = "org/project/global"
    scanned = []

    def cycle(_producer, *, scopes, cache_retention=None):
        scanned.extend(scopes)
        return ()

    async def stop(_delay):
        raise asyncio.CancelledError

    monkeypatch.setattr(inventory_worker, "run_inventory_cycle", cycle)
    monkeypatch.setattr(inventory_worker.asyncio, "sleep", stop)
    runtime = inventory_worker.StorageInventoryRuntime(
        producer=None,  # type: ignore[arg-type]
        scopes=(scope,),
        interval_seconds=300,
        scope_provider=lambda: (scope,),
    )
    with pytest.raises(asyncio.CancelledError):
        await inventory_worker.serve_inventory(runtime)
    assert scanned == [scope]

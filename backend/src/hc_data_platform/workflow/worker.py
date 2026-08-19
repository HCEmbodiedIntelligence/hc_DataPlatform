from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Callable
from typing import Any, cast

from hc_data_platform.core.config import get_settings

DEFAULT_TASK_QUEUE = "hc-data-pipeline"


def _load_dependency_factory() -> None:
    """Load a deployment-owned activity adapter factory when configured."""

    factory_path = os.getenv(
        "HC_WORKFLOW_ACTIVITY_FACTORY",
        "hc_data_platform.runtime:activity_dependencies",
    )
    module_name, separator, attribute = factory_path.partition(":")
    if not separator or not module_name or not attribute:
        raise RuntimeError(
            "HC_WORKFLOW_ACTIVITY_FACTORY must use the form 'package.module:factory'"
        )
    module = importlib.import_module(module_name)
    factory = cast(Callable[[], Any], getattr(module, attribute))

    from .activities import ActivityDependencies, configure_activity_dependencies

    dependencies = factory()
    if not isinstance(dependencies, ActivityDependencies):
        raise TypeError("workflow activity factory must return ActivityDependencies")
    configure_activity_dependencies(dependencies)


def discover_temporal_registrations() -> tuple[list[type[Any]], list[Any]]:
    """Return every concrete workflow and activity registered by the worker."""

    from hc_data_platform.storage.temporal import (
        ALL_STORAGE_ACTIVITIES,
        ALL_STORAGE_WORKFLOWS,
    )

    from .activities import ALL_ACTIVITIES
    from .temporal_workflows import ALL_WORKFLOWS

    return [*ALL_WORKFLOWS, *ALL_STORAGE_WORKFLOWS], [
        *ALL_ACTIVITIES,
        *ALL_STORAGE_ACTIVITIES,
    ]


async def serve() -> None:
    try:
        from temporalio.client import Client
        from temporalio.contrib.pydantic import pydantic_data_converter
        from temporalio.worker import Worker
    except ImportError as exc:
        raise RuntimeError("install the 'workflow' extra to run a Temporal worker") from exc

    _load_dependency_factory()
    workflows, activities = discover_temporal_registrations()
    if not workflows or not activities:
        raise RuntimeError("no Temporal workflows or activities are registered")

    settings = get_settings()
    client = await Client.connect(
        settings.temporal_target,
        namespace=os.getenv("HC_TEMPORAL_NAMESPACE", "default"),
        data_converter=pydantic_data_converter,
    )
    worker = Worker(
        client,
        task_queue=os.getenv("HC_TEMPORAL_TASK_QUEUE", DEFAULT_TASK_QUEUE),
        workflows=workflows,
        activities=activities,
    )
    from hc_data_platform.runtime import build_worker_outbox

    from .outbox_worker import serve_outbox

    outbox = build_worker_outbox(settings, temporal_client=client)
    if outbox is None:
        await worker.run()
        return
    tasks = {
        asyncio.create_task(worker.run(), name="temporal-worker"),
        asyncio.create_task(
            serve_outbox(
                outbox.dispatcher,
                scopes=outbox.scopes,
                poll_interval_seconds=outbox.poll_interval_seconds,
                batch_size=outbox.batch_size,
            ),
            name="outbox-dispatcher",
        ),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from hc_data_platform.core.config import get_settings

DEFAULT_TASK_QUEUE = "hc-data-pipeline"


def _load_dependency_factory(*, role: str) -> None:
    """Load a deployment-owned activity adapter factory when configured."""

    default_factory = (
        "hc_data_platform.runtime:media_activity_dependencies"
        if role == "media"
        else "hc_data_platform.runtime:activity_dependencies"
    )
    factory_path = os.getenv("HC_WORKFLOW_ACTIVITY_FACTORY", default_factory)
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


def discover_temporal_registrations(
    role: str = "main",
) -> tuple[list[type[Any]], list[Any]]:
    """Return every concrete workflow and activity registered by the worker."""

    from hc_data_platform.storage.temporal import (
        ALL_STORAGE_ACTIVITIES,
        ALL_STORAGE_WORKFLOWS,
    )

    from .activities import ALL_ACTIVITIES, create_preview
    from .temporal_workflows import ALL_WORKFLOWS, PreviewWorkflow

    if role == "media":
        return [PreviewWorkflow], [create_preview]

    return [
        *(workflow for workflow in ALL_WORKFLOWS if workflow is not PreviewWorkflow),
        *ALL_STORAGE_WORKFLOWS,
    ], [
        *(registered for registered in ALL_ACTIVITIES if registered is not create_preview),
        *ALL_STORAGE_ACTIVITIES,
    ]


async def serve() -> None:
    try:
        from temporalio.client import Client
        from temporalio.contrib.pydantic import pydantic_data_converter
        from temporalio.worker import Worker
    except ImportError as exc:
        raise RuntimeError("install the 'workflow' extra to run a Temporal worker") from exc

    role = os.getenv("HC_WORKER_ROLE", "main").strip().lower()
    if role not in {"main", "media"}:
        raise RuntimeError("HC_WORKER_ROLE must be main or media")
    _load_dependency_factory(role=role)
    workflows, activities = discover_temporal_registrations(role)
    if not workflows or not activities:
        raise RuntimeError("no Temporal workflows or activities are registered")

    settings = get_settings()
    client = await Client.connect(
        settings.temporal_target,
        namespace=os.getenv("HC_TEMPORAL_NAMESPACE", "default"),
        data_converter=pydantic_data_converter,
    )
    task_queue = (
        os.getenv("HC_MEDIA_TEMPORAL_TASK_QUEUE", "hc-media-pipeline")
        if role == "media"
        else os.getenv("HC_TEMPORAL_TASK_QUEUE", DEFAULT_TASK_QUEUE)
    )
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=workflows,
        activities=activities,
        max_concurrent_activities=(
            settings.media_max_concurrent_generations if role == "media" else None
        ),
    )
    from hc_data_platform.preview.gc import PreviewStagingSweeper, serve_preview_maintenance
    from hc_data_platform.runtime import (
        build_preview_gc,
        build_projection_staging_sweeper,
        build_storage_inventory,
        build_worker_outbox,
    )
    from hc_data_platform.storage.inventory_worker import serve_inventory

    from .outbox_worker import serve_outbox

    outbox = (
        None if role == "media" else build_worker_outbox(settings, temporal_client=client)
    )
    inventory = (
        build_storage_inventory(settings)
        if role != "media" and settings.storage_inventory_scopes
        else None
    )
    preview_gc = (
        build_preview_gc(settings)
        if role == "main" and settings.outbox_scopes
        else None
    )
    projection_staging_sweeper = (
        build_projection_staging_sweeper(settings) if role == "main" else None
    )
    staging_roots = (
        (Path(settings.preview_cache_root) / "staging",)
        if role == "media"
        else (
            Path(settings.preview_cache_root) / "staging",
            Path(settings.alignment_staging_root) / "projections",
        )
    )
    preview_staging = PreviewStagingSweeper(
        staging_roots,
        ttl=timedelta(hours=settings.preview_staging_ttl_hours),
    )
    tasks = {
        asyncio.create_task(worker.run(), name="temporal-worker"),
        asyncio.create_task(
            serve_preview_maintenance(
                collector=preview_gc,
                scopes=settings.outbox_scopes if role == "main" else (),
                staging_sweeper=preview_staging,
                interval_seconds=settings.preview_gc_interval_seconds,
                object_staging_sweepers=(
                    ()
                    if projection_staging_sweeper is None
                    else (projection_staging_sweeper.run_once,)
                ),
            ),
            name="preview-maintenance",
        ),
    }
    if outbox is not None:
        tasks.add(
            asyncio.create_task(
                serve_outbox(
                    outbox.dispatcher,
                    scopes=outbox.scopes,
                    poll_interval_seconds=outbox.poll_interval_seconds,
                    batch_size=outbox.batch_size,
                    schedule_enqueuer=outbox.schedule_enqueuer,
                ),
                name="outbox-dispatcher",
            )
        )
    if inventory is not None:
        tasks.add(
            asyncio.create_task(
                serve_inventory(inventory),
                name="storage-inventory",
            )
        )
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

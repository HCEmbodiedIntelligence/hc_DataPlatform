from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from hc_data_platform.core.config import Settings, get_settings, require_durable_runtime
from hc_data_platform.core.structured_logging import (
    StructuredLogIdentity,
    configure_structured_logging,
    log_event,
)
from hc_data_platform.platform_control.release_identity import (
    PlatformReleaseIdentityV1,
    release_identity_from_settings,
)
from hc_data_platform.platform_ops.instances import (
    InMemoryPlatformInstanceRepository,
    InstanceReadinessSummary,
    PlatformInstanceHeartbeater,
    PlatformInstanceService,
    PostgresPlatformInstanceRepository,
    platform_instance_identity,
)
from hc_data_platform.platform_ops.maintenance import (
    PostgresMaintenanceRepository,
)
from hc_data_platform.platform_ops.object_store_config import (
    load_persisted_object_store_settings,
    settings_object_store_configured,
)
from hc_data_platform.platform_ops.runtime_config import (
    InMemoryRuntimeConfigRepository,
    PollingRuntimeConfigSubscriber,
    PostgresRuntimeConfigRepository,
    PostgresRuntimeConfigSubscriber,
    RuntimeConfigService,
    RuntimeConfigState,
    RuntimeConfigSynchronizer,
    runtime_config_boot_values,
)
from hc_data_platform.platform_ops.task_leases import (
    PostgresPlatformTaskLeaseRepository,
)

DEFAULT_TASK_QUEUE = "hc-data-pipeline"
DEFAULT_WORKER_READINESS_FILE = Path("/tmp/hc-runtime/worker-ready")
logger = logging.getLogger(__name__)


class WorkerGroup:
    """Task queues in one process, with independent activity concurrency."""

    def __init__(self, *workers: Any) -> None:
        self.workers = workers

    async def run(self) -> None:
        tasks = [asyncio.create_task(worker.run()) for worker in self.workers]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        await asyncio.gather(*(worker.shutdown() for worker in self.workers))


async def _worker_readiness(shutdown_requested: asyncio.Event) -> InstanceReadinessSummary:
    return InstanceReadinessSummary(status="draining" if shutdown_requested.is_set() else "ready")


async def _run_until_shutdown(
    worker: Any,
    services: Mapping[str, Coroutine[Any, Any, None]],
    shutdown_requested: asyncio.Event,
    *,
    readiness_file: Path | None = None,
) -> None:
    """Stop new claims first, then let Temporal drain its in-flight activities."""

    worker_task = asyncio.create_task(worker.run(), name="temporal-worker")
    service_tasks: set[asyncio.Task[None]] = {
        asyncio.create_task(coroutine, name=name) for name, coroutine in services.items()
    }
    shutdown_task = asyncio.create_task(shutdown_requested.wait(), name="shutdown-requested")
    tasks = {worker_task, shutdown_task, *service_tasks}
    try:
        await asyncio.sleep(0)
        if readiness_file is not None and not any(
            task.done() for task in {worker_task, *service_tasks}
        ):
            readiness_file.write_text("ready\n", encoding="utf-8")
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if shutdown_task in done:
            if readiness_file is not None:
                readiness_file.unlink(missing_ok=True)
            log_event(logger, logging.INFO, "PROCESS.GRACEFUL_DRAIN_STARTED")
            for task in service_tasks:
                task.cancel()
            await asyncio.gather(*service_tasks, return_exceptions=True)
            await worker.shutdown()
            await worker_task
            log_event(logger, logging.INFO, "PROCESS.GRACEFUL_DRAIN_COMPLETED")
            return

        await worker.shutdown()
        for task in tasks - done:
            task.cancel()
        await asyncio.gather(*(tasks - done), return_exceptions=True)
        for task in done:
            if task.get_name() != "shutdown-requested":
                task.result()
    finally:
        if readiness_file is not None:
            readiness_file.unlink(missing_ok=True)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def worker_release_identity(
    settings: Settings,
    *,
    worker_role: str,
) -> PlatformReleaseIdentityV1:
    expected_component = "media-worker" if worker_role == "media" else "worker"
    identity = release_identity_from_settings(settings)
    if identity.component != expected_component:
        raise ValueError(
            f"HC_WORKER_ROLE={worker_role} requires HC_COMPONENT_ROLE={expected_component}"
        )
    return identity


def worker_build_id(settings: Settings) -> str | None:
    """Return the release-bound Build ID independent of runtime activation policy."""

    if settings.temporal_worker_build_id is not None:
        return settings.temporal_worker_build_id
    if settings.release_id != "unreleased":
        return settings.release_id
    return None


def worker_runtime_build_id(settings: Settings) -> str | None:
    """Enable routing in managed environments or when a local override is explicit."""

    if settings.temporal_worker_build_id is not None:
        return worker_build_id(settings)
    if settings.environment not in {"staging", "production"}:
        return None
    return worker_build_id(settings)


def _load_dependency_factory(*, role: str) -> None:
    """Load a deployment-owned activity adapter factory when configured."""

    default_factory = (
        "hc_data_platform.runtime:media_activity_dependencies"
        if role in {"media", "combined"}
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

    from .activities import ALL_ACTIVITIES, create_aligned_media
    from .lerobot_workflow import (
        LeRobotImportWorkflow,
        prepare_lerobot_episode,
        update_lerobot_state,
    )
    from .temporal_workflows import ALL_WORKFLOWS

    if role == "media":
        return [], [create_aligned_media]

    return [
        LeRobotImportWorkflow,
        *ALL_WORKFLOWS,
        *ALL_STORAGE_WORKFLOWS,
    ], [
        prepare_lerobot_episode,
        update_lerobot_state,
        *(registered for registered in ALL_ACTIVITIES if registered is not create_aligned_media),
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
    if role not in {"main", "media", "combined"}:
        raise RuntimeError("HC_WORKER_ROLE must be main, media or combined")
    settings, _object_store_config_revision = load_persisted_object_store_settings(get_settings())
    object_store_configured = settings_object_store_configured(settings)
    require_durable_runtime(settings)
    readiness_file = Path(os.getenv("HC_WORKER_READINESS_FILE", str(DEFAULT_WORKER_READINESS_FILE)))
    readiness_file.unlink(missing_ok=True)
    release_identity = worker_release_identity(settings, worker_role=role)
    instance_identity = platform_instance_identity(settings, release_identity)
    configure_structured_logging(
        StructuredLogIdentity(
            service=(
                "hc-data-platform-media-worker" if role == "media" else "hc-data-platform-worker"
            ),
            instance_id=str(instance_identity.instance_id),
            node_name=instance_identity.node_name,
            role="media-worker" if role == "media" else "worker",
            release_id=release_identity.release_id,
        )
    )
    log_event(
        logger,
        logging.INFO,
        "PROCESS.RELEASE_IDENTITY_INITIALIZED",
    )
    if not object_store_configured:
        log_event(
            logger,
            logging.WARNING,
            "OBJECT_STORAGE.CONFIGURATION_PENDING",
        )
    _load_dependency_factory(role=role)
    from .activities import configure_activity_maintenance

    persistent_platform_control = settings.environment in {"staging", "production"}
    maintenance_gate = (
        PostgresMaintenanceRepository.from_dsn(settings.postgres_dsn)
        if persistent_platform_control
        else None
    )
    task_lease_repository = (
        PostgresPlatformTaskLeaseRepository.from_dsn(settings.postgres_dsn)
        if persistent_platform_control
        else None
    )
    configure_activity_maintenance(
        maintenance_gate,
        environment_id=settings.platform_environment_id,
        instance_id=str(instance_identity.instance_id),
    )
    workflows, activities = discover_temporal_registrations(role)
    if not activities:
        raise RuntimeError("no Temporal activities are registered")

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
    build_id = worker_runtime_build_id(settings)
    worker: Any = Worker(
        client,
        task_queue=task_queue,
        workflows=workflows,
        activities=activities,
        max_concurrent_workflow_tasks=settings.worker_max_concurrent_workflow_tasks,
        max_cached_workflows=settings.worker_max_cached_workflows,
        max_concurrent_activities=(
            settings.media_max_concurrent_generations
            if role == "media"
            else settings.worker_max_concurrent_activities
        ),
        graceful_shutdown_timeout=timedelta(seconds=settings.worker_graceful_shutdown_seconds),
        build_id=build_id,
        use_worker_versioning=build_id is not None,
    )
    robot_executor = None
    if role != "media":
        from hc_data_platform.robot_ingest.lerobot_processor import NativeLeRobotProcessor, robot_task_queue
        from hc_data_platform.robot_ingest.processing_store import ProcessingStore
        from hc_data_platform.robot_ingest.processing_worker import RobotProcessingActivities
        from hc_data_platform.robot_ingest.processing_workflow import RobotIngestProcessingWorkflow
        from hc_data_platform.robot_ingest.recording_workflow import RobotRecordingWorkflow
        from hc_data_platform.robot_ingest.recording_bridge import RecordingOutboxHandler
        from .activities import _dependencies, _require

        pipeline = _require(_dependencies.lerobot_pipeline, "lerobot_pipeline")
        robot_activities = RobotProcessingActivities(
            ProcessingStore(pipeline.connections), pipeline.storage,
            NativeLeRobotProcessor(pipeline, client, asyncio.get_running_loop(), task_queue=task_queue),
        )
        robot_executor = ThreadPoolExecutor(max_workers=settings.worker_max_concurrent_activities)
        recording_activity = RecordingOutboxHandler(pipeline.connections,pipeline.storage)
        worker = WorkerGroup(worker, Worker(
            client, task_queue=robot_task_queue(task_queue),
            workflows=[RobotIngestProcessingWorkflow,RobotRecordingWorkflow], activities=[*robot_activities.activities,recording_activity.prepare,recording_activity.failed],
            activity_executor=robot_executor,
            max_concurrent_activities=settings.worker_max_concurrent_activities,
            graceful_shutdown_timeout=timedelta(seconds=settings.worker_graceful_shutdown_seconds),
            build_id=build_id, use_worker_versioning=build_id is not None,
        ))
    if role == "combined":
        media_queue = os.getenv("HC_MEDIA_TEMPORAL_TASK_QUEUE", "hc-media-pipeline")
        if media_queue == task_queue:
            raise RuntimeError("combined worker requires distinct main and media task queues")
        _, media_activities = discover_temporal_registrations("media")
        worker = WorkerGroup(
            worker,
            Worker(
                client,
                task_queue=media_queue,
                activities=media_activities,
                max_concurrent_activities=settings.media_max_concurrent_generations,
                graceful_shutdown_timeout=timedelta(
                    seconds=settings.worker_graceful_shutdown_seconds
                ),
                build_id=build_id,
                use_worker_versioning=build_id is not None,
            ),
        )
    from hc_data_platform.aligned_media.maintenance import (
        MediaStagingSweeper,
        serve_media_maintenance,
    )
    from hc_data_platform.runtime import (
        build_aligned_media_orphan_reconciler,
        build_aligned_media_version_retirement_collector,
        build_frame_selection_lifecycle_collector,
        build_projection_staging_sweeper,
        build_storage_inventory,
        build_worker_outbox,
    )
    from hc_data_platform.storage.inventory_worker import serve_inventory

    from .outbox_worker import serve_outbox

    outbox = (
        None
        if role == "media" or not object_store_configured
        else build_worker_outbox(
            settings,
            temporal_client=client,
            worker_instance_id=str(instance_identity.instance_id),
        )
    )
    inventory = (
        build_storage_inventory(settings)
        if role != "media" and object_store_configured and settings.storage_inventory_scopes
        else None
    )
    from hc_data_platform.lerobot_imports.cache import SourceCache

    source_cache_sweeper = SourceCache(
        Path(settings.alignment_staging_root) / "lerobot-cache",
        max_bytes=settings.lerobot_cache_max_bytes,
        ttl_seconds=settings.lerobot_cache_ttl_hours * 3600,
    )
    projection_staging_sweeper = (
        build_projection_staging_sweeper(settings)
        if role != "media" and object_store_configured
        else None
    )
    aligned_media_orphan_reconciler = (
        build_aligned_media_orphan_reconciler(settings)
        if role != "media" and object_store_configured
        else None
    )
    frame_selection_collector = (
        build_frame_selection_lifecycle_collector(settings)
        if role != "media" and object_store_configured and settings.outbox_scopes
        else None
    )
    aligned_media_version_retirement = (
        build_aligned_media_version_retirement_collector(settings)
        if role != "media" and object_store_configured and settings.outbox_scopes
        else None
    )
    staging_roots = (
        (Path(settings.aligned_media_staging_root),)
        if role == "media"
        else (
            Path(settings.aligned_media_staging_root),
            Path(settings.alignment_staging_root) / "projections",
        )
    )
    media_staging = MediaStagingSweeper(
        staging_roots,
        ttl=timedelta(hours=settings.aligned_media_staging_ttl_hours),
    )
    persistent_runtime_config = settings.environment in {"staging", "production"}
    persistent_instance_directory = persistent_platform_control
    boot_runtime_config = runtime_config_boot_values(
        media_maintenance_interval_seconds=settings.media_maintenance_interval_seconds,
        storage_inventory_interval_seconds=settings.storage_inventory_interval_seconds,
    )
    runtime_config_state = RuntimeConfigState(
        settings.platform_environment_id,
        baseline_values=boot_runtime_config,
    )
    runtime_config_service = RuntimeConfigService(
        (
            PostgresRuntimeConfigRepository.from_dsn(
                settings.postgres_dsn,
                baseline_values=boot_runtime_config,
            )
            if persistent_runtime_config
            else InMemoryRuntimeConfigRepository(baseline_values=boot_runtime_config)
        ),
        runtime_config_state,
        settings.platform_environment_id,
    )
    runtime_config_synchronizer = RuntimeConfigSynchronizer(
        runtime_config_service,
        (
            PostgresRuntimeConfigSubscriber(
                settings.postgres_dsn,
                settings.platform_environment_id,
            )
            if persistent_runtime_config
            else PollingRuntimeConfigSubscriber()
        ),
    )
    instance_heartbeater = PlatformInstanceHeartbeater(
        PlatformInstanceService(
            (
                PostgresPlatformInstanceRepository.from_dsn(
                    settings.postgres_dsn,
                    supports_runtime_config_revision=persistent_runtime_config,
                )
                if persistent_instance_directory
                else InMemoryPlatformInstanceRepository()
            ),
            instance_identity,
            applied_config_revision_provider=lambda: runtime_config_state.revision,
        )
    )
    shutdown_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(shutdown_signal, shutdown_requested.set)
            installed_signals.append(shutdown_signal)
        except (NotImplementedError, RuntimeError):
            log_event(
                logger,
                logging.WARNING,
                "PROCESS.SIGNAL_HANDLER_UNAVAILABLE",
            )
    await runtime_config_synchronizer.start()
    try:
        await instance_heartbeater.start(lambda: _worker_readiness(shutdown_requested))
        try:
            services: dict[str, Coroutine[Any, Any, None]] = {
                "media-maintenance": serve_media_maintenance(
                    scopes=settings.outbox_scopes if role != "media" else (),
                    staging_sweeper=media_staging,
                    interval_seconds=settings.media_maintenance_interval_seconds,
                    interval_seconds_provider=lambda: float(
                        runtime_config_state.value("scheduling.media_maintenance_interval_seconds")
                    ),
                    object_sweepers=(
                        tuple(
                            sweeper.run_once
                            for sweeper in (
                                source_cache_sweeper,
                                projection_staging_sweeper,
                                aligned_media_orphan_reconciler,
                            )
                            if sweeper is not None
                        )
                    ),
                    scoped_object_sweepers=(
                        tuple(
                            sweeper.run_once
                            for sweeper in (
                                frame_selection_collector,
                                aligned_media_version_retirement,
                            )
                            if sweeper is not None
                        )
                    ),
                    maintenance_gate=maintenance_gate,
                    environment_id=settings.platform_environment_id,
                    writer_id=f"media-maintenance:{instance_identity.instance_id}",
                ),
            }
            if outbox is not None:
                services["outbox-dispatcher"] = serve_outbox(
                    outbox.dispatcher,
                    scopes=outbox.scopes,
                    poll_interval_seconds=outbox.poll_interval_seconds,
                    batch_size=outbox.batch_size,
                    schedule_enqueuer=outbox.schedule_enqueuer,
                    maintenance_gate=maintenance_gate,
                    environment_id=outbox.environment_id,
                    writer_id=outbox.writer_id,
                    scope_provider=outbox.scope_provider,
                )
            if inventory is not None:
                services["storage-inventory"] = serve_inventory(
                    inventory,
                    interval_seconds_provider=lambda: float(
                        runtime_config_state.value("scheduling.storage_inventory_interval_seconds")
                    ),
                    maintenance_gate=maintenance_gate,
                    environment_id=settings.platform_environment_id,
                    writer_id=f"storage-inventory:{instance_identity.instance_id}",
                    task_lease_repository=task_lease_repository,
                    owner_instance_id=instance_identity.instance_id,
                )
            await _run_until_shutdown(
                worker,
                services,
                shutdown_requested,
                readiness_file=readiness_file,
            )
        finally:
            await instance_heartbeater.stop()
    finally:
        for shutdown_signal in installed_signals:
            loop.remove_signal_handler(shutdown_signal)
        await runtime_config_synchronizer.stop()
        if robot_executor is not None:
            robot_executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()

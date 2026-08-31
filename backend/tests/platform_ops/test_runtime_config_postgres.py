from __future__ import annotations

import asyncio
import multiprocessing
import os
from collections.abc import Iterator
from time import monotonic
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

from hc_data_platform.core.dbapi import normalize_postgres_dsn
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.platform_ops.instances import (
    InstanceReadinessSummary,
    PlatformInstanceIdentity,
    PlatformInstanceService,
    PostgresPlatformInstanceRepository,
)
from hc_data_platform.platform_ops.runtime_config import (
    RUNTIME_CONFIG_POLL_INTERVAL_SECONDS,
    PollingRuntimeConfigSubscriber,
    PostgresRuntimeConfigRepository,
    PostgresRuntimeConfigSubscriber,
    RuntimeConfigError,
    RuntimeConfigService,
    RuntimeConfigState,
    RuntimeConfigSynchronizer,
)

pytestmark = pytest.mark.integration
DIGEST = f"sha256:{'c' * 64}"


def _runtime_config_follower_process(
    dsn: str,
    environment_id: str,
    instance_id: str,
    node_name: str,
    subscriber_kind: str,
    ready_queue: Any,
    result_queue: Any,
) -> None:
    async def run() -> None:
        repository = PostgresRuntimeConfigRepository.from_dsn(dsn)
        state = RuntimeConfigState(environment_id)
        service = RuntimeConfigService(repository, state, environment_id)
        subscriber = (
            PostgresRuntimeConfigSubscriber(dsn, environment_id)
            if subscriber_kind == "notify"
            else PollingRuntimeConfigSubscriber()
        )
        synchronizer = RuntimeConfigSynchronizer(
            service,
            subscriber,
            poll_interval_seconds=(
                1 if subscriber_kind == "notify" else RUNTIME_CONFIG_POLL_INTERVAL_SECONDS
            ),
        )
        await synchronizer.start()
        ready_queue.put((node_name, state.revision))
        try:
            while state.revision < 1:
                await asyncio.sleep(0.01)
            record = await asyncio.to_thread(
                PlatformInstanceService(
                    PostgresPlatformInstanceRepository.from_dsn(dsn),
                    _identity(UUID(instance_id), node_name),
                    applied_config_revision_provider=lambda: state.revision,
                ).heartbeat,
                InstanceReadinessSummary(status="ready"),
            )
            result_queue.put(
                (
                    node_name,
                    state.revision,
                    state.current().content_sha256,
                    monotonic(),
                    record.applied_config_revision,
                )
            )
        finally:
            await synchronizer.stop()

    asyncio.run(run())


def _source_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


def _database_dsn(base_dsn: str, database_name: str) -> str:
    parsed = urlsplit(base_dsn)
    return parsed._replace(path=f"/{quote(database_name, safe='')}").geturl()


@pytest.fixture(scope="module")
def runtime_config_dsn() -> Iterator[str]:
    base_dsn = _source_dsn()
    database_name = f"hc_runtime_config_{uuid4().hex[:12]}"
    with psycopg.connect(base_dsn, autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("HC_TEST_POSTGRES_DSN role cannot create an isolated database")
    isolated_dsn = _database_dsn(base_dsn, database_name)
    try:
        asyncio.run(apply_migrations(isolated_dsn))
        yield isolated_dsn
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


def _identity(instance_id: UUID, node_name: str) -> PlatformInstanceIdentity:
    return PlatformInstanceIdentity(
        instance_id=instance_id,
        node_name=node_name,
        role="worker",
        release_id="platform-v0.1.0-runtime-config.1",
        release_manifest_digest=DIGEST,
        component_image_digest=DIGEST,
        runtime_version="CPython 3.12.11",
        pod_name=node_name,
        kubernetes_node_name="physical-node-a",
        kubernetes_zone="cn-east-1a",
    )


@pytest.mark.asyncio
async def test_postgres_notification_poll_fallback_immutability_and_applied_heartbeats(
    runtime_config_dsn: str,
) -> None:
    environment_id = "ha4-05-integration"
    repository = PostgresRuntimeConfigRepository.from_dsn(runtime_config_dsn)
    notified_state = RuntimeConfigState(environment_id)
    polling_state = RuntimeConfigState(environment_id)
    notified = RuntimeConfigSynchronizer(
        RuntimeConfigService(repository, notified_state, environment_id),
        PostgresRuntimeConfigSubscriber(runtime_config_dsn, environment_id),
        poll_interval_seconds=1,
    )
    notification_lost = RuntimeConfigSynchronizer(
        RuntimeConfigService(repository, polling_state, environment_id),
        PollingRuntimeConfigSubscriber(),
        poll_interval_seconds=RUNTIME_CONFIG_POLL_INTERVAL_SECONDS,
    )
    await notified.start()
    await notification_lost.start()
    try:
        started = monotonic()
        revision = await asyncio.to_thread(
            repository.publish,
            environment_id,
            expected_revision=0,
            schema_version="hc-runtime-config/v1",
            patch={
                "scheduling.media_maintenance_interval_seconds": 450,
                "ui.maintenance_banner_enabled": True,
            },
            actor_id="integration-operator",
            reason="exercise notification and fallback convergence",
            request_id="integration-request-1",
        )

        async def wait_for_notified_node() -> None:
            while notified_state.revision != 1:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_notified_node(), timeout=2)
        notification_elapsed = monotonic() - started

        async def wait_for_polling_node() -> None:
            while polling_state.revision != 1:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_polling_node(), timeout=31)
        elapsed = monotonic() - started
    finally:
        await notified.stop()
        await notification_lost.stop()

    assert revision.revision == 1
    assert notification_elapsed < 2
    assert elapsed <= 30.0
    assert notified_state.current().content_sha256 == polling_state.current().content_sha256

    instance_repository = PostgresPlatformInstanceRepository.from_dsn(runtime_config_dsn)
    notified_instance = PlatformInstanceService(
        instance_repository,
        _identity(UUID("00000000-0000-4000-8000-000000000451"), "worker-notified"),
        applied_config_revision_provider=lambda: notified_state.revision,
    ).heartbeat(InstanceReadinessSummary(status="ready"))
    polling_instance = PlatformInstanceService(
        instance_repository,
        _identity(UUID("00000000-0000-4000-8000-000000000452"), "worker-polling"),
        applied_config_revision_provider=lambda: polling_state.revision,
    ).heartbeat(InstanceReadinessSummary(status="ready"))
    applied_revisions = (
        notified_instance.applied_config_revision,
        polling_instance.applied_config_revision,
    )
    assert applied_revisions == (
        1,
        1,
    )

    with pytest.raises(RuntimeConfigError) as raised:
        repository.publish(
            environment_id,
            expected_revision=1,
            schema_version="hc-runtime-config/v1",
            patch={"database.secret": "must-not-persist"},
            actor_id="integration-operator",
            reason="exercise prohibited configuration rejection",
            request_id="integration-request-2",
        )
    assert raised.value.code == "PLATFORM_RUNTIME_CONFIG_FORBIDDEN_KEY"

    with psycopg.connect(runtime_config_dsn) as connection:
        assert connection.execute(
            """
            SELECT count(*)
            FROM platform.platform_runtime_config_revisions
            WHERE environment_id = %s
            """,
            (environment_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            """
            SELECT count(*)
            FROM platform.platform_runtime_config_events
            WHERE environment_id = %s
            """,
            (environment_id,),
        ).fetchone() == (1,)
        with pytest.raises(psycopg.errors.RaiseException, match="IMMUTABLE"):
            connection.execute(
                """
                UPDATE platform.platform_runtime_config_revisions
                SET reason = 'forbidden history rewrite'
                WHERE environment_id = %s AND revision = 1
                """,
                (environment_id,),
            )


def test_two_real_processes_converge_and_report_the_same_revision(
    runtime_config_dsn: str,
) -> None:
    environment_id = "ha4-05-multiprocess"
    context = multiprocessing.get_context("fork")
    ready_queue = context.Queue()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_runtime_config_follower_process,
            args=(
                runtime_config_dsn,
                environment_id,
                "00000000-0000-4000-8000-000000000461",
                "runtime-node-notify",
                "notify",
                ready_queue,
                result_queue,
            ),
        ),
        context.Process(
            target=_runtime_config_follower_process,
            args=(
                runtime_config_dsn,
                environment_id,
                "00000000-0000-4000-8000-000000000462",
                "runtime-node-poll",
                "poll",
                ready_queue,
                result_queue,
            ),
        ),
    ]
    for process in processes:
        process.start()
    try:
        ready = {ready_queue.get(timeout=15), ready_queue.get(timeout=15)}
        assert ready == {
            ("runtime-node-notify", 0),
            ("runtime-node-poll", 0),
        }
        publish_started = monotonic()
        published = PostgresRuntimeConfigRepository.from_dsn(runtime_config_dsn).publish(
            environment_id,
            expected_revision=0,
            schema_version="hc-runtime-config/v1",
            patch={"scheduling.storage_inventory_interval_seconds": 2_400},
            actor_id="multiprocess-operator",
            reason="prove independent process revision convergence",
            request_id="multiprocess-request-1",
        )
        results = {
            result[0]: result
            for result in (
                result_queue.get(timeout=31),
                result_queue.get(timeout=31),
            )
        }
    finally:
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        ready_queue.close()
        result_queue.close()

    assert published.revision == 1
    assert set(results) == {"runtime-node-notify", "runtime-node-poll"}
    notified = results["runtime-node-notify"]
    polled = results["runtime-node-poll"]
    assert notified[1:] == (
        1,
        published.content_sha256,
        notified[3],
        1,
    )
    assert polled[1:] == (
        1,
        published.content_sha256,
        polled[3],
        1,
    )
    assert notified[3] - publish_started < 2
    assert polled[3] - publish_started <= 30.0
    assert all(process.exitcode == 0 for process in processes)

    with psycopg.connect(runtime_config_dsn) as connection:
        rows = connection.execute(
            """
            SELECT node_name, applied_config_revision
            FROM platform.platform_instances
            WHERE instance_id IN (%s, %s)
            ORDER BY node_name
            """,
            (
                UUID("00000000-0000-4000-8000-000000000461"),
                UUID("00000000-0000-4000-8000-000000000462"),
            ),
        ).fetchall()
    assert rows == [
        ("runtime-node-notify", 1),
        ("runtime-node-poll", 1),
    ]

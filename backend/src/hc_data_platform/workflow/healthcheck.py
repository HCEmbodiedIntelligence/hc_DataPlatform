from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
from typing import Any

from .worker import DEFAULT_TASK_QUEUE, discover_temporal_registrations


def validate_worker_configuration() -> tuple[int, int]:
    """Validate registrations and the deployment-owned dependency factory without invoking it."""

    factory_path = os.getenv(
        "HC_WORKFLOW_ACTIVITY_FACTORY",
        "hc_data_platform.runtime:activity_dependencies",
    )
    module_name, separator, attribute = factory_path.partition(":")
    if not separator or not module_name or not attribute:
        raise RuntimeError(
            "HC_WORKFLOW_ACTIVITY_FACTORY must use the form 'package.module:factory'"
        )
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise RuntimeError("HC_WORKFLOW_ACTIVITY_FACTORY must resolve to a callable")

    workflows, activities = discover_temporal_registrations()
    if not workflows or not activities:
        raise RuntimeError("no Temporal workflows or activities are registered")
    return len(workflows), len(activities)


async def check_worker_pollers(
    *,
    target: str,
    namespace: str,
    task_queue: str,
) -> dict[str, object]:
    """Require Temporal health plus live workflow and activity pollers for this task queue."""

    from temporalio.api.enums.v1 import TaskQueueType
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
    from temporalio.client import Client

    workflow_count, activity_count = validate_worker_configuration()
    client = await Client.connect(target, namespace=namespace, lazy=True)
    if not await client.service_client.check_health():
        raise RuntimeError("Temporal health service reported not serving")

    poller_counts: dict[str, int] = {}
    queue_types = {
        "workflow": TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
        "activity": TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
    }
    for label, queue_type in queue_types.items():
        response: Any = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=namespace,
                task_queue=TaskQueue(name=task_queue),
                task_queue_type=queue_type,
                report_pollers=True,
            )
        )
        poller_counts[label] = len(response.pollers)
        if not response.pollers:
            raise RuntimeError(f"Temporal task queue has no {label} poller")

    return {
        "status": "ready",
        "target": target,
        "namespace": namespace,
        "task_queue": task_queue,
        "registered_workflows": workflow_count,
        "registered_activities": activity_count,
        "pollers": poller_counts,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HC Data Platform Worker readiness check")
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("HC_WORKER_HEALTHCHECK_TIMEOUT_SECONDS", "5")),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    os.kill(1, 0)
    target = os.getenv("HC_TEMPORAL_TARGET", "localhost:7233")
    namespace = os.getenv("HC_TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("HC_TEMPORAL_TASK_QUEUE", DEFAULT_TASK_QUEUE)
    result = asyncio.run(
        asyncio.wait_for(
            check_worker_pollers(
                target=target,
                namespace=namespace,
                task_queue=task_queue,
            ),
            timeout=args.timeout,
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

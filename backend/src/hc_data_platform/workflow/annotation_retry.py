"""Preview or start annotation-only recovery from a persisted processing receipt.

Run with python -m hc_data_platform.workflow.annotation_retry --help.
No data preparation, Episode commit, or ingest retry is performed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from typing import Any

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory

from .models import AutomaticAnnotationActivityInput


def stored_request(
    db: Any, scope: tuple[str, str, str], workflow_id: str
) -> AutomaticAnnotationActivityInput:
    row = db.execute(
        """SELECT result FROM workflow.jobs
        WHERE organization_id=%s AND project_id=%s AND workflow_id=%s AND status='SUCCEEDED'""",
        (*scope[:2], workflow_id),
    ).fetchone()
    if not row or not row[0].get("data_readable") or not row[0].get("annotation_retry"):
        raise ValueError("successful data receipt has no pending annotation retry")
    request = AutomaticAnnotationActivityInput.model_validate(row[0]["annotation_retry"])
    if (request.organization_id, request.project_id, request.region_code) != scope:
        raise ValueError("annotation retry receipt is outside the requested scope")
    return request


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    scope = (args.organization, args.project, args.region)
    token = bind_request_context(
        RequestContext(
            organization_id=scope[0],
            project_id=scope[1],
            region_code=scope[2],
            service_identity=True,
            subject_id="annotation-retry",
        )
    )
    try:
        with psycopg_connection_factory(settings.postgres_dsn)() as db:
            request = stored_request(db, scope, args.workflow)
    finally:
        reset_request_context(token)
    if not args.start:
        print(request.model_dump_json(indent=2))
        return
    if not args.request_id:
        raise ValueError("--start requires an explicit --request-id for idempotency")
    identity = hashlib.sha256(
        json.dumps([*scope, args.workflow, args.request_id], separators=(",", ":")).encode()
    ).hexdigest()
    client = await Client.connect(
        args.temporal or settings.temporal_target,
        namespace=args.namespace,
        data_converter=pydantic_data_converter,
    )
    handle = await client.start_workflow(
        "RetryEpisodeAnnotationWorkflow",
        request,
        id="annotation-retry-" + identity,
        task_queue=args.queue,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )
    print(json.dumps({"workflow_id": handle.id}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("organization", "project", "region", "workflow"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--queue", default="hc-data-pipeline")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--temporal")
    parser.add_argument("--request-id")
    parser.add_argument(
        "--start", action="store_true", help="start the reviewed annotation-only retry"
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()

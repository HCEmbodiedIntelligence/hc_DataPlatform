"""Disposable Temporal Worker used by the BE-12 process-kill system test."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from hc_data_platform.lance_catalog.models import DatasetVersionRef, DerivedReadyV1
from hc_data_platform.workflow.models import (
    CatalogCommitActivityInput,
    CatalogCommitActivityOutput,
)
from hc_data_platform.workflow.names import COMMIT_FRAGMENT_ACTIVITY


def _output(request: CatalogCommitActivityInput) -> CatalogCommitActivityOutput:
    manifest = request.fragment.manifest
    version = DatasetVersionRef(
        project_id=manifest.project_id,
        dataset_id=manifest.dataset_id,
        version=1,
        schema_snapshot_id=manifest.schema_snapshot_id,
        schema_fingerprint=manifest.schema_fingerprint,
        frequency_hz=manifest.frequency_hz,
        content_hash=manifest.content_hash,
        dataset_uri=f"lance://{manifest.project_id}/{manifest.dataset_id}",
        lance_version=1,
        storage_commit_id=manifest.content_hash,
        committed_rollouts=(manifest.rollout_id,),
        created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    ready = DerivedReadyV1(
        project_id=manifest.project_id,
        dataset_id=manifest.dataset_id,
        rollout_id=manifest.rollout_id,
        source_sha256=manifest.source_sha256,
        converter_version=manifest.converter_version,
        dataset_version=version.version,
        lance_version=version.lance_version,
        step_count=manifest.step_count,
        content_hash=manifest.content_hash,
    )
    return CatalogCommitActivityOutput(version=version, derived_ready=ready)


def _write_exclusive(path: Path, payload: bytes) -> bool:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


async def serve(target: str, task_queue: str, state_directory: Path) -> None:
    commit_path = state_directory / "immutable-commit.json"
    started_path = state_directory / "side-effect-complete"
    recovered_path = state_directory / "recovered.json"

    @activity.defn(name=COMMIT_FRAGMENT_ACTIVITY)
    async def commit_once(request: CatalogCommitActivityInput) -> CatalogCommitActivityOutput:
        output = _output(request)
        created = _write_exclusive(commit_path, output.model_dump_json().encode())
        if created:
            started_path.write_text("durable\n", encoding="utf-8")
            while True:
                activity.heartbeat({"stage": "lance_commit", "state": "durable-side-effect"})
                await asyncio.sleep(0.25)

        persisted = CatalogCommitActivityOutput.model_validate_json(commit_path.read_bytes())
        recovered_path.write_text(
            json.dumps(
                {
                    "attempt": activity.info().attempt,
                    "storage_commit_id": persisted.version.storage_commit_id,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return persisted

    client = await Client.connect(target, data_converter=pydantic_data_converter)
    worker = Worker(
        client,
        task_queue=task_queue,
        activities=[commit_once],
    )
    await worker.run()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--task-queue", required=True)
    parser.add_argument("--state-directory", required=True, type=Path)
    args = parser.parse_args()
    args.state_directory.mkdir(parents=True, exist_ok=True)
    asyncio.run(serve(args.target, args.task_queue, args.state_directory))


if __name__ == "__main__":
    main()

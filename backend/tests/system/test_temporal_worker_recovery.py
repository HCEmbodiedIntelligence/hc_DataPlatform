"""Kill a real Temporal Worker after a durable side effect and verify takeover."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from hc_data_platform.lance_catalog.models import AlignedFragmentManifestV1, StepRecord
from hc_data_platform.workflow.models import (
    CatalogCommitActivityInput,
    CatalogFragmentPayloadV1,
    DatasetWriterWorkflowInput,
    JobStatus,
    workflow_id,
)
from hc_data_platform.workflow.temporal_workflows import DatasetWriterWorkflow

SHA = "a" * 64


def _workflow_input() -> DatasetWriterWorkflowInput:
    step = StepRecord(
        rollout_id="worker-kill-rollout",
        step_index=0,
        timestamp_ns=0,
        modalities={"joint": [0.0]},
        source_timestamps_ns={"joint": 0},
        time_error_ns={"joint": 0},
        valid={"joint": True},
        repeated={"joint": False},
    )
    manifest = AlignedFragmentManifestV1(
        project_id="be12",
        dataset_id="worker-recovery",
        schema_snapshot_id="schema-v1",
        schema_fingerprint="b" * 64,
        frequency_hz=30,
        rollout_id=step.rollout_id,
        source_sha256="c" * 64,
        converter_version="be12-process-kill/1",
        attempt_id="attempt-1",
        fragment_uri="test://staging/worker-kill-rollout",
        step_count=1,
        content_hash=SHA,
    )
    return DatasetWriterWorkflowInput(
        project_id="be12",
        dataset_id="worker-recovery",
        resource_id="worker-recovery/worker-kill-rollout",
        commit=CatalogCommitActivityInput(
            fragment=CatalogFragmentPayloadV1(manifest=manifest, steps=(step,))
        ),
    )


async def _wait_for_file(path: Path, timeout_seconds: float = 15.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while not path.exists():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"timed out waiting for {path.name}")
        await asyncio.sleep(0.05)


async def _stop(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
    return await process.communicate()


async def _start_worker(
    *, target: str, task_queue: str, state_directory: Path
) -> asyncio.subprocess.Process:
    script = Path(__file__).with_name("temporal_worker_process.py")
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        "--target",
        target,
        "--task-queue",
        task_queue,
        "--state-directory",
        str(state_directory),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _exercise_worker_kill(state_directory: Path) -> None:
    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    first: asyncio.subprocess.Process | None = None
    recovery: asyncio.subprocess.Process | None = None
    try:
        target = environment.client.service_client.config.target_host
        task_queue = "be12-worker-process-recovery"
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[DatasetWriterWorkflow],
        ):
            first = await _start_worker(
                target=target,
                task_queue=task_queue,
                state_directory=state_directory,
            )
            handle = await environment.client.start_workflow(
                DatasetWriterWorkflow.run,
                _workflow_input(),
                id=workflow_id(
                    "dataset-writer",
                    "be12",
                    "worker-recovery/worker-kill-rollout",
                ),
                task_queue=task_queue,
            )
            await _wait_for_file(state_directory / "side-effect-complete")

            first.kill()
            await first.wait()
            assert first.returncode is not None and first.returncode < 0

            recovery = await _start_worker(
                target=target,
                task_queue=task_queue,
                state_directory=state_directory,
            )
            result = await asyncio.wait_for(handle.result(), timeout=45)
        assert result.status is JobStatus.SUCCEEDED
        assert result.result is not None
        assert result.result["dataset_version"]["version"] == 1

        recovery_record = json.loads(
            (state_directory / "recovered.json").read_text(encoding="utf-8")
        )
        assert recovery_record["attempt"] >= 2
        assert recovery_record["storage_commit_id"] == SHA
        assert [path.name for path in state_directory.glob("immutable-commit*.json")] == [
            "immutable-commit.json"
        ]
    finally:
        diagnostics: list[str] = []
        for process in (first, recovery):
            if process is not None:
                stdout, stderr = await _stop(process)
                diagnostics.extend((stdout.decode(), stderr.decode()))
        await environment.shutdown()
        if any("Traceback" in item for item in diagnostics):
            message = "worker subprocess emitted a traceback:\n" + "\n".join(diagnostics)
            raise AssertionError(message)


def test_real_temporal_worker_process_kill_recovers_one_commit(tmp_path: Path) -> None:
    asyncio.run(_exercise_worker_kill(tmp_path))

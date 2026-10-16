"""Production recording workflow: healthy slow codec, then real process loss."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from hc_data_platform.robot_ingest.recording_workflow import RobotRecordingWorkflow


async def exercise(directory: Path):
    target = os.getenv("HC_TEST_TEMPORAL_TARGET")
    environment = None
    if not target:
        environment = await WorkflowEnvironment.start_time_skipping()
        target = environment.client.service_client.config.target_host
    client = await Client.connect(target)
    queue = "recording-recovery-test-" + uuid4().hex
    children = []
    handle = None
    script = Path(__file__).with_name("recording_recovery_worker.py")

    async def start(block=False):
        command = [sys.executable, str(script), "--target", target, "--queue", queue,
                   "--directory", str(directory)]
        if block:
            command.append("--block")
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE)
        children.append(process)
        return process

    try:
        async with Worker(client, task_queue=queue, workflows=[RobotRecordingWorkflow]):
            first = await start(block=True)
            handle = await client.start_workflow(RobotRecordingWorkflow.run, {
                "event_id": str(uuid4()), "event_type": "robot.recording.register.requested.v1",
                "aggregate_type": "raw_source", "aggregate_id": queue,
                "organization_id": "recovery-test", "project_id": "recovery-test",
                "region_code": "local", "payload": {"upload_id": queue},
            }, id=queue, task_queue=queue)
            deadline = time.monotonic() + 20
            while not (directory / "started").exists():
                assert time.monotonic() < deadline and first.returncode is None
                await asyncio.sleep(.1)
            # Longer than the configured timeout: no frame progress, still healthy.
            await asyncio.sleep(35)
            pending = (await handle.describe()).raw_description.pending_activities
            assert len(pending) == 1 and pending[0].attempt == 1
            first.kill()
            await first.wait()
            killed_at = time.monotonic()
            await start()
            await asyncio.wait_for(handle.result(), timeout=45)
            recovery_seconds = time.monotonic() - killed_at
            recovered = json.loads((directory / "recovered.json").read_text())
            assert recovered["attempt"] == 2
            assert len(list(directory.glob("immutable-receipt*.json"))) == 1
            assert recovered["receipt"]["source"] == queue
            evidence = dict(workflow_id=queue, healthy_without_progress_seconds=35,
                            killed_exit_code=first.returncode, recovery_seconds=recovery_seconds,
                            attempt=recovered["attempt"], immutable_receipt_count=1,
                            automatic_recovery=True)
            (directory / "evidence.json").write_text(json.dumps(evidence, indent=2))
            print(json.dumps(evidence))
    finally:
        if handle and (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
            await handle.terminate("Isolated recovery test cleanup")
        for child in children:
            if child.returncode is None:
                child.kill()
            await child.communicate()
        if environment:
            await environment.shutdown()


def test_recording_worker_process_loss_automatically_resumes(tmp_path):
    asyncio.run(exercise(tmp_path))

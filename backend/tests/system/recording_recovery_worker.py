"""Isolated activity process; never stops the deployment's shared worker."""

import argparse
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from hc_data_platform.robot_ingest.recording_bridge import RecordingOutboxHandler


class ProbeRecordingHandler(RecordingOutboxHandler):
    def __init__(self, directory: Path, block: bool):
        super().__init__(
            None, None, registrations=None, execution_scope=lambda *a, **kw: nullcontext()
        )
        self.directory, self.block = directory, block

    def adopt(self, event, progress):
        receipt = self.directory / "immutable-receipt.json"
        if not receipt.exists():
            with receipt.open("x") as f:
                json.dump({"source": event.aggregate_id, "recording": "same-recording"}, f)
        (self.directory / "started").write_text(str(activity.info().attempt))
        if self.block:
            # Simulates a long codec call that cannot report frame progress.
            # The production activity's independent heartbeat must keep it alive.
            while True:
                time.sleep(1)
        progress()
        assert json.loads(receipt.read_text())["source"] == event.aggregate_id
        (self.directory / "recovered.json").write_text(json.dumps({
            "attempt": activity.info().attempt, "receipt": json.loads(receipt.read_text())
        }))


async def main(args):
    client = await Client.connect(args.target)
    handler = ProbeRecordingHandler(args.directory, args.block)
    @activity.defn(name="robot.recording.failed")
    async def record_failure(event: dict):
        (args.directory / "failed.json").write_text(json.dumps(event))
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            client, task_queue=args.queue, activities=[handler.prepare, record_failure],
            activity_executor=executor,
        ):
            await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--queue", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--block", action="store_true")
    asyncio.run(main(parser.parse_args()))

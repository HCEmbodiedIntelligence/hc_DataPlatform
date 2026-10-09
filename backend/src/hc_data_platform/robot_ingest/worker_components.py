"""Robot worker factories assembled by runtime, independent of a pipeline's internals."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from hc_data_platform.core.execution import ExecutionScope
from hc_data_platform.object_storage.ports import JsonObjectStore

if TYPE_CHECKING:
    from .lerobot_processor import NativeLeRobotProcessor
    from .processing_store import ProcessingStore
    from .recording_bridge import RecordingOutboxHandler


class NativeProcessorFactory(Protocol):
    def __call__(
        self, client: Any, loop: asyncio.AbstractEventLoop, *, task_queue: str
    ) -> NativeLeRobotProcessor: ...


class RecordingActivityFactory(Protocol):
    def __call__(self, *, execution_scope: ExecutionScope) -> RecordingOutboxHandler: ...


@dataclass(frozen=True)
class RobotWorkerComponents:
    processing: ProcessingStore
    storage: JsonObjectStore
    native_processor: NativeProcessorFactory
    recording_activity: RecordingActivityFactory

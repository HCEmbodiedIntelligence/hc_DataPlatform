"""C Worker registration components. B is injected, never replaced with simulated success."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager, suppress
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from temporalio import activity
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.ingest.ports import ObjectStoragePort

from .models import QualityStatus
from .processing_contract import (
    CommittedLeRobotProcessor,
    CommittedSource,
    EpisodeProgress,
    EpisodeReceipt,
    ProcessingDocument,
    ProcessingFailure,
    ProcessorUnavailable,
    SourceEpisode,
)
from .processing_store import EVENT_TYPE, WORKFLOW_NAME, ProcessingStore, ProcessingTask


@contextmanager
def processing_scope(task: ProcessingTask):
    token = bind_request_context(
        RequestContext(
            organization_id=task.organization_id,
            project_id=task.project_id,
            region_code=task.region_code,
            subject_id="robot-processing-worker",
            service_identity=True,
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


class RobotProcessingActivities:
    def __init__(
        self,
        store: ProcessingStore,
        storage: ObjectStoragePort,
        processor: CommittedLeRobotProcessor | None = None,
    ) -> None:
        self.store, self.storage = store, storage
        self.processor = processor if processor is not None else ProcessorUnavailable()

    @property
    def activities(self) -> list[Callable]:
        return [self.discover, self.episode, self.fail, self.finish]

    @contextmanager
    def scope(self, request: dict[str, Any]):
        from hc_data_platform.workflow.activities import _worker_scope

        task = ProcessingTask.model_validate(request["task"])
        with _worker_scope(task.project_id, task.region_code, task.raw_source_id,
                           organization_id=task.organization_id):
            try:
                yield task
            except ProcessingFailure as exc:
                # Controlled business failure is persisted by the workflow. Transient
                # storage/database exceptions use Temporal's bounded activity retry.
                raise ApplicationError(
                    exc.code, {"retryable": exc.retryable}, type=exc.code, non_retryable=True
                ) from exc
            except Exception:
                # Third-party exceptions may embed signed URLs. Temporal persists
                # and logs activity failures, so cross that boundary with a code only.
                raise ApplicationError(
                    "Robot processing operation failed",
                    {"retryable": True},
                    type="ROBOT_PROCESSING_TECHNICAL_FAILURE",
                ) from None

    @activity.defn(name="robot.processing.discover")
    def discover(self, request: dict[str, Any]) -> list[int]:
        with self.scope(request) as task:
            upload, doc = self.store.load(task)
            if doc.phase == "DONE":
                return []
            if not doc.episodes:
                self.store.change(
                    task, lambda d: d.model_copy(update={"phase": "DISCOVERING_EPISODES"})
                )
                discovered = tuple(
                    SourceEpisode.model_validate(ep)
                    for ep in self.processor.discover(CommittedSource(upload, self.storage))
                )
                indexes = [ep.source_episode_index for ep in discovered]
                ids = [ep.source_episode_id for ep in discovered]
                if (
                    not discovered
                    or len(discovered) > 10_000
                    or len(set(indexes)) != len(indexes)
                    or len(set(ids)) != len(ids)
                ):
                    raise ProcessingFailure("ROBOT_SOURCE_EPISODES_INVALID")
                episodes = tuple(
                    EpisodeProgress(
                        **ep.model_dump(),
                        episode_id="rie-"
                        + uuid5(
                            NAMESPACE_URL,
                            f"{task.organization_id}:{task.raw_source_id}:{ep.source_episode_index}",
                        ).hex,
                    )
                    for ep in sorted(discovered, key=lambda e: e.source_episode_index)
                )

                def save(current: ProcessingDocument) -> ProcessingDocument:
                    if current.episodes:
                        old = [
                            (e.source_episode_index, e.source_episode_id) for e in current.episodes
                        ]
                        if old != [(e.source_episode_index, e.source_episode_id) for e in episodes]:
                            raise ProcessingFailure("ROBOT_SOURCE_EPISODES_CHANGED")
                        return current
                    return current.model_copy(update={"phase": "PROCESSING", "episodes": episodes})

                doc = self.store.change(task, save)
            else:
                doc = self.store.change(
                    task, lambda d: d.model_copy(update={"phase": "PROCESSING"})
                )
            return [
                ep.source_episode_index
                for ep in doc.episodes
                if ep.status in {"PENDING", "PROCESSING"}
            ]

    @activity.defn(name="robot.processing.episode")
    def episode(self, request: dict[str, Any]) -> None:
        with self.scope(request) as task:
            index = int(request["index"])
            upload, doc = self.store.load(task)
            ep = next(e for e in doc.episodes if e.source_episode_index == index)
            if ep.status in {"READY", "FAILED"}:
                return

            def running(d: ProcessingDocument) -> ProcessingDocument:
                return d.model_copy(
                    update={
                        "episodes": tuple(
                            e.model_copy(update={"status": "PROCESSING"})
                            if e.source_episode_index == index and e.status == "PENDING"
                            else e
                            for e in d.episodes
                        )
                    }
                )

            self.store.change(task, running)
            activity.heartbeat({"source_episode_index": index})
            receipt = EpisodeReceipt.model_validate(
                self.processor.process(
                    CommittedSource(upload, self.storage),
                    SourceEpisode(
                        source_episode_id=ep.source_episode_id, source_episode_index=index
                    ),
                    episode_id=ep.episode_id,
                    attempt_id=str(uuid5(NAMESPACE_URL, f"{task.workflow_id}:{index}")),
                )
            )

            def save(d: ProcessingDocument) -> ProcessingDocument:
                return d.model_copy(
                    update={
                        "episodes": tuple(
                            e.model_copy(
                                update={
                                    "status": "READY",
                                    "quality_status": QualityStatus(receipt.quality_status),
                                    "receipt": receipt,
                                    "error_code": None,
                                    "retryable": False,
                                }
                            )
                            if e.source_episode_index == index and e.status != "READY"
                            else e
                            for e in d.episodes
                        )
                    }
                )

            self.store.change(task, save)

    @activity.defn(name="robot.processing.fail")
    def fail(self, request: dict[str, Any]) -> None:
        with self.scope(request) as task:

            def save(doc: ProcessingDocument) -> ProcessingDocument:
                if doc.phase == "DONE":
                    return doc
                error = request["error_code"]
                # Never persist exception text (may contain credentials/signed URLs).
                import re

                if not isinstance(error, str) or not re.fullmatch(r"[A-Z0-9_]{1,128}", error):
                    error = "ROBOT_PROCESSING_TECHNICAL_FAILURE"
                retryable = bool(request["retryable"])
                if request.get("index") is None:
                    return doc.model_copy(
                        update={
                            "phase": "DONE",
                            "error_code": error,
                            "retryable": retryable,
                            "episodes": tuple(
                                ep.model_copy(
                                    update={
                                        "status": "FAILED",
                                        "error_code": error,
                                        "retryable": retryable,
                                    }
                                )
                                if ep.status in {"PENDING", "PROCESSING"}
                                else ep
                                for ep in doc.episodes
                            ),
                        }
                    )
                return doc.model_copy(
                    update={
                        "episodes": tuple(
                            ep.model_copy(
                                update={
                                    "status": "FAILED",
                                    "error_code": error,
                                    "retryable": retryable,
                                }
                            )
                            if ep.source_episode_index == request["index"] and ep.status != "READY"
                            else ep
                            for ep in doc.episodes
                        )
                    }
                )

            self.store.change(task, save)

    @activity.defn(name="robot.processing.finish")
    def finish(self, request: dict[str, Any]) -> None:
        with self.scope(request) as task:

            def finish(doc: ProcessingDocument) -> ProcessingDocument:
                if any(e.status in {"PENDING", "PROCESSING"} for e in doc.episodes):
                    raise RuntimeError("cannot finish while episodes are active")
                return doc.model_copy(update={"phase": "DONE"})

            self.store.change(task, finish)


class RobotProcessingOutboxHandler:
    def __init__(self, client: Any, store: ProcessingStore, *, task_queue: str) -> None:
        self.client, self.store, self.task_queue = client, store, task_queue

    async def __call__(self, event: DomainEventEnvelope) -> None:
        task = ProcessingTask.model_validate(event.payload)
        if (
            event.event_type,
            event.organization_id,
            event.project_id,
            event.region_code,
            event.aggregate_id,
        ) != (
            EVENT_TYPE,
            task.organization_id,
            task.project_id,
            task.region_code,
            task.raw_source_id,
        ):
            raise ValueError("robot processing event scope mismatch")
        with processing_scope(task):
            try:
                self.store.load(task)
            except ProcessingFailure as exc:
                if exc.code == "ROBOT_PROCESSING_STALE_ATTEMPT":
                    return  # Obsolete outbox delivery is acknowledged, never re-executed.
                raise
        # Start succeeded but an outbox acknowledgement may have been lost.
        with suppress(WorkflowAlreadyStartedError):
            await self.client.start_workflow(
                WORKFLOW_NAME,
                task.model_dump(),
                id=task.workflow_id,
                task_queue=self.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )

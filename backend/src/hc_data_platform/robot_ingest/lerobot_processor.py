"""Committed robot sources through the real native QC/media/Episode workflow.

Runs in the robot activity queue, never in the native ingest activity pool.
Temporal identity survives activity/worker restarts; durable successful jobs also
recover a receipt after an explicit processing retry changes the attempt ID.
"""

from __future__ import annotations

import asyncio
import re
from concurrent.futures import TimeoutError
from typing import Any

from temporalio import activity
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.episode_store.catalog_models import (
    CollectionVersion,
    EpisodeDerivedReady,
    validate_commit_pair,
)
from hc_data_platform.episode_store.ingest_ports import PublishedPackageReceipts
from hc_data_platform.ingest.raw_sources import RawSourceRepositoryPort
from hc_data_platform.lerobot_imports.committed import discover_committed_source
from hc_data_platform.lerobot_imports.pipeline import LeRobotPipeline
from hc_data_platform.quality.ports import QualityReportReader
from hc_data_platform.workflow.models import JobRecord
from hc_data_platform.workflow.names import INGEST_ROLLOUT_WORKFLOW
from hc_data_platform.workflow.result_queries import TerminalWorkflowResults

from .processing_contract import CommittedSource, EpisodeReceipt, ProcessingFailure, SourceEpisode


def robot_task_queue(native_queue: str) -> str:
    return f"{native_queue}-robot-processing"


class NativeLeRobotProcessor:
    def __init__(
        self,
        pipeline: LeRobotPipeline,
        client: Any,
        loop: asyncio.AbstractEventLoop,
        *,
        task_queue: str,
        raw_sources: RawSourceRepositoryPort,
        quality_reports: QualityReportReader,
        workflow_results: TerminalWorkflowResults,
        publication_receipts: PublishedPackageReceipts,
    ) -> None:
        self.pipeline, self.client, self.loop = pipeline, client, loop
        self.task_queue = task_queue
        self.raw_sources = raw_sources
        self.quality_reports = quality_reports
        self.workflow_results = workflow_results
        self.publication_receipts = publication_receipts

    def _discover(self, source: CommittedSource):
        u = source.upload
        raw = self.raw_sources.get_source(
            organization_id=u.target.organization_id,
            project_id=u.target.project_id,
            region_code=u.target.region_code,
            raw_source_id=u.raw_source_id,
        )
        if raw is None or raw.upload_id != u.upload_id:
            raise ProcessingFailure("ROBOT_PROCESSING_SOURCE_NOT_FOUND")
        try:
            return discover_committed_source(source.storage, raw)
        except (ValueError, KeyError, IndexError) as exc:
            code = str(exc).split(":", 1)[0]
            if not re.fullmatch(r"(?:LEROBOT|OPENARM)_[A-Z0-9_]+", code):
                code = "LEROBOT_SOURCE_INVALID"
            raise ProcessingFailure(code) from None

    def discover(self, source: CommittedSource) -> tuple[SourceEpisode, ...]:
        return tuple(
            SourceEpisode(
                source_episode_id=e.get("source_episode_id", f"episode-{e['episode_index']}"),
                source_episode_index=e["episode_index"],
            )
            for e in self._discover(source).episodes
        )

    def _receipt(
        self, task: Any, episode_id: str, metadata: dict, status: str, result: dict
    ) -> EpisodeReceipt:
        report = self.quality_reports.get_report(
            project_id=task.project_id, region_code=task.region_code, rollout_id=episode_id
        )
        if (
            report is None
            or result.get("quality", {}).get("content_sha256") != report.content_sha256
        ):
            raise ProcessingFailure("ROBOT_QC_RECEIPT_MISSING", retryable=True)
        derived = result.get("derived") or {}
        package_receipt = None
        native = derived.get("data") is not None
        has_cameras = bool((result.get("frame_selection") or {}).get("camera_set") or metadata.get("cameras"))
        if status == "SUCCEEDED" and (
            report.status.value != "PASS"
            or (
                result.get("data_readable") is not True
                if native
                else not result.get("training_eligible")
            )
            or derived.get("rollout_id") != episode_id
            or not result.get("annotation_task")
            or (result.get("aligned_media_count", 0) < 1 if has_cameras else False)
        ):
            raise ProcessingFailure("ROBOT_PUBLICATION_RECEIPT_INCOMPLETE", retryable=True)
        if native:
            try:
                ready = EpisodeDerivedReady.model_validate(derived)
                collection = CollectionVersion.model_validate(result["dataset_version"])
                validate_commit_pair(collection, ready)
                data = ready.data
                if (
                    data.identity.scope != (task.organization_id, task.project_id, task.region_code)
                    or data.identity.dataset_id != task.dataset_id
                    or data.identity.rollout_id != episode_id
                    or derived.get("lance_version") is not None
                ):
                    raise ValueError("robot package scope mismatch")
                package_receipt = self.publication_receipts.published_receipt(data, collection)
            except (ValueError, KeyError, PermissionError):
                raise ProcessingFailure(
                    "ROBOT_PUBLICATION_RECEIPT_INCOMPLETE", retryable=True
                ) from None
        return EpisodeReceipt(
            quality_status=report.status.value,
            frame_count=metadata["length"],
            sample_count=derived.get("step_count", 0),
            dataset_version=derived.get("dataset_version"),
            lance_version=derived.get("lance_version"),
            package_receipt=package_receipt,
            qc_report_id=f"qc_{report.content_sha256[:32]}",
        )

    def process(
        self, source: CommittedSource, episode: SourceEpisode, *, episode_id: str, attempt_id: str
    ) -> EpisodeReceipt:
        discovered = self._discover(source)
        index = episode.source_episode_index
        task = discovered.plan.episode_tasks[index]
        metadata = discovered.episodes[index]
        if metadata.get("source_episode_id", f"episode-{index}") != episode.source_episode_id:
            raise ProcessingFailure("ROBOT_SOURCE_EPISODES_CHANGED")
        task = task.model_copy(
            update={
                "source": task.source.model_copy(
                    update={
                        "platform_episode_id": episode_id,
                    }
                )
            }
        )
        # A publication may have finished while C's receipt transaction failed.
        # Read only a terminal real ingest job for this immutable rollout identity.
        terminal = self.workflow_results.latest_terminal(
            organization_id=task.organization_id,
            project_id=task.project_id,
            resource_id=episode_id,
            job_type=INGEST_ROLLOUT_WORKFLOW,
        )
        if terminal is not None:
            return self._receipt(task, episode_id, metadata, terminal.status, terminal.result)
        try:
            request = self.pipeline.prepare(task)
        except ProblemException as exc:
            raise ProcessingFailure(exc.problem.code, retryable=exc.problem.retryable) from None

        async def execute() -> JobRecord:
            identity = f"robot-native-{episode_id}-{attempt_id}"
            try:
                handle = await self.client.start_workflow(
                    INGEST_ROLLOUT_WORKFLOW,
                    request,
                    id=identity,
                    task_queue=self.task_queue,
                    result_type=JobRecord,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
            except WorkflowAlreadyStartedError:
                handle = self.client.get_workflow_handle(identity, result_type=JobRecord)
            return await handle.result()

        pending = asyncio.run_coroutine_threadsafe(execute(), self.loop)
        try:
            while True:
                try:
                    job = pending.result(timeout=10)
                    break
                except TimeoutError:
                    activity.heartbeat({"episode_id": episode_id, "attempt_id": attempt_id})
                    if activity.is_cancelled():
                        raise ProcessingFailure(
                            "ROBOT_PROCESSING_CANCELLED", retryable=True
                        ) from None
        finally:
            # Cancel only the local wait. The durable workflow remains recoverable.
            if not pending.done():
                pending.cancel()
        if job.status.value not in {"SUCCEEDED", "QUALITY_RISK", "QUALITY_REJECTED"}:
            code = job.error_code or "ROBOT_NATIVE_PROCESSING_FAILED"
            if not re.fullmatch(r"[A-Z0-9_]{1,128}", code):
                code = "ROBOT_NATIVE_PROCESSING_FAILED"
            raise ProcessingFailure(code, retryable=True)
        return self._receipt(task, episode_id, metadata, job.status.value, job.result or {})

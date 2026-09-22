"""C orchestration port for B's committed-source processor; no LeRobot reader here."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field, model_validator

from hc_data_platform.ingest.ports import ObjectStoragePort

from .models import QualityStatus, RobotIngestUpload, StrictModel


class ProcessingFailure(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        if not re.fullmatch(r"[A-Z0-9_]{1,128}", code):
            raise ValueError("processing error codes must be bounded uppercase identifiers")
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class SourceEpisode(StrictModel):
    source_episode_id: str = Field(min_length=1, max_length=512)
    source_episode_index: int = Field(ge=0, lt=10_000)


class EpisodeReceipt(StrictModel):
    """B returns only after durable data/media/QC publication (idempotent by episode_id).

    RISK/REJECT are completed quality decisions, not transient technical errors.
    Dataset/Lance versions are required for PASS; rejected data may lack versions.
    """

    quality_status: Literal["PASS", "RISK", "REJECT"]
    frame_count: int = Field(gt=0)
    sample_count: int = Field(ge=0)
    dataset_version: int | None = Field(default=None, gt=0)
    lance_version: int | None = Field(default=None, gt=0)
    qc_report_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")

    @model_validator(mode="after")
    def published_pass(self) -> EpisodeReceipt:
        if self.quality_status == "PASS" and (
            self.dataset_version is None or self.lance_version is None
        ):
            raise ValueError("PASS requires durable dataset and Lance versions")
        return self


@dataclass(frozen=True)
class CommittedSource:
    upload: RobotIngestUpload
    storage: ObjectStoragePort

    def read(self, path: str) -> bytes:
        return b"".join(self.read_chunks(path))

    def read_chunks(self, path: str) -> Iterator[bytes]:
        """Only allow committed object locators; never interpret client local paths."""
        matches = [asset for asset in self.upload.assets if asset.path == path]
        if len(matches) != 1:
            raise ProcessingFailure("ROBOT_SOURCE_ASSET_MISSING")
        asset = matches[0]
        digest = hashlib.sha256()
        size = 0
        for chunk in self.storage.read_chunks(asset.object_key):
            digest.update(chunk)
            size += len(chunk)
            yield chunk
        if (size, digest.hexdigest()) != (
            asset.expected_size_bytes,
            asset.expected_sha256,
        ):
            raise ProcessingFailure("ROBOT_SOURCE_ASSET_CHANGED")


class CommittedLeRobotProcessor(Protocol):
    def discover(self, source: CommittedSource) -> tuple[SourceEpisode, ...]:
        """Validate immutable package; map capture-context/source_mapping, not declarations."""
        ...

    def process(
        self,
        source: CommittedSource,
        episode: SourceEpisode,
        *,
        episode_id: str,
        attempt_id: str,
    ) -> EpisodeReceipt:
        """Use B's common pipeline; replay must return the existing durable receipt.

        episode_id stays constant across all retries. attempt_id changes only on
        explicit retry, remains constant for activity retries and Worker restarts.
        Implementations must heartbeat long work via Temporal's activity context.
        """
        ...


class ProcessorUnavailable:
    def discover(self, source: CommittedSource) -> tuple[SourceEpisode, ...]:
        # Check actual immutable bytes before reporting the unavailable parser.
        source.read("meta/info.json")
        raise ProcessingFailure("ROBOT_PROCESSOR_UNAVAILABLE", retryable=True)

    def process(self, *args: object, **kwargs: object) -> EpisodeReceipt:
        raise ProcessingFailure("ROBOT_PROCESSOR_UNAVAILABLE", retryable=True)


class EpisodeProgress(SourceEpisode):
    episode_id: str
    status: Literal["PENDING", "PROCESSING", "READY", "FAILED"] = "PENDING"
    quality_status: QualityStatus = QualityStatus.PENDING
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,128}$")
    retryable: bool = False
    receipt: EpisodeReceipt | None = None


class ProcessingDocument(StrictModel):
    phase: Literal["PENDING", "DISCOVERING_EPISODES", "PROCESSING", "DONE"] = "PENDING"
    episodes: tuple[EpisodeProgress, ...] = ()
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,128}$")
    retryable: bool = False


class EpisodeProcessingResult(SourceEpisode):
    episode_id: str | None
    dataset_id: str | None
    status: Literal["PENDING", "PROCESSING", "READY", "FAILED"]
    quality_status: QualityStatus
    error_code: str | None
    retryable: bool
    next_action: Literal[
        "wait", "open_episode", "retry_processing", "repair_export", "review_quality"
    ]


class ProcessingResult(StrictModel):
    schema_version: Literal["openarm-processing-result/v1"] = "openarm-processing-result/v1"
    upload_id: str
    raw_source_id: str
    processing_status: Literal[
        "PENDING", "DISCOVERING_EPISODES", "PROCESSING", "READY", "PARTIALLY_FAILED", "FAILED"
    ]
    quality_status: QualityStatus
    terminal: bool
    poll_after_seconds: int
    episodes: tuple[EpisodeProcessingResult, ...]


def processing_result(upload: RobotIngestUpload, doc: ProcessingDocument) -> ProcessingResult:
    terminal = doc.phase == "DONE"
    statuses = {ep.status for ep in doc.episodes}
    if not terminal:
        status = doc.phase
    elif doc.error_code and not doc.episodes or statuses == {"FAILED"}:
        status = "FAILED"
    elif "FAILED" in statuses:
        status = "PARTIALLY_FAILED"
    elif statuses == {"READY"}:
        status = "READY"
    else:
        status = "FAILED"  # An empty discovery can never be READY.
    qualities = {ep.quality_status for ep in doc.episodes}
    quality = (
        "REJECT"
        if "REJECT" in qualities
        else "RISK"
        if "RISK" in qualities
        else "PASS"
        if "PASS" in qualities
        else "PENDING"
    )
    if (
        quality == "PASS"
        and doc.episodes
        and upload.declared_episode_count is not None
        and len(doc.episodes) != upload.declared_episode_count
    ):
        quality = "RISK"
    return ProcessingResult(
        upload_id=upload.upload_id,
        raw_source_id=upload.raw_source_id,
        processing_status=status,
        quality_status=quality,
        terminal=terminal,
        poll_after_seconds=0 if terminal else 5,
        episodes=tuple(
            EpisodeProcessingResult(
                source_episode_id=ep.source_episode_id,
                source_episode_index=ep.source_episode_index,
                episode_id=ep.episode_id,
                dataset_id=upload.target.dataset_id,
                status=ep.status,
                quality_status=ep.quality_status,
                error_code=ep.error_code,
                retryable=ep.status == "FAILED" and ep.retryable,
                next_action=(
                    "wait"
                    if ep.status in {"PENDING", "PROCESSING"}
                    else "review_quality"
                    if ep.quality_status in {"RISK", "REJECT"}
                    else "open_episode"
                    if ep.status == "READY"
                    else "retry_processing"
                    if ep.retryable
                    else "repair_export"
                ),
            )
            for ep in doc.episodes
        ),
    )

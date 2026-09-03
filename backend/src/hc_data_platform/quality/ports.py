"""Persistence and detector ports for BE-06 quality evaluation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, Protocol, runtime_checkable

from .models import (
    ImageObservation,
    QcReportV1,
    QualityInputV1,
    QualityProfileV1,
    QualityStreamObservationV1,
    QualitySummaryV1,
)


class QualityPersistenceError(RuntimeError):
    """A technical persistence failure, never a PASS/RISK/REJECT conclusion."""

    def __init__(self, stage: Literal["report", "metadata"], message: str) -> None:
        super().__init__(message)
        self.stage = stage


@runtime_checkable
class QualityEvaluationPort(Protocol):
    def evaluate(self, data: QualityInputV1, profile: QualityProfileV1) -> QcReportV1: ...

    def evaluate_stream(
        self,
        data: QualityInputV1,
        observations: Iterable[QualityStreamObservationV1],
        profile: QualityProfileV1,
    ) -> QcReportV1: ...


@runtime_checkable
class ReportSink(Protocol):
    """Stores the complete report by content hash without overwriting it."""

    def put_immutable(self, report: QcReportV1) -> None: ...


@runtime_checkable
class MetadataSink(Protocol):
    """Updates the queryable latest-summary pointer after report persistence."""

    def put_summary(self, summary: QualitySummaryV1) -> None: ...


@runtime_checkable
class QualityProfileStore(Protocol):
    """Stores immutable profile versions in project scope."""

    def put_immutable(self, project_id: str, profile: QualityProfileV1) -> None: ...

    def get(
        self, project_id: str, profile_id: str, profile_version: int
    ) -> QualityProfileV1 | None: ...


@runtime_checkable
class VisualAnomalyProbe(Protocol):
    """Image decoding/detection adapter; encoded bytes never enter the rule engine."""

    def inspect(self, *, timestamp_ns: int, encoded_image: bytes) -> ImageObservation: ...


class FakeReportSink:
    def __init__(self) -> None:
        self.reports: dict[str, QcReportV1] = {}

    def put_immutable(self, report: QcReportV1) -> None:
        if not report.has_valid_digest():
            raise ValueError("report content hash does not match its canonical content")
        existing = self.reports.get(report.content_sha256)
        if existing is not None and existing != report:
            raise ValueError("immutable report hash collision")
        self.reports[report.content_sha256] = report


class FakeMetadataSink:
    def __init__(self) -> None:
        self.summaries: dict[str, QualitySummaryV1] = {}

    def put_summary(self, summary: QualitySummaryV1) -> None:
        self.summaries[summary.rollout_id] = summary


class FakeQualityProfileStore:
    def __init__(self) -> None:
        self.profiles: dict[tuple[str, str, int], QualityProfileV1] = {}

    def put_immutable(self, project_id: str, profile: QualityProfileV1) -> None:
        key = (project_id, profile.profile_id, profile.profile_version)
        existing = self.profiles.get(key)
        if existing is not None and existing != profile:
            raise ValueError("quality profile version is immutable")
        self.profiles[key] = profile

    def get(
        self, project_id: str, profile_id: str, profile_version: int
    ) -> QualityProfileV1 | None:
        return self.profiles.get((project_id, profile_id, profile_version))

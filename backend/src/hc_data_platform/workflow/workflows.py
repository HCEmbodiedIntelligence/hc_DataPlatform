from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from .models import JobStatus, QualityOutcome


class RawVerifier(Protocol):
    def verify(self, raw: dict[str, Any]) -> dict[str, Any]: ...


class QualityEvaluator(Protocol):
    def evaluate(self, verified: dict[str, Any]) -> tuple[QualityOutcome, dict[str, Any]]: ...


class Aligner(Protocol):
    def align(self, verified: dict[str, Any]) -> dict[str, Any]: ...


class DerivedCommitter(Protocol):
    def commit(self, fragment: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class IngestRolloutWorkflow:
    verifier: RawVerifier
    quality: QualityEvaluator
    aligner: Aligner
    committer: DerivedCommitter

    def run(self, raw: dict[str, Any]) -> dict[str, Any]:
        verified = self.verifier.verify(raw)
        outcome, report = self.quality.evaluate(verified)
        if outcome == QualityOutcome.REJECT:
            return {
                "_job_status": JobStatus.QUALITY_REJECTED,
                "quality_status": outcome,
                "qc_report": report,
                "raw_preserved": True,
                "training_eligible": False,
            }
        if outcome == QualityOutcome.RISK:
            return {
                "_job_status": JobStatus.QUALITY_RISK,
                "quality_status": outcome,
                "qc_report": report,
                "media_state": "NOT_PRODUCED",
                "raw_preserved": True,
                "training_eligible": False,
            }
        fragment = self.aligner.align(verified)
        derived = self.committer.commit(fragment)
        return {
            "quality_status": outcome,
            "qc_report": report,
            "derived": derived,
            "raw_preserved": True,
            "training_eligible": True,
        }


class FragmentCommitPort(Protocol):
    def commit(self, fragment: dict[str, Any]) -> dict[str, Any]: ...


class DatasetWriterWorkflow:
    """Serializes one dataset's physical commits and deduplicates fragment keys."""

    def __init__(self, committer: FragmentCommitPort) -> None:
        self._committer = committer
        self._results: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def submit(self, idempotency_key: str, fragment: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            existing = self._results.get(idempotency_key)
            if existing is not None:
                return existing
            result = self._committer.commit(fragment)
            self._results[idempotency_key] = result
            return result


class Publisher(Protocol):
    def publish(self, request: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class PublishDatasetWorkflow:
    publisher: Publisher

    def run(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.publisher.publish(request)


class Exporter(Protocol):
    def export(self, request: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class ExportWorkflow:
    exporter: Exporter

    def run(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.exporter.export(request)

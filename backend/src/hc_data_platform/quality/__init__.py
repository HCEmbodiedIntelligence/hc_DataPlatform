"""Compatibility exports. New callers use owned models/ports and adapter modules.

Resolving a contract does not initialize unrelated services, SDKs or workers.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import QualityEngine
    from .models import (
        ActionObservation,
        ActionQualityProfileV1,
        CompleteStepProfileV1,
        FindingSeverity,
        ImageObservation,
        ImageQualityProfileV1,
        JointObservation,
        ModalityOffsetProfileV1,
        PointCloudObservation,
        PointCloudQualityProfileV1,
        QcFinding,
        QcReportV1,
        QualityCode,
        QualityCompletedV1,
        QualityInputV1,
        QualityProfileV1,
        QualityStatus,
        QualitySummaryV1,
        TopicTimingMetricsV1,
        TopicTimingProfileV1,
    )
    from .ports import (
        FakeMetadataSink,
        FakeQualityProfileStore,
        FakeReportSink,
        MetadataSink,
        QualityEvaluationPort,
        QualityPersistenceError,
        QualityProfileStore,
        ReportSink,
        VisualAnomalyProbe,
    )

__all__ = [
    "ActionObservation",
    "ActionQualityProfileV1",
    "CompleteStepProfileV1",
    "FakeMetadataSink",
    "FakeQualityProfileStore",
    "FakeReportSink",
    "FindingSeverity",
    "ImageObservation",
    "ImageQualityProfileV1",
    "JointObservation",
    "MetadataSink",
    "ModalityOffsetProfileV1",
    "PointCloudObservation",
    "PointCloudQualityProfileV1",
    "QcFinding",
    "QcReportV1",
    "QualityCode",
    "QualityCompletedV1",
    "QualityEngine",
    "QualityEvaluationPort",
    "QualityInputV1",
    "QualityPersistenceError",
    "QualityProfileStore",
    "QualityProfileV1",
    "QualityStatus",
    "QualitySummaryV1",
    "ReportSink",
    "TopicTimingMetricsV1",
    "TopicTimingProfileV1",
    "VisualAnomalyProbe",
]

_EXPORTS = {
    "QualityEngine": (".engine", "QualityEngine"),
    "ActionObservation": (".models", "ActionObservation"),
    "ActionQualityProfileV1": (".models", "ActionQualityProfileV1"),
    "CompleteStepProfileV1": (".models", "CompleteStepProfileV1"),
    "FindingSeverity": (".models", "FindingSeverity"),
    "ImageObservation": (".models", "ImageObservation"),
    "ImageQualityProfileV1": (".models", "ImageQualityProfileV1"),
    "JointObservation": (".models", "JointObservation"),
    "ModalityOffsetProfileV1": (".models", "ModalityOffsetProfileV1"),
    "PointCloudObservation": (".models", "PointCloudObservation"),
    "PointCloudQualityProfileV1": (".models", "PointCloudQualityProfileV1"),
    "QcFinding": (".models", "QcFinding"),
    "QcReportV1": (".models", "QcReportV1"),
    "QualityCode": (".models", "QualityCode"),
    "QualityCompletedV1": (".models", "QualityCompletedV1"),
    "QualityInputV1": (".models", "QualityInputV1"),
    "QualityProfileV1": (".models", "QualityProfileV1"),
    "QualityStatus": (".models", "QualityStatus"),
    "QualitySummaryV1": (".models", "QualitySummaryV1"),
    "TopicTimingMetricsV1": (".models", "TopicTimingMetricsV1"),
    "TopicTimingProfileV1": (".models", "TopicTimingProfileV1"),
    "FakeMetadataSink": (".ports", "FakeMetadataSink"),
    "FakeQualityProfileStore": (".ports", "FakeQualityProfileStore"),
    "FakeReportSink": (".ports", "FakeReportSink"),
    "MetadataSink": (".ports", "MetadataSink"),
    "QualityEvaluationPort": (".ports", "QualityEvaluationPort"),
    "QualityPersistenceError": (".ports", "QualityPersistenceError"),
    "QualityProfileStore": (".ports", "QualityProfileStore"),
    "ReportSink": (".ports", "ReportSink"),
    "VisualAnomalyProbe": (".ports", "VisualAnomalyProbe"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module, attribute = target
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

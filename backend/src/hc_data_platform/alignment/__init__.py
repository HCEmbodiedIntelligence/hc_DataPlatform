"""Deterministic fixed-frequency multimodal alignment (BE-07)."""

from .arrow_writer import ArrowFragmentWriter
from .engine import AlignmentEngine
from .models import (
    AlignedFragmentManifestV1,
    AlignedFragmentReadyV1,
    AlignedRowV1,
    AlignedValueV1,
    AlignmentInputV1,
    AlignmentProfileV1,
    AlignmentStrategy,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from .ports import AlignmentPort, FakeFragmentWriter, FragmentWriterPort

__all__ = [
    "AlignedFragmentManifestV1",
    "AlignedFragmentReadyV1",
    "AlignedRowV1",
    "AlignedValueV1",
    "AlignmentEngine",
    "AlignmentInputV1",
    "AlignmentPort",
    "AlignmentProfileV1",
    "AlignmentStrategy",
    "ArrowFragmentWriter",
    "FakeFragmentWriter",
    "FragmentWriterPort",
    "ModalityKind",
    "ModalityStreamV1",
    "TimedSampleV1",
]

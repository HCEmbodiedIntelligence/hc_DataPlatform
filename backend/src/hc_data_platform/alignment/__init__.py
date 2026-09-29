"""Deterministic fixed-frequency multimodal alignment (BE-07)."""

from typing import TYPE_CHECKING

from .arrow_writer import ArrowFragmentWriter
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

if TYPE_CHECKING:
    from .engine import AlignmentEngine

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


def __getattr__(name: str) -> object:
    # Workflow payloads import models, not numerical execution. Loading the
    # engine here made every sandbox reload NumPy before imports_passed_through.
    if name == "AlignmentEngine":
        from .engine import AlignmentEngine

        return AlignmentEngine
    raise AttributeError(name)

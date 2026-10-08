"""Compatibility exports. New callers use owned models/ports and adapter modules.

Resolving a contract does not initialize unrelated services, SDKs or workers.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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

_EXPORTS = {
    "ArrowFragmentWriter": (".arrow_writer", "ArrowFragmentWriter"),
    "AlignedFragmentManifestV1": (".models", "AlignedFragmentManifestV1"),
    "AlignedFragmentReadyV1": (".models", "AlignedFragmentReadyV1"),
    "AlignedRowV1": (".models", "AlignedRowV1"),
    "AlignedValueV1": (".models", "AlignedValueV1"),
    "AlignmentInputV1": (".models", "AlignmentInputV1"),
    "AlignmentProfileV1": (".models", "AlignmentProfileV1"),
    "AlignmentStrategy": (".models", "AlignmentStrategy"),
    "ModalityKind": (".models", "ModalityKind"),
    "ModalityStreamV1": (".models", "ModalityStreamV1"),
    "TimedSampleV1": (".models", "TimedSampleV1"),
    "AlignmentPort": (".ports", "AlignmentPort"),
    "FakeFragmentWriter": (".ports", "FakeFragmentWriter"),
    "FragmentWriterPort": (".ports", "FragmentWriterPort"),
    "AlignmentEngine": (".engine", "AlignmentEngine"),
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

"""Compatibility exports. New callers use owned models/ports and adapter modules.

Resolving a contract does not initialize unrelated services, SDKs or workers.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import (
        CollectionTarget,
        CollectionTask,
        CollectionTaskProgress,
        CollectionTaskStatus,
        CreateCollectionTask,
        UpdateCollectionTask,
    )
    from .service import CollectionTaskService

__all__ = [
    "CollectionTarget",
    "CollectionTask",
    "CollectionTaskProgress",
    "CollectionTaskService",
    "CollectionTaskStatus",
    "CreateCollectionTask",
    "UpdateCollectionTask",
]

_EXPORTS = {
    "CollectionTarget": (".models", "CollectionTarget"),
    "CollectionTask": (".models", "CollectionTask"),
    "CollectionTaskProgress": (".models", "CollectionTaskProgress"),
    "CollectionTaskStatus": (".models", "CollectionTaskStatus"),
    "CreateCollectionTask": (".models", "CreateCollectionTask"),
    "UpdateCollectionTask": (".models", "UpdateCollectionTask"),
    "CollectionTaskService": (".service", "CollectionTaskService"),
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

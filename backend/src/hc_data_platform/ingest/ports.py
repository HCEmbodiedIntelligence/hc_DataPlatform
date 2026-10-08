"""Compatibility imports for historical ingest consumers.

New consumers import their specific object_storage capability. The in-memory
adapter is loaded only if explicitly requested; importing a contract is pure.
"""

from typing import TYPE_CHECKING, Any

from hc_data_platform.object_storage.checksums import crc64_ecma as crc64_ecma
from hc_data_platform.object_storage.checksums import normalize_etag as normalize_etag
from hc_data_platform.object_storage.models import MultipartPart as MultipartPart
from hc_data_platform.object_storage.models import ObjectMetadata as ObjectMetadata
from hc_data_platform.object_storage.ports import ObjectStoragePort as ObjectStoragePort

if TYPE_CHECKING:
    from hc_data_platform.object_storage.memory import (
        InMemoryObjectStorage as InMemoryObjectStorage,
    )
    from hc_data_platform.object_storage.validation import (
        validate_completion_parts as validate_completion_parts,
    )


def __getattr__(name: str) -> Any:
    if name == "InMemoryObjectStorage":
        from hc_data_platform.object_storage.memory import InMemoryObjectStorage

        return InMemoryObjectStorage
    if name == "validate_completion_parts":
        from hc_data_platform.object_storage.validation import validate_completion_parts

        return validate_completion_parts
    raise AttributeError(name)

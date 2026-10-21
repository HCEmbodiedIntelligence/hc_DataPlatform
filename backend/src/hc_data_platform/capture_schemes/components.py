"""Explicit dependencies for the scheme HTTP application, composed by its runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .models import DeviceMapping, Sample


class SampleValidator(Protocol):
    def validate(
        self,
        organization_id: str,
        document: dict[str, Any],
        sample: Sample,
        mapping: DeviceMapping | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CaptureServices:
    connections: Callable[[], Any]
    samples: SampleValidator
    organization_exists: Callable[[str], bool]

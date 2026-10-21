"""Owned read port for current processing failures, independent of delivery receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .processing_contract import ProcessingDocument


def safe_processing_detail(value: object) -> str | None:
    """Only bounded business explanations cross the persisted diagnostic boundary."""
    if not isinstance(value, str) or not value.strip():
        return None
    # Third-party exceptions and signed object locators are never public diagnostics.
    if re.search(
        r"[a-z][a-z0-9+.-]*://|\b(?:authorization|bearer|password|passwd|secret|token|"
        r"signature|credential|api[_-]?key|x-amz-[\w-]+)\b",
        value,
        re.IGNORECASE,
    ):
        return None
    return value.strip()[:2048]


@dataclass(frozen=True)
class ProcessingError:
    code: str
    detail: str | None


def current_failure(document: ProcessingDocument) -> ProcessingError | None:
    for episode in document.episodes:
        if episode.status == "FAILED" and episode.error_code:
            return ProcessingError(episode.error_code, safe_processing_detail(episode.error_detail))
    # Historical aggregate errors must not resurrect a discarded/recovered Episode.
    if not document.episodes and document.error_code:
        return ProcessingError(document.error_code, safe_processing_detail(document.error_detail))
    return None


class ProcessingErrors(Protocol):
    def for_source(
        self, *, scope: tuple[str, str, str], raw_source_id: str
    ) -> ProcessingError | None: ...

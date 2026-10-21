"""The recording client consumes these operations, never scheme persistence."""

from dataclasses import dataclass
from typing import Any, Protocol

from .references import PrepareRecording, Scope


@dataclass(frozen=True)
class TaskCapturePreview:
    plan: dict[str, Any]
    # Compatibility projection for clients that display processing_config.
    processing_config: dict[str, Any]


class RecordingPlans(Protocol):
    def preview(
        self, scope: Scope, task_id: str, robot_id: str, source_format: str
    ) -> TaskCapturePreview:
        """Read the chosen task plan without locking or creating a recording."""
        ...

    def prepare(
        self,
        scope: Scope,
        task_id: str,
        robot_id: str,
        command: PrepareRecording,
        actor: str,
    ) -> dict[str, Any]:
        """Atomically fix the plan for this recording; identical retries replay it."""
        ...


class WebUploadPlans(Protocol):
    def prepare(
        self,
        scope: Scope,
        upload_id: str,
        *,
        dataset_id: str,
        task_id: str | None,
        robot_id: str | None,
        source_format: str,
        actor: str,
    ) -> dict[str, Any]:
        """Freeze a server-owned receipt before accepting a browser upload session."""
        ...


def unresolved_receipt(code: str, detail: str, **details: Any) -> dict[str, Any]:
    return {
        "error": code,
        "detail": detail,
        "details": details,
        "source": "TASK_PLAN_UNRESOLVED",
        "retryable": False,
    }

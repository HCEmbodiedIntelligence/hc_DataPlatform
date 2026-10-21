"""Public PostgreSQL recording adapter, explicitly composed by the runtime."""

from collections.abc import Callable
from typing import Any

from . import plans
from .ports import TaskCapturePreview
from .references import PrepareRecording, Scope


class PostgresRecordingPlans:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connections = connection_factory

    def preview(
        self, scope: Scope, task_id: str, robot_id: str, source_format: str
    ) -> TaskCapturePreview:
        with self._connections() as db:
            selected = plans.for_robot(db, scope, task_id, robot_id, source_format)
        plan, mapping = selected["plan"], selected["mapping"]
        return TaskCapturePreview(
            plan={
                "revision": plan["revision"],
                "plan_sha256": plan["plan_sha256"],
                "scheme": plan["scheme"],
                "mapping_version": mapping["mapping_version"],
                "mapping_sha256": mapping["content_sha256"],
                "output": plan["output"],
                "source_format": source_format,
            },
            processing_config={
                **plan["scheme"],
                "source": "TASK_PLAN",
                "configuration": selected["adapter"].get("configuration", selected["adapter"]),
            },
        )

    def prepare(
        self,
        scope: Scope,
        task_id: str,
        robot_id: str,
        command: PrepareRecording,
        actor: str,
    ) -> dict[str, Any]:
        with self._connections() as db:
            return plans.prepare_recording(db, scope, task_id, robot_id, command, actor)

"""Workflow-owned lookup for recovery after a publication receipt was lost."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from hc_data_platform.core.context import current_request_context


@dataclass(frozen=True)
class TerminalWorkflowResult:
    status: str
    result: dict[str, Any]


class TerminalWorkflowResults(Protocol):
    def latest_terminal(
        self,
        *,
        organization_id: str,
        project_id: str,
        resource_id: str,
        job_type: str,
    ) -> TerminalWorkflowResult | None: ...


class PostgresTerminalWorkflowResults:
    def __init__(self, connections: Callable[[], Any]) -> None:
        self.connections = connections

    def latest_terminal(
        self,
        *,
        organization_id: str,
        project_id: str,
        resource_id: str,
        job_type: str,
    ) -> TerminalWorkflowResult | None:
        context = current_request_context()
        if (context.organization_id, context.project_id) != (organization_id, project_id):
            raise ValueError("workflow result scope does not match caller context")
        with self.connections() as connection:
            row = connection.execute(
                """SELECT status,result FROM workflow.jobs
                WHERE organization_id=%s AND project_id=%s AND resource_id=%s
                  AND job_type=%s AND status IN ('SUCCEEDED','QUALITY_RISK','QUALITY_REJECTED')
                ORDER BY updated_at DESC LIMIT 1""",
                (organization_id, project_id, resource_id, job_type),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        return TerminalWorkflowResult(row[0], result or {})

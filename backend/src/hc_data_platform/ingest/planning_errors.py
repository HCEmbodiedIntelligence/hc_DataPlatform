"""Stable planning failure shared without importing an execution adapter."""


class IngestWorkflowPlanBlocked(RuntimeError):
    # Preserve the historical exception name/code for durable failure compatibility.
    code = "INGEST_WORKFLOW_PLAN_BLOCKED"


IngestPlanBlocked = IngestWorkflowPlanBlocked

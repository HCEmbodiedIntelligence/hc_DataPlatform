"""Temporal activity adapter for tenant scope, correlation and maintenance fences."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from temporalio import activity

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.observability import locator_workflow_id
from hc_data_platform.core.structured_logging import (
    LogCorrelation,
    bind_log_correlation,
    reset_log_correlation,
)
from hc_data_platform.platform_ops.maintenance import (
    EnvironmentId,
    MaintenanceWriteGate,
    SafeActorId,
    writer_permit_scope,
)


@dataclass(frozen=True)
class ActivityExecutionScope:
    gate: MaintenanceWriteGate | None
    environment_id: EnvironmentId = "local"
    instance_id: SafeActorId = "unconfigured-worker"

    @contextmanager
    def __call__(
        self,
        project_id: str | None,
        region_code: str | None,
        resource_id: str,
        *,
        organization_id: str | None = None,
    ) -> Iterator[None]:
        resource_digest = hashlib.sha256(resource_id.encode()).hexdigest()[:24]
        writer_id = f"activity:{self.instance_id}:{resource_digest}"
        try:
            workflow_id = activity.info().workflow_id
        except RuntimeError:
            workflow_id = None
        request_id = (
            locator_workflow_id("activity", project_id, resource_id)
            if project_id is not None
            else None
        )
        correlation_token = bind_log_correlation(
            LogCorrelation(
                request_id=request_id,
                operation_id=workflow_id,
                workflow_id=workflow_id,
            )
        )
        try:
            with writer_permit_scope(
                self.gate,
                environment_id=self.environment_id,
                writer_id=writer_id,
                writer_kind="temporal_activity",
            ):
                if project_id is None:
                    yield
                    return
                token = bind_request_context(
                    RequestContext(
                        organization_id=organization_id,
                        project_id=project_id,
                        region_code=region_code,
                        subject_id="hc-data-worker",
                        request_id=request_id or "activity",
                        service_identity=True,
                    )
                )
                try:
                    yield
                finally:
                    reset_request_context(token)
        finally:
            reset_log_correlation(correlation_token)

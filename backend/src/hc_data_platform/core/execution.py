"""Caller-owned tenant and write-fence execution boundary, without a task SDK."""

from contextlib import AbstractContextManager
from typing import Protocol


class ExecutionScope(Protocol):
    def __call__(
        self,
        project_id: str | None,
        region_code: str | None,
        resource_id: str,
        *,
        organization_id: str | None = None,
    ) -> AbstractContextManager[None]: ...

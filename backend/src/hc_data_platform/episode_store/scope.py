"""Verified Episode and collection scope checks, independent of repositories."""

from __future__ import annotations

from typing import TYPE_CHECKING

from hc_data_platform.core.context import current_request_context

if TYPE_CHECKING:
    from .models import EpisodeIdentity


def assert_scope(identity: EpisodeIdentity) -> None:
    context = current_request_context()
    if identity.scope != (context.organization_id, context.project_id, context.region_code):
        raise PermissionError("Episode data reference is outside the verified request scope")


def collection_scope(dataset_id: str, project_id: str | None = None) -> tuple[str, str, str, str]:
    context = current_request_context()
    if (
        not context.organization_id
        or not context.project_id
        or not context.region_code
        or (project_id is not None and project_id != context.project_id)
    ):
        raise PermissionError("collection access requires an exact organization/project/region")
    return context.organization_id, context.project_id, context.region_code, dataset_id

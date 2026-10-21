"""Annotation-owned, scoped read projection for already published data."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from hc_data_platform.core.context import current_request_context
from hc_data_platform.episode_store.models import DataVersionRef


@dataclass(frozen=True)
class AnnotationDelivery:
    rollout_id: str
    dataset_version: int
    lance_version: int | None
    data_ref: DataVersionRef | None
    status: str


class AnnotationDeliveries(Protocol):
    def for_episodes(
        self,
        *,
        scope: tuple[str, str, str],
        dataset_id: str,
        episode_ids: tuple[str, ...],
    ) -> tuple[AnnotationDelivery, ...]: ...


class PostgresAnnotationDeliveries:
    def __init__(self, connections: Callable[[], Any]) -> None:
        self.connections = connections

    def for_episodes(
        self,
        *,
        scope: tuple[str, str, str],
        dataset_id: str,
        episode_ids: tuple[str, ...],
    ) -> tuple[AnnotationDelivery, ...]:
        context = current_request_context()
        if (context.organization_id, context.project_id, context.region_code) != scope:
            raise ValueError("annotation delivery scope differs from request context")
        if not episode_ids:
            return ()
        with self.connections() as connection:
            rows = connection.execute(
                """SELECT rollout_id, dataset_version, base_lance_version, data_ref, status
                FROM annotation.annotation_tasks
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND dataset_id=%s AND rollout_id=ANY(%s) AND task_kind='TAGGING'""",
                (*scope, dataset_id, list(episode_ids)),
            ).fetchall()
        return tuple(
            AnnotationDelivery(
                rollout_id=row[0],
                dataset_version=row[1],
                lance_version=row[2],
                data_ref=DataVersionRef.model_validate(row[3]) if row[3] is not None else None,
                status=row[4],
            )
            for row in rows
        )

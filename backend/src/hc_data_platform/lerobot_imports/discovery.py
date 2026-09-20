"""Native Episode manifests for the shared annotation/video/robot workbench."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hc_data_platform.core.context import current_request_context
from hc_data_platform.ingest.manifest import ObjectStorageManifestParser
from hc_data_platform.ingest.models import ManifestDiscoveryV1
from hc_data_platform.ingest.ports import ObjectStoragePort
from hc_data_platform.ingest.service import AlternateManifestDiscoveryPort


class NativeManifestDiscovery:
    def __init__(
        self,
        connections: Callable[[], Any],
        storage: ObjectStoragePort,
        fallback: AlternateManifestDiscoveryPort,
    ) -> None:
        self.connections, self.parser, self.fallback = (
            connections,
            ObjectStorageManifestParser(storage),
            fallback,
        )

    def find(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> ManifestDiscoveryV1 | None:
        organization_id = current_request_context().organization_id
        with self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT s.raw_source_id, s.dataset_id, e.source_episode_index
                FROM ingest.raw_sources s JOIN ingest.raw_source_episodes e USING
                  (organization_id, project_id, region_code, raw_source_id)
                WHERE s.organization_id=%s AND s.project_id=%s AND s.region_code=%s
                  AND e.episode_id=%s AND s.source_format='LEROBOT_V3' LIMIT 2""",
                (organization_id, project_id, region_code, rollout_id),
            )
            rows = cursor.fetchall()
        if not rows:
            return self.fallback.find(
                project_id=project_id, region_code=region_code, rollout_id=rollout_id
            )
        if len(rows) != 1:
            raise ValueError("native Episode manifest identity is ambiguous")
        raw_id, dataset_id, index = rows[0]
        key = (
            f"derived/lerobot-imports/{organization_id}/{dataset_id}/{raw_id}/"
            f"episodes/{index:06d}-manifest.json"
        )
        preflight = self.parser.parse(key)
        if (
            preflight.manifest.project_id != project_id
            or preflight.manifest.rollout_id != rollout_id
        ):
            raise ValueError("native Episode manifest lineage changed")
        return preflight.discovery.model_copy(update={"robot_id": preflight.identifiers.robot_id})

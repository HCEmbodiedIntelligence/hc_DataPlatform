"""Native import state transactions, including obsolete-attempt rejection.

The job lock, episode receipt, resolution and aggregate remain in one transaction.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from hc_data_platform.core.context import current_request_context

from .orchestration import LeRobotEpisodeTaskV1
from .pipeline_ports import NativeStateChange


class PostgresNativeProcessingState:
    def __init__(self, connections: Callable[[], Any]) -> None:
        self.connections = connections

    @staticmethod
    def _assert_scope(task: LeRobotEpisodeTaskV1) -> None:
        context = current_request_context()
        if (context.organization_id, context.project_id, context.region_code) != (
            task.organization_id,
            task.project_id,
            task.region_code,
        ):
            raise ValueError("native processing state scope does not match caller context")

    def episode_ready(self, task: LeRobotEpisodeTaskV1) -> bool:
        self._assert_scope(task)
        with self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT status FROM ingest.raw_source_episodes
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND raw_source_id=%s AND source_episode_index=%s""",
                (
                    task.organization_id,
                    task.project_id,
                    task.region_code,
                    task.source.raw_upload_id,
                    task.source.episode_index,
                ),
            )
            row = cursor.fetchone()
            return row is not None and row[0] in {"READY", "DISCARDED"}

    def update_state(self, update: NativeStateChange) -> None:
        task = update.task
        self._assert_scope(task)
        import re

        error = (
            None
            if update.error_code is None
            else re.sub(r"[^A-Z0-9_]", "_", update.error_code.upper())[:128]
        )
        scope = (task.organization_id, task.project_id, task.region_code, task.source.raw_upload_id)
        with self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT job_id FROM ingest.raw_ingest_jobs
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                  AND raw_source_id=%s FOR UPDATE""",
                scope,
            )
            resolution_id = task.source.processing_attempt_id
            if resolution_id:
                # Ignore obsolete activity deliveries after a newer user decision.
                cursor.execute(
                    """SELECT resolution_id FROM ingest.episode_resolutions
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND raw_source_id=%s AND episode_id=(
                        SELECT episode_id FROM ingest.raw_source_episodes
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                          AND raw_source_id=%s AND source_episode_index=%s)
                    ORDER BY created_at DESC LIMIT 1""",
                    (*scope, *scope, task.source.episode_index),
                )
                latest = cursor.fetchone()
                if latest is None or latest[0] != resolution_id:
                    return
            if update.status.startswith("EPISODE_"):
                ready = update.ready
                derived = update.derived
                cursor.execute(
                    """UPDATE ingest.raw_source_episodes
                    SET status=%s, frame_count=%s, dataset_version=%s, lance_version=%s,
                    data_ref=%s::jsonb,
                    updated_at=clock_timestamp()
                    WHERE organization_id=%s AND project_id=%s AND region_code=%s
                      AND raw_source_id=%s AND source_episode_index=%s""",
                    (
                        "READY" if ready else "FAILED",
                        derived.get("step_count"),
                        derived.get("dataset_version"),
                        derived.get("lance_version"),
                        json.dumps(derived["data"]) if derived.get("data") is not None else None,
                        *scope,
                        task.source.episode_index,
                    ),
                )
                return
            cursor.execute(
                """UPDATE ingest.raw_ingest_jobs
                SET status=%s, last_error_code=%s, updated_at=clock_timestamp()
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND raw_source_id=%s""",
                (update.status, error, *scope),
            )
            source_status = {
                "RUNNING": "PROCESSING",
                "SUCCEEDED": "READY",
                "PARTIALLY_FAILED": "PARTIALLY_FAILED",
                "FAILED": "FAILED",
                "CANCELLED": "FAILED",
            }[update.status]
            cursor.execute(
                """UPDATE ingest.raw_sources SET processing_status=%s, updated_at=clock_timestamp()
                WHERE organization_id=%s AND project_id=%s AND region_code=%s
                AND raw_source_id=%s""",
                (source_status, *scope),
            )
            if resolution_id:
                if update.status in {"FAILED", "PARTIALLY_FAILED", "CANCELLED"}:
                    cursor.execute(
                        """UPDATE ingest.raw_source_episodes SET status='FAILED',
                        updated_at=clock_timestamp()
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                          AND raw_source_id=%s AND source_episode_index=%s
                          AND status IN ('PENDING', 'PROCESSING')""",
                        (*scope, task.source.episode_index),
                    )
                cursor.execute(
                    """UPDATE ingest.episode_resolutions SET status=%s, error_code=%s,
                    updated_at=clock_timestamp() WHERE resolution_id=%s
                    AND organization_id=%s AND project_id=%s AND region_code=%s""",
                    (
                        "RUNNING"
                        if update.status == "RUNNING"
                        else "SUCCEEDED"
                        if update.status == "SUCCEEDED"
                        else "FAILED",
                        error,
                        resolution_id,
                        *scope[:3],
                    ),
                )
            if update.status not in {"RUNNING", "CANCELLED"}:
                from .resolutions import refresh_import_state

                refresh_import_state(cursor, scope)

"""Test-profile-only persistence hooks for resources without public create APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hc_data_platform.annotation.postgres import PostgresAnnotationRepository
from hc_data_platform.annotation.service import AnnotationService
from hc_data_platform.core.dbapi import normalize_postgres_dsn

from .cleanup import require_test_cleanup_profile
from .fixture import RunScope


@dataclass(slots=True)
class PostgresAnnotationTaskProvisioner:
    """Create only the isolated annotation task missing from the public API.

    The guard is intentionally the same as destructive cleanup: a caller must opt
    into a named disposable test database.  This hook is never mounted as an HTTP
    route and therefore cannot become a production seed endpoint.
    """

    postgres_dsn: str

    def create(
        self,
        *,
        scope: RunScope,
        task_id: str,
        dataset_id: str,
        dataset_version: int,
        rollout_id: str,
        base_step_count: int,
        tag_schema_id: str,
        tag_schema_version: int,
    ) -> str:
        require_test_cleanup_profile(scope, self.postgres_dsn)

        def connect() -> Any:
            import psycopg

            connection = psycopg.connect(normalize_postgres_dsn(self.postgres_dsn))
            connection.execute(
                """
                SELECT
                    set_config('app.project_id', %s, false),
                    set_config('app.region_code', %s, false),
                    set_config('app.subject_id', %s, false),
                    set_config('app.request_id', %s, false),
                    set_config('app.service_identity', 'true', false)
                """,
                (
                    scope.project_id,
                    scope.region_code,
                    "be22-test-provisioner",
                    f"be22-{scope.run_id}-annotation-provision",
                ),
            )
            return connection

        service = AnnotationService(PostgresAnnotationRepository(connect))
        task = service.create_task(
            task_id=task_id,
            project_id=scope.project_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            rollout_id=rollout_id,
            base_lance_version=dataset_version,
            base_step_count=base_step_count,
            tag_schema_id=tag_schema_id,
            tag_schema_version=tag_schema_version,
        )
        return task.task_id

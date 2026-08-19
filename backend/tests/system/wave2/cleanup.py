"""Guarded, idempotent PostgreSQL/MinIO cleanup for isolated BE22 runs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from hc_data_platform.core.dbapi import normalize_postgres_dsn

from .fixture import RunScope


def require_test_cleanup_profile(scope: RunScope, dsn: str) -> None:
    scope.assert_cleanup_safe()
    if os.environ.get("HC_ENVIRONMENT") != "test":
        raise RuntimeError("BE22 cleanup is available only when HC_ENVIRONMENT=test")
    if os.environ.get("HC_WAVE2_CLEANUP_ENABLED") != "1":
        raise RuntimeError("set HC_WAVE2_CLEANUP_ENABLED=1 for the isolated test database")
    database = urlparse(normalize_postgres_dsn(dsn)).path.lstrip("/")
    if os.environ.get("HC_WAVE2_TEST_DATABASE_ACK") != database:
        raise RuntimeError("HC_WAVE2_TEST_DATABASE_ACK must exactly name the test database")


@dataclass(slots=True)
class PostgresS3CleanupBackend:
    postgres_dsn: str
    s3_client: Any
    bucket: str

    def delete_database_scope(self, scope: RunScope) -> dict[str, int]:
        require_test_cleanup_profile(scope, self.postgres_dsn)
        import psycopg

        projects = (scope.project_id, scope.foreign_project_id)
        counts: dict[str, int] = {}
        connection = psycopg.connect(normalize_postgres_dsn(self.postgres_dsn))
        try:
            with connection.transaction():
                # Test DB owner only. SET LOCAL guarantees immutable-row triggers are restored
                # even if cleanup is interrupted or rolled back.
                connection.execute("SET LOCAL session_replication_role = 'replica'")
                principals = tuple(
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT principal_id FROM access_control.accounts
                        WHERE canonical_username IN (%s, %s)
                        """,
                        (scope.admin_username, scope.contractor_username),
                    ).fetchall()
                )

                statements: tuple[tuple[str, str, tuple[Any, ...]], ...] = (
                    (
                        "annotation.annotation_task_triggers",
                        "DELETE FROM annotation.annotation_task_triggers "
                        "WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "annotation.annotation_submission_mutations",
                        "DELETE FROM annotation.annotation_submission_mutations WHERE task_id IN "
                        "(SELECT task_id FROM annotation.annotation_tasks "
                        "WHERE project_id = ANY(%s))",
                        (list(projects),),
                    ),
                    (
                        "annotation.annotation_reviews",
                        "DELETE FROM annotation.annotation_reviews WHERE task_id IN "
                        "(SELECT task_id FROM annotation.annotation_tasks "
                        "WHERE project_id = ANY(%s))",
                        (list(projects),),
                    ),
                    (
                        "annotation.annotation_submissions",
                        "DELETE FROM annotation.annotation_submissions WHERE task_id IN "
                        "(SELECT task_id FROM annotation.annotation_tasks "
                        "WHERE project_id = ANY(%s))",
                        (list(projects),),
                    ),
                    (
                        "annotation.annotation_mutations",
                        "DELETE FROM annotation.annotation_mutations WHERE task_id IN "
                        "(SELECT task_id FROM annotation.annotation_tasks "
                        "WHERE project_id = ANY(%s))",
                        (list(projects),),
                    ),
                    (
                        "annotation.annotation_operations",
                        "DELETE FROM annotation.annotation_operations WHERE task_id IN "
                        "(SELECT task_id FROM annotation.annotation_tasks "
                        "WHERE project_id = ANY(%s))",
                        (list(projects),),
                    ),
                    (
                        "annotation.legacy_cleaning_migrations",
                        "DELETE FROM annotation.legacy_cleaning_migrations WHERE target_task_id IN "
                        "(SELECT task_id FROM annotation.annotation_tasks "
                        "WHERE project_id = ANY(%s))",
                        (list(projects),),
                    ),
                    (
                        "annotation.annotation_revisions",
                        "DELETE FROM annotation.annotation_revisions WHERE task_id IN "
                        "(SELECT task_id FROM annotation.annotation_tasks "
                        "WHERE project_id = ANY(%s))",
                        (list(projects),),
                    ),
                    (
                        "annotation.annotation_tasks",
                        "DELETE FROM annotation.annotation_tasks WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "annotation.tag_schema_bindings",
                        "DELETE FROM annotation.tag_schema_bindings WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "annotation.tag_schema_versions",
                        "DELETE FROM annotation.tag_schema_versions WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "lance_rollout_lineage",
                        "DELETE FROM lance_rollout_lineage WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "lance_pending_reconciliation",
                        "DELETE FROM lance_pending_reconciliation WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "lance_dataset_versions",
                        "DELETE FROM lance_dataset_versions WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "lance_datasets",
                        "DELETE FROM lance_datasets WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "lance_schema_snapshots",
                        "DELETE FROM lance_schema_snapshots WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "quality_rollout_summaries",
                        "DELETE FROM quality_rollout_summaries WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "qc_reports",
                        "DELETE FROM qc_reports WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "quality_profiles",
                        "DELETE FROM quality_profiles WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "raw_verification_reports",
                        "DELETE FROM raw_verification_reports WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "aligned_fragment_attempts",
                        "DELETE FROM aligned_fragment_attempts WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "alignment_profiles",
                        "DELETE FROM alignment_profiles WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "workflow.reconciliation_items",
                        "DELETE FROM workflow.reconciliation_items WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "workflow.jobs",
                        "DELETE FROM workflow.jobs WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "ingest.workflow_triggers",
                        "DELETE FROM ingest.workflow_triggers WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "ingest.manifest_discoveries",
                        "DELETE FROM ingest.manifest_discoveries WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "ingest.upload_parts",
                        "DELETE FROM ingest.upload_parts WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "ingest.rollout_objects",
                        "DELETE FROM ingest.rollout_objects WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "ingest.upload_objects",
                        "DELETE FROM ingest.upload_objects WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "ingest.upload_sessions",
                        "DELETE FROM ingest.upload_sessions WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "ingest.rollouts",
                        "DELETE FROM ingest.rollouts WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "ingest.collection_jobs",
                        "DELETE FROM ingest.collection_jobs WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "collection_tasks.collection_tasks",
                        "DELETE FROM collection_tasks.collection_tasks WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "core.outbox_events",
                        "DELETE FROM core.outbox_events WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "core.audit_events",
                        "DELETE FROM core.audit_events WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "core.idempotency_records",
                        "DELETE FROM core.idempotency_records WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "access_control.capability_grants",
                        "DELETE FROM access_control.capability_grants WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "access_control.memberships",
                        "DELETE FROM access_control.memberships WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "access_control.capability_requests",
                        "DELETE FROM access_control.capability_requests WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "access_control.membership_requests",
                        "DELETE FROM access_control.membership_requests WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                    (
                        "access_control.audit_events",
                        "DELETE FROM access_control.audit_events WHERE project_id = ANY(%s)",
                        (list(projects),),
                    ),
                )
                for name, sql, parameters in statements:
                    result = connection.execute(sql, parameters)
                    counts[name] = max(result.rowcount, 0)

                if principals:
                    principal_list = list(principals)
                    for name, sql in (
                        (
                            "access_control.command_idempotency",
                            "DELETE FROM access_control.command_idempotency "
                            "WHERE actor_id = ANY(%s::text[])",
                        ),
                        (
                            "access_control.sessions",
                            "DELETE FROM access_control.sessions "
                            "WHERE principal_id = ANY(%s::uuid[])",
                        ),
                        (
                            "access_control.accounts",
                            "DELETE FROM access_control.accounts "
                            "WHERE principal_id = ANY(%s::uuid[])",
                        ),
                    ):
                        result = connection.execute(
                            sql,
                            ([str(principal) for principal in principal_list],),
                        )
                        counts[name] = max(result.rowcount, 0)
        finally:
            connection.close()
        return counts

    def delete_object_prefix(self, prefix: str) -> int:
        allowed = (
            prefix.startswith("raw/v1/project=be22-"),
            prefix.startswith("lance/be22-"),
            prefix.startswith("lance/_attempts/be22-"),
        )
        if not any(allowed) or not prefix.endswith("/") or ".." in prefix:
            raise ValueError("object cleanup is restricted to an exact BE22 run prefix")
        deleted = 0
        paginator = self.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            # MinIO requires Content-MD5 for S3 multi-delete, while recent botocore
            # negotiates a different request checksum. Per-key deletion keeps this
            # test-only, bounded namespace cleanup portable and idempotent.
            for item in page.get("Contents", ()):
                self.s3_client.delete_object(Bucket=self.bucket, Key=item["Key"])
                deleted += 1
        return deleted

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

from hc_data_platform.core.context import (  # noqa: E402
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import (  # noqa: E402
    normalize_postgres_dsn,
    psycopg_connection_factory,
)
from hc_data_platform.core.migrations import apply_migrations  # noqa: E402
from hc_data_platform.publishing.models import (  # noqa: E402
    PublishedDatasetManifestV1,
    PublishedRolloutV1,
    StepRangeV1,
)
from hc_data_platform.publishing.postgres import (  # noqa: E402
    PostgresPublishedManifestRepository,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 21, 4, tzinfo=timezone.utc)
ORGANIZATION_ID = "publication-notification-organization"
REGION_CODE = "publication-notification-region"


def _source_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


@pytest.fixture(scope="module")
def isolated_database() -> Iterator[str]:
    base_dsn = _source_dsn()
    database_name = f"hc_publish_notify_{uuid4().hex[:12]}"
    with psycopg.connect(base_dsn, autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("HC_TEST_POSTGRES_DSN role cannot create an isolated database")
    parsed = urlsplit(base_dsn)
    isolated_dsn = urlunsplit(parsed._replace(path=f"/{database_name}"))
    try:
        asyncio.run(apply_migrations(isolated_dsn))
        yield isolated_dsn
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


@contextmanager
def _request_scope(project_id: str, *, subject_id: str) -> Iterator[None]:
    token = bind_request_context(
        RequestContext(
            organization_id=ORGANIZATION_ID,
            project_id=project_id,
            region_code=REGION_CODE,
            subject_id=subject_id,
            request_id=str(uuid4()),
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def _seed_product_dataset(
    dsn: str,
    *,
    project_id: str,
    dataset_id: str,
    owner_id: str,
    create_account: bool,
) -> None:
    document = {
        "dataset_id": dataset_id,
        "scope": {
            "organization_id": ORGANIZATION_ID,
            "project_id": project_id,
            "region_code": REGION_CODE,
        },
    }
    base_version_document = {
        "dataset_id": dataset_id,
        "version_id": "version_lance_1",
        "display_version": "v1",
        "kind": "RAW",
        "status": "READY",
        "published_at": NOW.isoformat(),
        "scope": document["scope"],
        "manifest": {
            "manifest_id": f"manifest-{dataset_id}",
            "sha256": hashlib.sha256(f"manifest:{dataset_id}".encode()).hexdigest(),
            "entry_count": "0",
        },
    }
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            INSERT INTO registry.organization_projects (organization_id, project_id, display_name)
            VALUES (%s, %s, 'Publication test')
            """,
            (ORGANIZATION_ID, project_id),
        )
        if create_account:
            connection.execute(
                """
                INSERT INTO access_control.accounts (
                    principal_id, canonical_username, display_username, display_name,
                    password_hash, password_changed_at
                ) VALUES (%s::uuid, %s, %s, %s, %s, %s)
                """,
                (
                    owner_id,
                    f"publication-owner-{owner_id}",
                    "Publication owner",
                    "Publication owner",
                    "scrypt$test-not-used-by-publication-notification",
                    NOW,
                ),
            )
        connection.execute(
            """
            INSERT INTO dataset_registry.datasets (
                organization_id, project_id, region_code, dataset_id, name, description,
                labels, availability, owner_id, owner_display_name, asset_state,
                storage_class, channels, version, dataset_document,
                created_at, updated_at, activity_at
            ) VALUES (
                %s, %s, %s, %s, %s, '', '[]'::jsonb, 'ACTIVE', %s, %s,
                'READY', 'STANDARD', '[]'::jsonb, 1, %s::jsonb, %s, %s, %s
            )
            """,
            (
                ORGANIZATION_ID,
                project_id,
                REGION_CODE,
                dataset_id,
                f"Dataset {dataset_id}",
                owner_id,
                "Publication owner",
                json.dumps(document, sort_keys=True),
                NOW,
                NOW,
                NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO dataset_registry.dataset_versions (
                organization_id, project_id, region_code, dataset_id, version_id,
                display_version, version_kind, version_status, created_at, published_at,
                version_document, version_scope
            ) VALUES (
                %s, %s, %s, %s, 'version_lance_1', 'v1', 'RAW', 'READY', %s, %s,
                %s::jsonb, 'INTERNAL'
            )
            """,
            (
                ORGANIZATION_ID,
                project_id,
                REGION_CODE,
                dataset_id,
                NOW,
                NOW,
                json.dumps(base_version_document, sort_keys=True),
            ),
        )


def _seed_rollout(dsn: str, *, project_id: str, rollout_id: str) -> None:
    job_id = f"job-{rollout_id}"
    digest = hashlib.sha256(rollout_id.encode()).hexdigest()
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            SELECT set_config('app.organization_id', %s, false),
                   set_config('app.project_id', %s, false),
                   set_config('app.region_code', %s, false),
                   set_config('app.subject_id', 'publication-notification-seed', false)
            """,
            (ORGANIZATION_ID, project_id, REGION_CODE),
        )
        connection.execute(
            """
            INSERT INTO ingest.collection_jobs (
                project_id, region_code, task_id, collection_job_id, robot_id,
                status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, 'COLLECTING', %s, %s)
            """,
            (
                project_id,
                REGION_CODE,
                f"task-{rollout_id}",
                job_id,
                f"robot-{rollout_id}",
                NOW,
                NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO ingest.rollouts (
                project_id, region_code, collection_job_id, rollout_id, sequence_no,
                robot_id, source_sha256, status, created_at, updated_at,
                collection_session_id, recording_request_id, data_package_id
            ) VALUES (%s, %s, %s, %s, 1, %s, %s, 'RAW_COMMITTED', %s, %s, %s, %s, %s)
            """,
            (
                project_id,
                REGION_CODE,
                job_id,
                rollout_id,
                f"robot-{rollout_id}",
                digest,
                NOW,
                NOW,
                f"session-{rollout_id}",
                f"recording-{rollout_id}",
                f"package-{rollout_id}",
            ),
        )


def _manifest(
    *, project_id: str, dataset_id: str, dataset_version: str, rollout_id: str
) -> PublishedDatasetManifestV1:
    content_hash = hashlib.sha256(
        f"{project_id}:{dataset_id}:{dataset_version}:{rollout_id}".encode()
    ).hexdigest()
    annotation_hash = hashlib.sha256(f"annotation:{content_hash}".encode()).hexdigest()
    training_hash = hashlib.sha256(f"training:{content_hash}".encode()).hexdigest()
    return PublishedDatasetManifestV1(
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        base_lance_version="v1",
        created_at=NOW,
        content_hash=content_hash,
        annotations_uri=f"published/{project_id}/{dataset_id}/annotations.lance",
        annotations_content_sha256=annotation_hash,
        training_manifest_uri=f"published/{project_id}/{dataset_id}/training.json",
        training_manifest_content_sha256=training_hash,
        rollouts=(
            PublishedRolloutV1(
                rollout_id=rollout_id,
                source_mcap_sha256=hashlib.sha256(rollout_id.encode()).hexdigest(),
                base_lance_version="v1",
                annotation_revision=1,
                annotation_task_id=f"task-{rollout_id}",
                quality_profile_version="quality:v1",
                alignment_profile_version="alignment:v1",
                alignment_frequency_hz=30,
                converter_version="converter:v1",
                total_steps=1,
                included_step_ranges=(StepRangeV1(start_step=0, end_step=1),),
            ),
        ),
    )


def test_publication_creates_one_durable_owner_notification_on_replay(
    isolated_database: str,
) -> None:
    project_id = "publication-notification-project"
    dataset_id = "dataset_notification"
    dataset_version = "version_1"
    rollout_id = "publication-notification-rollout"
    owner_id = str(uuid4())
    _seed_product_dataset(
        isolated_database,
        project_id=project_id,
        dataset_id=dataset_id,
        owner_id=owner_id,
        create_account=True,
    )
    _seed_rollout(isolated_database, project_id=project_id, rollout_id=rollout_id)
    candidate = _manifest(
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        rollout_id=rollout_id,
    )
    repository = PostgresPublishedManifestRepository(psycopg_connection_factory(isolated_database))

    with _request_scope(project_id, subject_id=owner_id):
        first = repository.create_immutable(candidate)
        replay = repository.create_immutable(candidate)
    assert replay == first

    with psycopg.connect(isolated_database) as connection:
        rows = connection.execute(
            """
            SELECT kind, organization_id, project_id, resource_type, resource_id,
                   event_key, state
            FROM access_control.account_notifications
            WHERE recipient_id = %s::uuid
            """,
            (owner_id,),
        ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert tuple(str(value) for value in row[:5]) == (
        "DATASET_VERSION_PUBLISHED",
        ORGANIZATION_ID,
        project_id,
        "DATASET_VERSION",
        f"{dataset_id}:{dataset_version}",
    )
    assert str(row[5]).startswith("dataset-publication:v1:")
    assert len(str(row[5])) == len("dataset-publication:v1:") + 64
    assert str(row[6]) == "UNREAD"
    assert dataset_id not in str(row[5])


def test_notification_failure_rolls_back_publication_and_lineage(
    isolated_database: str,
) -> None:
    project_id = "publication-notification-rollback-project"
    dataset_id = "dataset_notification_rollback"
    dataset_version = "version_1"
    rollout_id = "publication-notification-rollback-rollout"
    missing_owner_id = str(uuid4())
    _seed_product_dataset(
        isolated_database,
        project_id=project_id,
        dataset_id=dataset_id,
        owner_id=missing_owner_id,
        create_account=False,
    )
    _seed_rollout(isolated_database, project_id=project_id, rollout_id=rollout_id)
    repository = PostgresPublishedManifestRepository(psycopg_connection_factory(isolated_database))
    candidate = _manifest(
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        rollout_id=rollout_id,
    )

    with (
        _request_scope(project_id, subject_id=missing_owner_id),
        pytest.raises(psycopg.errors.ForeignKeyViolation),
    ):
        repository.create_immutable(candidate)

    with psycopg.connect(isolated_database) as connection:
        publication_count = connection.execute(
            """
            SELECT count(*) FROM publishing.dataset_versions
            WHERE project_id = %s AND dataset_id = %s AND dataset_version = %s
            """,
            (project_id, dataset_id, dataset_version),
        ).fetchone()[0]
        lineage_count = connection.execute(
            """
            SELECT count(*) FROM publishing.rollout_publication_lineage
            WHERE project_id = %s AND dataset_id = %s AND dataset_version = %s
            """,
            (project_id, dataset_id, dataset_version),
        ).fetchone()[0]
        notification_count = connection.execute(
            """
            SELECT count(*) FROM access_control.account_notifications
            WHERE recipient_id = %s::uuid
            """,
            (missing_owner_id,),
        ).fetchone()[0]
    assert (publication_count, lineage_count, notification_count) == (0, 0, 0)

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402
from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

from hc_data_platform.core.context import (  # noqa: E402
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import (  # noqa: E402
    normalize_postgres_dsn,
    psycopg_connection_factory,
)
from hc_data_platform.core.errors import ProblemException  # noqa: E402
from hc_data_platform.dashboard.postgres import PostgresDashboardRepository  # noqa: E402
from hc_data_platform.dashboard.repository import (  # noqa: E402
    DashboardScope,
    DashboardWindow,
)
from hc_data_platform.publishing.models import (  # noqa: E402
    PublishedDatasetManifestV1,
    PublishedRolloutV1,
    StepRangeV1,
)
from hc_data_platform.publishing.postgres import (  # noqa: E402
    PostgresPublishedManifestRepository,
)
from hc_data_platform.security.auth import AuthContext  # noqa: E402
from hc_data_platform.security.capabilities import CAPABILITY_DASHBOARD_READ  # noqa: E402

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 18, 8, tzinfo=timezone.utc)


def source_dsn() -> str:
    value = os.getenv("HC_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    return normalize_postgres_dsn(value)


@pytest.fixture(scope="module")
def isolated_database() -> dict[str, str]:
    base_dsn = source_dsn()
    parameters = conninfo_to_dict(base_dsn)
    database_name = f"hc_br01_{uuid4().hex[:16]}"
    with psycopg.connect(base_dsn, autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("HC_TEST_POSTGRES_DSN role cannot create an isolated database")
    isolated_dsn = make_conninfo(**{**parameters, "dbname": database_name})
    try:
        for relative in (
            "migrations/security/001_core.sql",
            "migrations/ingest/001_ingest.sql",
            "migrations/ingest/002_package_manifest_uploads.sql",
            "migrations/publishing/0001_publishing.sql",
        ):
            apply_sql(isolated_dsn, BACKEND / relative)

        matched_rollout = "history-matched"
        missing_rollout = "history-unassignable"
        insert_rollout(
            isolated_dsn,
            project_id="history-project",
            region_code="cn-east",
            rollout_id=matched_rollout,
        )
        historical = manifest(
            project_id="history-project",
            dataset_id="history-dataset",
            dataset_version="1",
            rollout_ids=(matched_rollout, missing_rollout),
            created_at=NOW - timedelta(hours=1),
        )
        with psycopg.connect(isolated_dsn) as connection:
            connection.execute(
                """
                INSERT INTO publishing.dataset_versions (
                    project_id, dataset_id, dataset_version, base_lance_version,
                    content_hash, manifest_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    historical.project_id,
                    historical.dataset_id,
                    historical.dataset_version,
                    historical.base_lance_version,
                    historical.content_hash,
                    historical.model_dump_json(),
                    historical.created_at,
                ),
            )

        lineage_migration = (
            BACKEND / "migrations/publishing/0002_rollout_publication_region_lineage.sql"
        )
        apply_sql(isolated_dsn, lineage_migration)
        apply_sql(isolated_dsn, lineage_migration)
        yield {
            "dsn": isolated_dsn,
            "matched_rollout": matched_rollout,
            "missing_rollout": missing_rollout,
        }
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


def apply_sql(dsn: str, path: Path) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(path.read_text(encoding="utf-8"))


def insert_rollout(
    dsn: str,
    *,
    project_id: str,
    region_code: str,
    rollout_id: str,
) -> None:
    job_id = f"job-{rollout_id}"
    digest = hashlib.sha256(rollout_id.encode()).hexdigest()
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            SELECT set_config('app.project_id', %s, false),
                   set_config('app.region_code', %s, false),
                   set_config('app.subject_id', 'br01-seed', false)
            """,
            (project_id, region_code),
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
                region_code,
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
                region_code,
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


def published_rollout(rollout_id: str) -> PublishedRolloutV1:
    return PublishedRolloutV1(
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
    )


def manifest(
    *,
    project_id: str,
    dataset_id: str,
    dataset_version: str,
    rollout_ids: tuple[str, ...],
    created_at: datetime = NOW,
    salt: str = "",
) -> PublishedDatasetManifestV1:
    digest = hashlib.sha256(
        f"{project_id}:{dataset_id}:{dataset_version}:{rollout_ids}:{salt}".encode()
    ).hexdigest()
    asset_digest = hashlib.sha256(f"asset:{digest}".encode()).hexdigest()
    training_digest = hashlib.sha256(f"training:{digest}".encode()).hexdigest()
    return PublishedDatasetManifestV1(
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        base_lance_version="v1",
        created_at=created_at,
        content_hash=digest,
        annotations_uri=f"published/{project_id}/{dataset_id}/{dataset_version}/annotations.lance",
        annotations_content_sha256=asset_digest,
        training_manifest_uri=(
            f"published/{project_id}/{dataset_id}/{dataset_version}/training-manifest.json"
        ),
        training_manifest_content_sha256=training_digest,
        rollouts=tuple(published_rollout(rollout_id) for rollout_id in rollout_ids),
    )


@contextmanager
def request_context(project_id: str, region_code: str | None = None) -> Any:
    token = bind_request_context(
        RequestContext(
            project_id=project_id,
            region_code=region_code,
            subject_id="br01-principal",
            request_id=f"request-{uuid4()}",
        )
    )
    try:
        yield
    finally:
        reset_request_context(token)


def dashboard_actor(project_id: str, region_code: str) -> AuthContext:
    return AuthContext(
        subject_id="br01-principal",
        project_ids=frozenset({project_id}),
        region_codes=frozenset({region_code}),
        roles=frozenset(),
        scope_pairs=frozenset({(project_id, region_code)}),
        scoped_capabilities=frozenset({(project_id, CAPABILITY_DASHBOARD_READ)}),
    )


def test_fresh_migration_backfills_only_certain_history_and_is_reentrant(
    isolated_database: dict[str, str],
) -> None:
    dsn = isolated_database["dsn"]
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            """
            SELECT rollout_id, region_code, lineage_source
            FROM publishing.rollout_publication_lineage
            WHERE project_id = 'history-project'
            ORDER BY rollout_id
            """
        ).fetchall()
        assert rows == [(isolated_database["matched_rollout"], "cn-east", "BACKFILL")]
        assert isolated_database["missing_rollout"] not in {row[0] for row in rows}
        assert connection.execute(
            """
            SELECT count(*) FROM pg_trigger
            WHERE tgname = 'rollout_publication_lineage_immutable'
              AND tgrelid = 'publishing.rollout_publication_lineage'::regclass
            """
        ).fetchone() == (1,)
        assert connection.execute(
            """
            SELECT count(*) FROM pg_indexes
            WHERE indexname = 'rollout_publication_lineage_scope_published_idx'
            """
        ).fetchone() == (1,)

    actor = dashboard_actor("history-project", "cn-east")
    with request_context("history-project", "cn-east"):
        summary = PostgresDashboardRepository(
            psycopg_connection_factory(dsn), statement_timeout_ms=5000
        ).publication_lineage_summary(
            auth=actor,
            scope=DashboardScope(actor.subject_id, "history-project", "cn-east"),
            window=DashboardWindow(NOW - timedelta(days=1), NOW + timedelta(days=1), "UTC"),
        )
    assert summary.available is True
    assert summary.lineage_count == 1
    assert summary.publication_count == 1
    assert summary.unresolved_history_count == 1


def test_forward_publication_writes_atomic_idempotent_exact_region_lineage(
    isolated_database: dict[str, str],
) -> None:
    dsn = isolated_database["dsn"]
    project_id = "forward-project"
    rollout_ids = ("forward-east", "forward-west")
    insert_rollout(dsn, project_id=project_id, region_code="cn-east", rollout_id=rollout_ids[0])
    insert_rollout(dsn, project_id=project_id, region_code="cn-west", rollout_id=rollout_ids[1])
    candidate = manifest(
        project_id=project_id,
        dataset_id="dataset-forward",
        dataset_version="1",
        rollout_ids=rollout_ids,
    )
    repository = PostgresPublishedManifestRepository(psycopg_connection_factory(dsn))
    with request_context(project_id):
        first = repository.create_immutable(candidate)
        replay = repository.create_immutable(candidate)
    assert replay == first
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            """
            SELECT rollout_id, region_code, publication_identity
            FROM publishing.rollout_publication_lineage
            WHERE project_id = %s
            ORDER BY rollout_id
            """,
            (project_id,),
        ).fetchall()
    assert rows == [
        (rollout_ids[0], "cn-east", candidate.content_hash),
        (rollout_ids[1], "cn-west", candidate.content_hash),
    ]


def test_concurrent_publication_has_one_immutable_winner_and_matching_lineage(
    isolated_database: dict[str, str],
) -> None:
    dsn = isolated_database["dsn"]
    project_id = "concurrent-project"
    insert_rollout(dsn, project_id=project_id, region_code="cn-east", rollout_id="winner-a")
    insert_rollout(dsn, project_id=project_id, region_code="cn-east", rollout_id="winner-b")
    candidates = (
        manifest(
            project_id=project_id,
            dataset_id="dataset-race",
            dataset_version="1",
            rollout_ids=("winner-a",),
            salt="a",
        ),
        manifest(
            project_id=project_id,
            dataset_id="dataset-race",
            dataset_version="1",
            rollout_ids=("winner-b",),
            salt="b",
        ),
    )

    def publish(candidate: PublishedDatasetManifestV1) -> tuple[str, str]:
        repository = PostgresPublishedManifestRepository(psycopg_connection_factory(dsn))
        try:
            with request_context(project_id):
                saved = repository.create_immutable(candidate)
            return "saved", saved.content_hash
        except ProblemException as exc:
            return exc.problem.code, candidate.content_hash

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(publish, candidates))
    assert [outcome[0] for outcome in outcomes].count("saved") == 1
    assert [outcome[0] for outcome in outcomes].count("DATASET_VERSION_IMMUTABLE") == 1
    winner_hash = next(outcome[1] for outcome in outcomes if outcome[0] == "saved")
    with psycopg.connect(dsn) as connection:
        stored_hash = connection.execute(
            """
            SELECT content_hash FROM publishing.dataset_versions
            WHERE project_id = %s AND dataset_id = 'dataset-race' AND dataset_version = '1'
            """,
            (project_id,),
        ).fetchone()
        lineages = connection.execute(
            """
            SELECT publication_identity FROM publishing.rollout_publication_lineage
            WHERE project_id = %s AND dataset_id = 'dataset-race' AND dataset_version = '1'
            """,
            (project_id,),
        ).fetchall()
    assert stored_hash == (winner_hash,)
    assert lineages == [(winner_hash,)]


def test_unresolved_forward_rollout_rolls_back_dataset_and_lineage(
    isolated_database: dict[str, str],
) -> None:
    dsn = isolated_database["dsn"]
    project_id = "unresolved-project"
    candidate = manifest(
        project_id=project_id,
        dataset_id="dataset-unresolved",
        dataset_version="1",
        rollout_ids=("not-in-ingest",),
    )
    repository = PostgresPublishedManifestRepository(psycopg_connection_factory(dsn))
    with request_context(project_id), pytest.raises(ProblemException) as captured:
        repository.create_immutable(candidate)
    assert captured.value.problem.code == "PUBLICATION_REGION_LINEAGE_UNRESOLVED"
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM publishing.dataset_versions WHERE project_id = %s",
            (project_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM publishing.rollout_publication_lineage WHERE project_id = %s",
            (project_id,),
        ).fetchone() == (0,)

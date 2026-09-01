from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn, psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
from hc_data_platform.robot_ingest.models import (
    CompleteAssetCommand,
    CompletedPart,
    CreateRobotIngestIdentity,
    IssueCredentialCommand,
    QualityStatus,
    RobotIngestAssetManifest,
    RobotIngestEpisodeResult,
    RobotIngestUploadManifest,
)
from hc_data_platform.robot_ingest.repository import PostgresRobotIngestRepository
from hc_data_platform.robot_ingest.service import RobotIngestService
from hc_data_platform.security.auth import AuthContext

DSN = os.environ.get("ROBOT_INGEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="ROBOT_INGEST_POSTGRES_DSN must name a fully migrated disposable database",
)

NOW = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
ORG = "robot-pg-org"
PROJECT_A = "robot-pg-project-a"
PROJECT_B = "robot-pg-project-b"
REGION_A = "cn-robot-a"
REGION_B = "cn-robot-b"
ROBOT = "robot-pg-g1"
TASK_A = "robot-pg-task-a"
TASK_B = "robot-pg-task-b"


def _admin() -> AuthContext:
    capabilities = {
        (ORG, PROJECT_A, "ingest_source.read"),
        (ORG, PROJECT_A, "ingest_source.manage"),
        (ORG, PROJECT_B, "ingest_source.read"),
        (ORG, PROJECT_B, "ingest_source.manage"),
    }
    return AuthContext(
        subject_id="robot-pg-admin",
        organization_ids=frozenset({ORG}),
        project_ids=frozenset({PROJECT_A, PROJECT_B}),
        region_codes=frozenset({REGION_A, REGION_B}),
        scope_pairs=frozenset({(PROJECT_A, REGION_A), (PROJECT_B, REGION_B)}),
        organization_scope_triples=frozenset(
            {(ORG, PROJECT_A, REGION_A), (ORG, PROJECT_B, REGION_B)}
        ),
        organization_scoped_capabilities=frozenset(capabilities),
    )


def _setup_database(dsn: str) -> None:
    import psycopg

    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        connection.execute("SELECT set_config('app.platform_admin', 'true', false)")
        for project in (PROJECT_A, PROJECT_B):
            connection.execute(
                """INSERT INTO registry.organization_projects (
                       organization_id, project_id, display_name
                   ) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                (ORG, project, project),
            )
        connection.execute(
            """INSERT INTO robotics.robot_assets (
                   organization_id, robot_id, display_name, serial_no,
                   lifecycle_status, connectivity_state, etag, topology_revision
               ) VALUES (%s, %s, %s, %s, 'ACTIVE', 'ONLINE', %s, %s)
               ON CONFLICT DO NOTHING""",
            (ORG, ROBOT, "Postgres G1", "robot-pg-serial", "etag-1", "topology-1"),
        )
        for task_id, project_id, task_code, region in (
            (TASK_A, PROJECT_A, "91000001", REGION_A),
            (TASK_B, PROJECT_B, "91000002", REGION_B),
        ):
            connection.execute(
                """INSERT INTO collection_tasks.collection_tasks (
                       collection_task_id, organization_id, project_id, dataset_id,
                       task_code, name, task_type, scenario, status, create_fingerprint,
                       upload_region_code
                   ) VALUES (%s, %s, %s, %s, %s, %s, 'ROBOT_CAPTURE', 'postgres',
                             'ACTIVE', %s, %s)
                   ON CONFLICT DO NOTHING""",
                (
                    task_id,
                    ORG,
                    project_id,
                    f"dataset_{project_id.replace('-', '_')}",
                    task_code,
                    task_id,
                    hashlib.sha256(task_id.encode()).hexdigest(),
                    region,
                ),
            )


def _manifest(
    task_id: str,
    client_upload_id: str,
    body: bytes,
    *,
    collection_job_id: str | None = None,
) -> RobotIngestUploadManifest:
    return RobotIngestUploadManifest(
        client_upload_id=client_upload_id,
        collection_task_id=task_id,
        collection_job_id=collection_job_id,
        robot_id=ROBOT,
        capture_mode="CONTINUOUS",
        source_format="MCAP",
        source_format_version="1.0",
        capture_started_at=NOW,
        capture_ended_at=NOW + timedelta(hours=2),
        assets=(
            RobotIngestAssetManifest(
                asset_id="raw",
                path="capture.mcap",
                media_type="application/x-mcap",
                size_bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
                crc64=crc64_ecma(body),
            ),
        ),
    )


def test_postgres_robot_identity_cross_project_raw_idempotency_and_rls() -> None:
    assert DSN is not None
    _setup_database(DSN)
    storage = InMemoryObjectStorage()
    repository = PostgresRobotIngestRepository.from_dsn(
        DSN,
        psycopg_connection_factory(DSN),
    )
    service = RobotIngestService(
        repository,
        storage,
        credential_hmac_key="robot-postgres-integration-hmac-key",
        clock=lambda: NOW,
    )
    context_token = bind_request_context(
        RequestContext(
            organization_id=ORG,
            project_id=PROJECT_A,
            region_code=REGION_A,
            subject_id="robot-pg-admin",
        )
    )
    try:
        identity = service.create_identity(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            command=CreateRobotIngestIdentity(
                robot_id=ROBOT,
                allowed_formats=("MCAP",),
            ),
        ).data
        credential = service.issue_credential(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            ingest_identity_id=identity.ingest_identity_id,
            command=IssueCredentialCommand(),
        ).credential
        assert credential is not None
        _assert_wrong_region_collection_job_is_rejected(
            DSN,
            service=service,
            credential=credential.token,
        )
        body = b"postgres-robot-raw"
        client_upload_id = str(uuid4())
        created = service.create_upload(
            token=credential.token,
            manifest=_manifest(TASK_A, client_upload_id, body),
        ).data
        _assert_collection_job_authority_is_immutable(DSN, created.collection_job_id)
        part = storage.upload_part(
            created.assets[0].multipart_upload_id,
            1,
            body,
            key=created.assets[0].object_key,
        )
        completed = service.complete_asset(
            token=credential.token,
            upload_id=created.upload_id,
            asset_id="raw",
            command=CompleteAssetCommand(parts=(CompletedPart(part_number=1, etag=part.etag),)),
        ).data
        committed = service.commit_upload(
            token=credential.token,
            upload_id=completed.upload_id,
        ).data
        processed = service.apply_processing_result(
            organization_id=ORG,
            upload_id=committed.upload_id,
            ingest_identity_id=identity.ingest_identity_id,
            episode_results=tuple(
                RobotIngestEpisodeResult(
                    episode_id=f"episode_pg_{index}",
                    source_episode_index=index,
                    status="READY",
                    frame_count=600,
                    sample_count=600,
                    dataset_version=1,
                    lance_version=1,
                    quality_status=(QualityStatus.RISK if index == 6 else QualityStatus.PASS),
                    qc_report_id=f"qc_pg_{index}",
                    created_at=NOW,
                    updated_at=NOW,
                )
                for index in range(7)
            ),
        )
        lineage = service.list_upload_episode_results(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            region_code=REGION_A,
            upload_id=committed.upload_id,
        )
        replayed = service.create_upload(
            token=credential.token,
            manifest=_manifest(TASK_A, client_upload_id, body),
        )
        cross_project = service.create_upload(
            token=credential.token,
            manifest=_manifest(TASK_B, str(uuid4()), body),
        ).data
        project_a_history = service.list_robot_uploads(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            region_code=REGION_A,
            robot_id=ROBOT,
        )
        project_a_attempts = service.list_attempts(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            region_code=REGION_A,
            robot_id=ROBOT,
        )
        project_a_statistics = service.statistics(
            auth=_admin(),
            organization_id=ORG,
            project_id=PROJECT_A,
            region_code=REGION_A,
            robot_id=ROBOT,
        )

        assert replayed.resumed is True
        assert replayed.data.upload_id == committed.upload_id
        assert committed.raw_source_id is not None
        assert processed.derived_episode_count == 7
        assert processed.processing_status.value == "READY"
        assert cross_project.target.project_id == PROJECT_B
        assert cross_project.ingest_identity_id == identity.ingest_identity_id
        assert repository.list_uploads(organization_id=ORG, robot_id=ROBOT)
        assert [item.upload_id for item in project_a_history.items] == [committed.upload_id]
        assert cross_project.upload_id not in {item.upload_id for item in project_a_attempts.items}
        assert project_a_statistics.upload_batch_count == 1
        assert project_a_statistics.committed_raw_count == 1

        _assert_raw_processing_facts(DSN, committed.raw_source_id)
        assert len(lineage.items) == 7
        assert lineage.items[-1].qc_report_id == "qc_pg_6"
        _assert_rls_isolation(DSN)
        _assert_auth_observations_and_robot_lifecycle_fence(
            DSN,
            service=service,
            ingest_identity_id=identity.ingest_identity_id,
            credential=credential.token,
        )
    finally:
        reset_request_context(context_token)


def test_collection_task_id_is_globally_unique_in_postgres() -> None:
    assert DSN is not None
    _setup_database(DSN)

    import psycopg

    with psycopg.connect(normalize_postgres_dsn(DSN)) as connection:
        connection.execute("SELECT set_config('app.platform_admin', 'true', false)")
        with pytest.raises(psycopg.errors.UniqueViolation) as caught:
            connection.execute(
                """INSERT INTO collection_tasks.collection_tasks (
                       collection_task_id, organization_id, project_id, dataset_id,
                       task_code, name, task_type, scenario, status, create_fingerprint,
                       upload_region_code
                   ) VALUES (%s, %s, %s, %s, '91999999', 'ambiguous duplicate',
                             'ROBOT_CAPTURE', 'postgres', 'ACTIVE', %s, %s)""",
                (
                    TASK_A,
                    ORG,
                    PROJECT_B,
                    "dataset_duplicate_task",
                    hashlib.sha256(b"duplicate-global-task-id").hexdigest(),
                    REGION_B,
                ),
            )
        assert caught.value.diag.constraint_name == "collection_tasks_global_upload_identity_uq"


def _assert_rls_isolation(dsn: str) -> None:
    import psycopg
    from psycopg import sql

    role = f"hc_robot_rls_{uuid4().hex[:16]}"
    with psycopg.connect(normalize_postgres_dsn(dsn), autocommit=True) as connection:
        try:
            connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA ingest TO {}").format(sql.Identifier(role))
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT ON ingest.robot_ingest_uploads, "
                    "ingest.robot_ingest_identities, ingest.robot_ingest_attempts TO {}"
                ).format(sql.Identifier(role))
            )
            connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            connection.execute(
                """SELECT set_config('app.platform_admin', 'false', false),
                          set_config('app.organization_id', %s, false),
                          set_config('app.project_id', %s, false),
                          set_config('app.region_code', %s, false)""",
                (ORG, PROJECT_A, REGION_A),
            )
            visible_projects = {
                row[0]
                for row in connection.execute(
                    "SELECT project_id FROM ingest.robot_ingest_uploads"
                ).fetchall()
            }
            visible_identities = connection.execute(
                "SELECT count(*) FROM ingest.robot_ingest_identities"
            ).fetchone()
            visible_attempt_projects = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT project_id FROM ingest.robot_ingest_attempts "
                    "WHERE project_id IS NOT NULL"
                ).fetchall()
            }
            assert visible_projects == {PROJECT_A}
            assert visible_identities == (1,)
            assert visible_attempt_projects == {PROJECT_A}
        finally:
            connection.execute("RESET ROLE")
            connection.execute(
                sql.SQL(
                    "REVOKE SELECT ON ingest.robot_ingest_uploads, "
                    "ingest.robot_ingest_identities, ingest.robot_ingest_attempts FROM {}"
                ).format(sql.Identifier(role))
            )
            connection.execute(
                sql.SQL("REVOKE USAGE ON SCHEMA ingest FROM {}").format(sql.Identifier(role))
            )
            connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _assert_raw_processing_facts(dsn: str, raw_source_id: str) -> None:
    import psycopg

    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        connection.execute("SELECT set_config('app.platform_admin', 'true', false)")
        facts = connection.execute(
            """SELECT source.derived_episode_count, source.verified_frame_count,
                      source.verified_sample_count, source.qc_pass_episode_count,
                      source.qc_risk_episode_count, source.quality_status,
                      source.processing_status, job.status
                 FROM ingest.raw_sources source
                 JOIN ingest.raw_ingest_jobs job
                   ON job.organization_id = source.organization_id
                  AND job.project_id = source.project_id
                  AND job.region_code = source.region_code
                  AND job.raw_source_id = source.raw_source_id
                WHERE source.raw_source_id = %s""",
            (raw_source_id,),
        ).fetchone()
        assert facts == (7, 4_200, 4_200, 6, 1, "RISK", "READY", "SUCCEEDED")


def _assert_auth_observations_and_robot_lifecycle_fence(
    dsn: str,
    *,
    service: RobotIngestService,
    ingest_identity_id: str,
    credential: str,
) -> None:
    import psycopg

    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        connection.execute("SELECT set_config('app.platform_admin', 'true', false)")
        observed = connection.execute(
            """SELECT last_authenticated_at, last_seen_at
                 FROM ingest.robot_ingest_identities
                WHERE organization_id = %s AND ingest_identity_id = %s""",
            (ORG, ingest_identity_id),
        ).fetchone()
        assert observed == (NOW, NOW)
        connection.execute(
            """UPDATE robotics.robot_assets SET lifecycle_status = 'DISABLED'
                 WHERE organization_id = %s AND robot_id = %s""",
            (ORG, ROBOT),
        )
        connection.commit()
    try:
        with pytest.raises(ProblemException) as caught:
            service.authenticate(credential)
        assert caught.value.problem.code == "ROBOT_IDENTITY_DISABLED"
    finally:
        with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
            connection.execute("SELECT set_config('app.platform_admin', 'true', false)")
            connection.execute(
                """UPDATE robotics.robot_assets SET lifecycle_status = 'ACTIVE'
                     WHERE organization_id = %s AND robot_id = %s""",
                (ORG, ROBOT),
            )


def _assert_wrong_region_collection_job_is_rejected(
    dsn: str,
    *,
    service: RobotIngestService,
    credential: str,
) -> None:
    import psycopg

    job_id = "robot-pg-wrong-region-job"
    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        connection.execute("SELECT set_config('app.platform_admin', 'true', false)")
        connection.execute(
            """INSERT INTO ingest.collection_jobs (
                   organization_id, project_id, region_code, task_id, collection_job_id,
                   robot_id, status, created_at, updated_at
               ) VALUES (%s, %s, %s, %s, %s, %s, 'REGISTERED', %s, %s)
               ON CONFLICT (project_id, collection_job_id) DO UPDATE
                   SET region_code = EXCLUDED.region_code,
                       task_id = EXCLUDED.task_id,
                       robot_id = EXCLUDED.robot_id,
                       status = EXCLUDED.status,
                       updated_at = EXCLUDED.updated_at""",
            (ORG, PROJECT_A, REGION_B, TASK_A, job_id, ROBOT, NOW, NOW),
        )
    with pytest.raises(ProblemException) as caught:
        service.create_upload(
            token=credential,
            manifest=_manifest(
                TASK_A,
                str(uuid4()),
                b"wrong-region-job",
                collection_job_id=job_id,
            ),
        )
    assert caught.value.problem.code == "COLLECTION_JOB_MISMATCH"


def _assert_collection_job_authority_is_immutable(dsn: str, collection_job_id: str) -> None:
    import psycopg

    with psycopg.connect(normalize_postgres_dsn(dsn)) as connection:
        connection.execute("SELECT set_config('app.platform_admin', 'true', false)")
        with pytest.raises(psycopg.errors.ForeignKeyViolation) as caught:
            connection.execute(
                """UPDATE ingest.collection_jobs SET region_code = %s
                     WHERE project_id = %s AND collection_job_id = %s""",
                (REGION_B, PROJECT_A, collection_job_id),
            )
        assert caught.value.diag.constraint_name == "robot_ingest_uploads_job_authority_fk"

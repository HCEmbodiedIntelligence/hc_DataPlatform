"""Real binding transactions: model lock, concurrent replay and failure rollback."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    bind_request_context,
    current_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.episode_store.models import EpisodeIdentity
from hc_data_platform.registry.binding_queries import published_model_for_binding
from hc_data_platform.robot_assets.binding_queries import model_has_binding_history
from hc_data_platform.robot_assets.repository import PostgresOrganizationRobotAssetRepository
from hc_data_platform.runtime import build_runtime
from hc_data_platform.security.auth import AuthContext
from tests.episode_store.test_commit_services import scoped
from tests.robotics_scene_fixture import scene_setup


@pytest.mark.integration
def test_model_binding_is_one_locked_transaction_with_idempotency_and_rollback(
    tmp_path, monkeypatch
):
    dsn = os.environ["HC_G1_TEST_POSTGRES_DSN"]
    suffix = uuid4().hex
    ident = EpisodeIdentity(
        organization_id="binding-" + suffix,
        project_id="project-" + suffix,
        region_code="region",
        dataset_id="dataset-" + suffix,
        episode_id="episode",
        rollout_id="rollout",
    )
    settings = Settings(
        environment="test",
        postgres_dsn=dsn.replace("postgresql://", "postgresql+asyncpg://"),
        auth_abuse_enabled=False,
        object_store_endpoint="http://minio:9000",
        object_store_public_endpoint="http://minio:9000",
        object_store_bucket="hc-data-local",
        lance_root_uri=str(tmp_path / "lance"),
        alignment_staging_root=str(tmp_path / "align"),
        aligned_media_staging_root=str(tmp_path / "media"),
        robot_model_asset_root=str(tmp_path / "models"),
    )
    runtime = build_runtime(settings, include_media=True)
    connect = psycopg_connection_factory(dsn)
    repository = PostgresOrganizationRobotAssetRepository(
        connect, published_models=published_model_for_binding
    )
    with scoped(SimpleNamespace(identity=ident)):
        with connect() as db:
            db.execute(
                "INSERT INTO registry.organization_projects VALUES (%s,%s,%s)",
                (ident.organization_id, ident.project_id, "Binding transaction test"),
            )
        robot_id, first, second, _, _ = scene_setup(
            runtime,
            AuthContext(
                subject_id="binding-test",
                organization_ids=frozenset({ident.organization_id}),
                project_ids=frozenset({ident.project_id}),
                region_codes=frozenset({ident.region_code}),
                organization_scope_triples=frozenset(
                    {ident.scope, (ident.organization_id, ident.project_id, None)}
                ),
                scope_pairs=frozenset(
                    {(ident.project_id, ident.region_code), (ident.project_id, None)}
                ),
                capabilities=frozenset(
                    {
                        "robot.read",
                        "robot.create",
                        "robot.manage",
                        "robot_model.read",
                        "robot_model.manage",
                    }
                ),
            ),
            ident.organization_id,
            ident.project_id,
            connect,
        )
        context = current_request_context()
        initial = repository.get_robot(organization_id=ident.organization_id, robot_id=robot_id)
        arguments = dict(
            organization_id=ident.organization_id,
            robot_id=robot_id,
            version_id=second[0],
            expected_robot_etag=initial.etag,
            idempotency_key="concurrent-binding",
            request_fingerprint="a" * 64,
            actor_id="binding-test",
            request_id=suffix,
            occurred_at=datetime.now(timezone.utc),
        )

        def bind(**changes):
            token = bind_request_context(replace(context, request_id=uuid4().hex))
            try:
                return repository.bind_model(**{**arguments, **changes})
            finally:
                reset_request_context(token)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = tuple(pool.map(lambda _: bind(), range(4)))
        assert all(result == results[0] for result in results)
        with pytest.raises(ValueError, match="etag"):
            bind(idempotency_key="stale", request_fingerprint="b" * 64)

        def snapshot():
            with connect() as db:
                bindings = db.execute(
                    "SELECT binding_id,status,unbound_at "
                    "FROM registry.organization_robot_model_bindings "
                    "WHERE organization_id=%s AND robot_id=%s ORDER BY binding_id",
                    (ident.organization_id, robot_id),
                ).fetchall()
                receipts = db.execute(
                    "SELECT count(*) FROM robotics.robot_asset_command_receipts "
                    "WHERE organization_id=%s",
                    (ident.organization_id,),
                ).fetchone()[0]
            return (
                repository.get_robot(organization_id=ident.organization_id, robot_id=robot_id),
                bindings,
                receipts,
            )

        before = snapshot()
        assert [row[1] for row in before[1]].count("ACTIVE") == 1
        assert [row[1] for row in before[1]].count("SUPERSEDED") == 1

        def fail_audit(*args, **kwargs):
            raise RuntimeError("injected after binding and receipt writes")

        with monkeypatch.context() as patch:
            patch.setattr(repository, "_append_audit", fail_audit)
            with pytest.raises(RuntimeError, match="injected"):
                bind(
                    version_id=first[0],
                    expected_robot_etag=before[0].etag,
                    idempotency_key="failure-retry",
                    request_fingerprint="c" * 64,
                )
        assert snapshot() == before

        locked, proceed = Event(), Event()

        def held_model(cursor, organization_id, version_id):
            model = published_model_for_binding(cursor, organization_id, version_id)
            locked.set()
            assert proceed.wait(10), "test model lock was not released"
            return model

        repository._published_models = held_model
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                bind,
                version_id=first[0],
                expected_robot_etag=before[0].etag,
                idempotency_key="failure-retry",
                request_fingerprint="c" * 64,
            )
            assert locked.wait(10)
            try:
                with pytest.raises(psycopg.errors.LockNotAvailable), connect() as db:
                    db.execute(
                        "SELECT version_id FROM registry.robot_model_versions "
                        "WHERE organization_id=%s AND version_id=%s FOR UPDATE NOWAIT",
                        (ident.organization_id, first[0]),
                    )
            finally:
                proceed.set()
            assert future.result(timeout=10).version_id == first[0]
        with connect() as db, db.cursor() as cursor:
            assert model_has_binding_history(cursor, ident.organization_id, first[0])
            assert model_has_binding_history(cursor, ident.organization_id, second[0])
            assert not model_has_binding_history(cursor, "foreign-organization", first[0])
            with pytest.raises(KeyError):
                published_model_for_binding(cursor, "foreign-organization", first[0])

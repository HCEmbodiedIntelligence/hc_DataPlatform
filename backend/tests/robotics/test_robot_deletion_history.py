from datetime import datetime, timezone

import pytest

from hc_data_platform.registry.binding_queries import published_model_for_binding
from hc_data_platform.robot_assets.repository import PostgresOrganizationRobotAssetRepository
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw  # noqa: F401

pytest_plugins = ["tests.episode_store.test_commit_services"]
pytestmark = pytest.mark.integration


def test_uploaded_recording_identity_and_raw_are_preserved_on_delete(uploaded_raw):  # noqa: F811
    repository, _, raw, _, command, _ = uploaded_raw
    org, rid = raw.target.organization_id, command.robot_id
    repo = PostgresOrganizationRobotAssetRepository(
        repository.connections, published_models=published_model_for_binding
    )

    def snapshot():
        with repository.connections() as db:
            return {
                table: db.execute(
                    f"SELECT row_to_json(t) FROM {table} t WHERE organization_id=%s", (org,)
                ).fetchall()
                for table in (
                    "robotics.robot_assets",
                    "ingest.raw_sources",
                    "ingest.robot_ingest_uploads",
                    "ingest.robot_ingest_identities",
                    "ingest.robot_ingest_credentials",
                )
            }

    before = snapshot()
    with pytest.raises(ValueError, match="原始数据 1 条.*上传记录 1 条"):
        repo.delete_robot(
            organization_id=org,
            robot_id=rid,
            actor_id="tester",
            request_id="protect-history",
            occurred_at=datetime.now(timezone.utc),
        )
    assert snapshot() == before

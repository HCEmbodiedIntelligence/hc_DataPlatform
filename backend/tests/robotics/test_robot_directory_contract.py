from __future__ import annotations

from pathlib import Path

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.health import ReadinessProbe


class ReadyProbe:
    async def check(self) -> None:
        return None


def test_robot_assets_publish_only_the_organization_contract() -> None:
    ready: ReadinessProbe = ReadyProbe()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={"postgresql": ready, "temporal": ready, "object_storage": ready},
    )
    paths = app.openapi()["paths"]
    root = "/api/v1/organizations/{organization_id}/robots"
    assert paths[root]["get"]["operationId"] == "listOrganizationRobots"
    assert paths[root]["post"]["operationId"] == "createOrganizationRobot"
    assert (
        paths[f"{root}/{{robot_id}}/bootstrap"]["get"]["operationId"]
        == "getOrganizationRobotBootstrap"
    )
    assert (
        paths[f"{root}/{{robot_id}}/model-bindings"]["post"]["operationId"]
        == "bindOrganizationRobotModel"
    )
    assert (
        paths[f"{root}/model-bindings"]["get"]["operationId"]
        == "listOrganizationRobotModelBindings"
    )
    assert not any(
        path.startswith("/api/v1/projects/{project_id}/regions/{region_code}/robots")
        for path in paths
    )


def test_robot_asset_migration_removes_the_project_tables() -> None:
    migration = (
        Path(__file__).parents[2]
        / "migrations/robotics/0005_organization_robot_assets.sql"
    ).read_text(encoding="utf-8")
    for marker in (
        "CREATE TABLE IF NOT EXISTS robotics.robot_assets",
        "CREATE TABLE IF NOT EXISTS robotics.project_robot_assignments",
        "CREATE TABLE IF NOT EXISTS registry.organization_robot_model_bindings",
        "DROP TABLE IF EXISTS robotics.robot_instances",
        "DROP TABLE IF EXISTS robotics.robot_components",
        "DROP TABLE IF EXISTS robotics.component_frames",
        "DROP TABLE IF EXISTS robotics.component_channels",
        "DROP TABLE IF EXISTS registry.robot_model_bindings",
    ):
        assert marker in migration

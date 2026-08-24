from __future__ import annotations

from pathlib import Path

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.health import ReadinessProbe


class ReadyProbe:
    async def check(self) -> None:
        return None


def test_p15_runtime_openapi_and_migration_have_real_management_contracts() -> None:
    ready: ReadinessProbe = ReadyProbe()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={"postgresql": ready, "temporal": ready, "object_storage": ready},
    )
    root = "/api/v1/projects/{project_id}/regions/{region_code}"
    paths = app.openapi()["paths"]
    assert {
        path
        for path in paths
        if path.startswith(root)
        and ("/robots" in path or "/components" in path or "/search" in path)
    } == {
        f"{root}/robots",
        f"{root}/search",
        f"{root}/robots/{{robot_id}}",
        f"{root}/robots/{{robot_id}}:transition",
        f"{root}/robots/{{robot_id}}/maintenance-records",
        f"{root}/robots/{{robot_id}}/bootstrap",
        f"{root}/robots/{{robot_id}}/components",
        f"{root}/components/{{component_id}}",
        f"{root}/components/{{component_id}}:transition",
        f"{root}/components/{{component_id}}/frames",
        f"{root}/components/{{component_id}}/channels",
    }
    assert paths[f"{root}/robots"]["get"]["operationId"] == "listRobots"
    assert paths[f"{root}/search"]["get"]["operationId"] == "searchRobots"
    assert (
        paths[f"{root}/robots/{{robot_id}}/bootstrap"]["get"]["operationId"] == "getRobotBootstrap"
    )
    assert paths[f"{root}/robots"]["post"]["operationId"] == "createRobot"
    assert paths[f"{root}/robots/{{robot_id}}"]["patch"]["operationId"] == "updateRobot"
    assert (
        paths[f"{root}/robots/{{robot_id}}:transition"]["post"]["operationId"]
        == "transitionRobotLifecycle"
    )
    assert (
        paths[f"{root}/robots/{{robot_id}}/maintenance-records"]["post"]["operationId"]
        == "createRobotMaintenanceRecord"
    )
    assert (
        paths[f"{root}/robots/{{robot_id}}/components"]["post"]["operationId"]
        == "createRobotComponent"
    )
    assert (
        paths[f"{root}/components/{{component_id}}"]["patch"]["operationId"]
        == "updateRobotComponent"
    )
    assert (
        paths[f"{root}/components/{{component_id}}:transition"]["post"]["operationId"]
        == "transitionRobotComponentLifecycle"
    )

    migration = Path(__file__).parents[2] / "migrations/robotics/0001_robot_directory.sql"
    text = migration.read_text(encoding="utf-8")
    for marker in (
        "robotics.robot_instances",
        "robotics.robot_components",
        "robotics.component_frames",
        "robotics.component_channels",
        "core.apply_project_rls('robotics.robot_instances'::regclass)",
    ):
        assert marker in text

    management_migration = (
        Path(__file__).parents[2] / "migrations/robotics/0002_robot_management.sql"
    ).read_text(encoding="utf-8")
    for marker in (
        "robotics.robot_maintenance_records",
        "robotics.robot_command_receipts",
        "core.apply_project_rls('robotics.robot_maintenance_records'::regclass)",
    ):
        assert marker in management_migration

    component_migration = (
        Path(__file__).parents[2] / "migrations/robotics/0003_component_management.sql"
    ).read_text(encoding="utf-8")
    for marker in (
        "robotics_robot_components_parent_same_robot_fk",
        "COMPONENT_LIFECYCLE_TRANSITION",
        "resource_type IN ('ROBOT', 'COMPONENT')",
    ):
        assert marker in component_migration

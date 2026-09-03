from __future__ import annotations

from pathlib import Path

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.health import ReadinessProbe


class ReadyProbe:
    async def check(self) -> None:
        return None


def test_p14_runtime_openapi_has_real_asset_transfers_and_no_product_501() -> None:
    ready: ReadinessProbe = ReadyProbe()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={"postgresql": ready, "temporal": ready, "object_storage": ready},
    )
    paths = app.openapi()["paths"]
    root = "/api/v1/organizations/{organization_id}"
    robot_model_paths = {path for path in paths if "/robot-model" in path and path.startswith(root)}
    assert robot_model_paths == {
        f"{root}/robot-models",
        f"{root}/robot-model-versions/{{version_id}}",
        f"{root}/robot-model-versions/{{version_id}}:discard-import",
        f"{root}/robot-model-versions/{{version_id}}:create-draft",
        f"{root}/robot-model-versions/{{version_id}}:preflight-publish",
        f"{root}/robot-model-versions/{{version_id}}:publish",
        f"{root}/robot-model-versions/{{version_id}}/joint-mappings",
        f"{root}/robot-model-versions/{{version_id}}/upload-sessions",
        f"{root}/robot-model-asset-uploads/{{upload_id}}:authorize-parts",
        f"{root}/robot-model-asset-uploads/{{upload_id}}:complete-file",
        f"{root}/robot-model-assets/upload-part",
        f"{root}/robot-model-assets/content",
        f"{root}/robot-model-versions/{{version_id}}/assets",
        f"{root}/robot-model-versions/{{version_id}}/assets/{{asset_id}}/download",
    }
    assert paths[f"{root}/robot-models"]["get"]["operationId"] == "listRobotModels"
    assert paths[f"{root}/robot-models"]["post"]["operationId"] == "createRobotModel"
    assert (
        paths[f"{root}/robot-model-versions/{{version_id}}"]["get"]["operationId"]
        == "getRobotModelVersion"
    )
    assert (
        paths[f"{root}/robot-model-versions/{{version_id}}:discard-import"]["delete"]["operationId"]
        == "discardRobotModelImport"
    )
    assert (
        paths[f"{root}/robot-model-versions/{{version_id}}/upload-sessions"]["post"]["operationId"]
        == "createRobotModelAssetUploadSession"
    )
    assert (
        paths[f"{root}/robot-model-asset-uploads/{{upload_id}}:complete-file"]["post"][
            "operationId"
        ]
        == "completeRobotModelAssetUploadFile"
    )
    assert (
        paths[f"{root}/robot-model-versions/{{version_id}}/assets/{{asset_id}}/download"]["get"][
            "operationId"
        ]
        == "authorizeRobotModelAssetDownload"
    )
    assert (
        paths[f"{root}/robot-model-assets/upload-part"]["put"]["operationId"]
        == "uploadRobotModelAssetPartToServer"
    )
    assert paths[f"{root}/robot-model-assets/upload-part"]["put"]["security"] == []
    assert (
        paths[f"{root}/robot-model-assets/content"]["get"]["operationId"]
        == "downloadRobotModelAssetFromServer"
    )
    assert paths[f"{root}/robot-model-assets/content"]["get"]["security"] == []
    assert (
        paths[f"{root}/robot-model-versions/{{version_id}}/joint-mappings"]["put"]["operationId"]
        == "replaceRobotModelJointMappings"
    )
    assert f"{root}/robot-model-versions/{{version_id}}/bindings" not in paths
    assert (
        paths[f"{root}/robots/{{robot_id}}/model-bindings"]["post"]["operationId"]
        == "bindOrganizationRobotModel"
    )
    for path in robot_model_paths:
        for operation in paths[path].values():
            if not isinstance(operation, dict):
                continue
            assert "501" not in operation.get("responses", {})

    migration = Path(__file__).parents[2] / "migrations/registry/0001_robot_model_registry.sql"
    text = migration.read_text(encoding="utf-8")
    for marker in (
        "registry.organization_projects",
        "registry.robot_models",
        "registry.robot_model_versions",
        "registry.audit_events",
        "core.apply_project_rls('registry.organization_projects'::regclass)",
    ):
        assert marker in text

    asset_migration = Path(__file__).parents[2] / "migrations/registry/0002_robot_model_assets.sql"
    asset_text = asset_migration.read_text(encoding="utf-8")
    for marker in (
        "registry.robot_model_asset_uploads",
        "registry.robot_model_asset_upload_files",
        "registry.robot_model_assets",
        "core.apply_project_rls('registry.robot_model_assets'::regclass)",
    ):
        assert marker in asset_text

    publication_migration = (
        Path(__file__).parents[2] / "migrations/registry/0003_robot_model_publication_controls.sql"
    )
    publication_text = publication_migration.read_text(encoding="utf-8")
    for marker in (
        "registry.robot_model_joint_mappings",
        "registry.robot_model_publish_preflights",
    ):
        assert marker in publication_text

    receipt_migration = (
        Path(__file__).parents[2] / "migrations/registry/0004_robot_model_command_receipts.sql"
    )
    receipt_text = receipt_migration.read_text(encoding="utf-8")
    assert "registry.robot_model_command_receipts" in receipt_text

    creation_migration = (
        Path(__file__).parents[2] / "migrations/registry/0006_robot_model_creation.sql"
    )
    creation_text = creation_migration.read_text(encoding="utf-8")
    assert "CREATE_MODEL_DRAFT" in creation_text

    organization_robot_migration = (
        Path(__file__).parents[2] / "migrations/robotics/0005_organization_robot_assets.sql"
    ).read_text(encoding="utf-8")
    assert "registry.organization_robot_model_bindings" in organization_robot_migration
    assert "DROP TABLE IF EXISTS registry.robot_model_bindings" in organization_robot_migration

from __future__ import annotations

from pathlib import Path

from hc_data_platform.core.app import create_app
from hc_data_platform.core.config import Settings
from hc_data_platform.core.health import ReadinessProbe


class ReadyProbe:
    async def check(self) -> None:
        return None


def test_p16_runtime_openapi_has_reads_and_durable_publication() -> None:
    ready: ReadinessProbe = ReadyProbe()
    app = create_app(
        settings=Settings(environment="test", runtime_backend="memory"),
        readiness_probes={"postgresql": ready, "temporal": ready, "object_storage": ready},
    )
    root = "/api/v1/projects/{project_id}/regions/{region_code}/calibration-sets"
    paths = app.openapi()["paths"]
    assert {path for path in paths if path.startswith(root)} == {
        root,
        f"{root}/{{set_id}}",
        f"{root}/{{set_id}}/versions",
        f"{root}/{{set_id}}/versions/{{version}}/dataset-associations",
        f"{root}/{{set_id}}/versions/{{version}}/document",
        f"{root}/{{set_id}}/versions/{{version}}:validate",
        f"{root}/{{set_id}}/versions/{{version}}:preflight-publish",
        f"{root}/{{set_id}}/versions/{{version}}:publish",
    }
    assert paths[root]["get"]["operationId"] == "listCalibrationSets"
    assert paths[root]["post"]["operationId"] == "createCalibrationSet"
    assert paths[f"{root}/{{set_id}}"]["get"]["operationId"] == "getCalibrationSet"
    assert paths[f"{root}/{{set_id}}/versions"]["get"]["operationId"] == "listCalibrationVersions"
    assert (
        paths[f"{root}/{{set_id}}/versions"]["post"]["operationId"] == "recalibrateCalibrationSet"
    )
    assert (
        paths[f"{root}/{{set_id}}/versions/{{version}}/document"]["get"]["operationId"]
        == "getCalibrationVersionDocument"
    )
    associations = paths[f"{root}/{{set_id}}/versions/{{version}}/dataset-associations"]
    assert associations["get"]["operationId"] == "listCalibrationDatasetAssociations"
    assert associations["post"]["operationId"] == "associateCalibrationDatasetVersion"
    assert (
        paths[f"{root}/{{set_id}}/versions/{{version}}:validate"]["post"]["operationId"]
        == "validateCalibrationVersion"
    )
    preflight = paths[f"{root}/{{set_id}}/versions/{{version}}:preflight-publish"]["post"]
    publish = paths[f"{root}/{{set_id}}/versions/{{version}}:publish"]["post"]
    assert preflight["operationId"] == "preflightCalibrationPublish"
    assert publish["operationId"] == "publishCalibrationVersion"
    assert "501" not in preflight["responses"]
    assert "501" not in publish["responses"]
    assert preflight["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "CalibrationPublishPreflightEnvelope"
    )
    assert publish["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "CalibrationSetEnvelope"
    )

    migration = Path(__file__).parents[2] / "migrations/calibrations/0001_calibration_reads.sql"
    text = migration.read_text(encoding="utf-8")
    for marker in (
        "calibrations.calibration_sets",
        "robotics.robot_components",
        "core.apply_project_rls('calibrations.calibration_sets'::regclass)",
    ):
        assert marker in text
    publish_migration = (
        Path(__file__).parents[2] / "migrations/calibrations/0002_publish_preflights.sql"
    ).read_text(encoding="utf-8")
    for marker in (
        "calibrations.calibration_publish_preflights",
        "token_hash",
        "status IN ('ISSUED', 'CONSUMED', 'EXPIRED')",
        "core.apply_project_rls('calibrations.calibration_publish_preflights'::regclass)",
    ):
        assert marker in publish_migration
    document_migration = (
        Path(__file__).parents[2] / "migrations/calibrations/0003_version_documents.sql"
    ).read_text(encoding="utf-8")
    for marker in (
        "calibrations.calibration_version_documents",
        "calibrations.calibration_validation_reports",
        "calibrations.calibration_command_receipts",
        "core.apply_project_rls('calibrations.calibration_version_documents'::regclass)",
    ):
        assert marker in document_migration
    blockers_migration = (
        Path(__file__).parents[2] / "migrations/calibrations/0004_publish_preflight_blockers.sql"
    ).read_text(encoding="utf-8")
    assert "calibration_publish_preflights_blockers_array" in blockers_migration
    recalibration_migration = (
        Path(__file__).parents[2] / "migrations/calibrations/0005_recalibration_versions.sql"
    ).read_text(encoding="utf-8")
    assert "'RECALIBRATE'" in recalibration_migration
    association_migration = (
        Path(__file__).parents[2] / "migrations/calibrations/0006_dataset_version_associations.sql"
    ).read_text(encoding="utf-8")
    for marker in (
        "calibrations.calibration_dataset_version_associations",
        "dataset_registry.dataset_versions",
        "'ASSOCIATE_DATASET'",
        "core.apply_project_rls('calibrations.calibration_dataset_version_associations'::regclass)",
    ):
        assert marker in association_migration

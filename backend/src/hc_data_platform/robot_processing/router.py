from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict

from hc_data_platform.continuous_recordings.mcap_native import inspect, prepare
from hc_data_platform.core.context import select_organization_scope
from hc_data_platform.lerobot_imports.configuration import connections
from hc_data_platform.lerobot_imports.router import get_service
from hc_data_platform.lerobot_imports.source_browser import OriginalSourceBrowser
from hc_data_platform.robot_assets.router import get_organization_robot_asset_service
from hc_data_platform.robot_ingest.raw_recording import verify
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from . import service
from .models import Draft, Publish, Template, Validation

router = APIRouter(prefix="/api/v1", tags=["robot-processing-config"])
Assets = Annotated[object, Depends(get_organization_robot_asset_service)]
Native = Annotated[object, Depends(get_service)]
BASE = "/organizations/{organization_id}/robots/{robot_id}/processing-config"


def authorize(assets, auth, org, manage=False):
    assets._authorize(auth, org, "robot.manage" if manage else "robot.read")
    select_organization_scope(org)


@router.get(BASE, operation_id="getRobotProcessingConfig")
def get_config(organization_id: str, robot_id: str, auth: VerifiedAuth, assets: Assets):
    authorize(assets, auth, organization_id)
    with connections()() as db:
        return service.read(db, organization_id, robot_id)


@router.put(BASE + "/{scheme_id}", operation_id="saveRobotProcessingDraft")
def save_config(
    organization_id: str,
    robot_id: str,
    scheme_id: str,
    command: Draft,
    auth: VerifiedAuth,
    assets: Assets,
):
    authorize(assets, auth, organization_id, True)
    with connections()() as db:
        result = service.save(db, organization_id, robot_id, scheme_id, command, auth.subject_id)
        db.commit()
        return result


def sample(native, org, project, region, raw_id, source_path, callback):
    browser = OriginalSourceBrowser(native.storage, native.raw_sources)
    raw = browser.source(org, project, region, raw_id)
    manifest = browser.manifest(raw)
    file = next(
        (
            f
            for f in manifest["files"]
            if f["path"] == source_path and source_path.lower().endswith(".mcap")
        ),
        None,
    )
    if file is None:
        service.reject("MCAP_SAMPLE_NOT_FOUND", "所选原始 MCAP 文件不存在", 404)
    with TemporaryDirectory(prefix="robot-config-sample-") as temporary:
        source = Path(temporary) / "source.mcap"
        with source.open("wb") as output:
            verify(
                native.storage,
                file.get("object_key") or raw.storage_prefix + "/" + source_path,
                file["size"],
                file["sha256"],
                lambda: None,
                output,
            )
        return callback(
            source,
            {
                "raw_source_id": raw_id,
                "source_path": source_path,
                "sha256": file["sha256"],
                "project_id": project,
                "region_code": region,
            },
        )


@router.post(BASE + "/{scheme_id}:validate", operation_id="validateRobotProcessingDraft")
def validate_config(
    organization_id: str,
    robot_id: str,
    scheme_id: str,
    command: Validation,
    auth: VerifiedAuth,
    assets: Assets,
    native: Native,
):
    authorize(assets, auth, organization_id, True)
    authorize_scope(auth, command.project_id, "upload.read", command.region_code, organization_id)

    def validate_source(path, evidence):
        inventory = inspect(path)
        select_organization_scope(organization_id)
        with connections()() as db:
            current = service.current(
                db, organization_id, robot_id, scheme_id, command.expected_revision
            )
            if current is None:
                service.reject("ROBOT_CONFIG_NOT_FOUND", "采集方案不存在", 404)
            # Decode the whole sample, including channel shape changes and invalid pixels.
            try:
                service.validate_configuration(current[1]["configuration"], inventory)
                prepare(path, path.parent / "prepared", current[1]["configuration"])
            except ValueError as exc:
                evidence["decode_error"] = str(exc)
            result = service.validate(
                db,
                organization_id,
                robot_id,
                scheme_id,
                command.expected_revision,
                inventory,
                evidence,
                auth.subject_id,
            )
            db.commit()
            return result

    return sample(
        native,
        organization_id,
        command.project_id,
        command.region_code,
        command.raw_source_id,
        command.source_path,
        validate_source,
    )


@router.post(BASE + "/{scheme_id}:publish", operation_id="publishRobotProcessingConfig")
def publish_config(
    organization_id: str,
    robot_id: str,
    scheme_id: str,
    command: Publish,
    auth: VerifiedAuth,
    assets: Assets,
):
    authorize(assets, auth, organization_id, True)
    with connections()() as db:
        result = service.publish(db, organization_id, robot_id, scheme_id, command, auth.subject_id)
        db.commit()
        return result


TEMPLATES = "/organizations/{organization_id}/robot-processing-templates"


@router.get(TEMPLATES, operation_id="listRobotProcessingTemplates")
def templates(organization_id: str, auth: VerifiedAuth, assets: Assets):
    authorize(assets, auth, organization_id)
    with connections()() as db:
        return {
            "items": [
                r[0]
                for r in db.execute(
                    (
                        """SELECT document FROM ingest.robot_processing_templates WHERE
                        organization_id=%s ORDER BY template_id"""
                    ),
                    (organization_id,),
                ).fetchall()
            ]
        }


@router.post(TEMPLATES, operation_id="createRobotProcessingTemplate")
def create_template(organization_id: str, command: Template, auth: VerifiedAuth, assets: Assets):
    authorize(assets, auth, organization_id, True)
    import json

    with connections()() as db:
        document = command.model_dump(mode="json")
        model = db.execute(
            (
                "SELECT lifecycle FROM registry.robot_model_versions WHERE "
                "organization_id=%s AND version_id=%s"
            ),
            (organization_id, command.model_version_id),
        ).fetchone()
        if not model or model[0] != "PUBLISHED":
            service.reject("ROBOT_CONFIG_TEMPLATE_MODEL_INVALID", "模板须引用本组织已发布型号版本")
        old = db.execute(
            (
                "SELECT document FROM ingest.robot_processing_templates "
                "WHERE organization_id=%s AND template_id=%s"
            ),
            (organization_id, command.template_id),
        ).fetchone()
        if old and old[0] != document:
            service.reject("ROBOT_TEMPLATE_IMMUTABLE", "模板版本不可覆盖，请使用新模板版本标识")
        db.execute(
            (
                "INSERT INTO ingest.robot_processing_templates "
                "VALUES(%s,%s,%s::jsonb,now()) ON CONFLICT DO NOTHING"
            ),
            (organization_id, command.template_id, json.dumps(document)),
        )
        db.commit()
    return document


class TaskRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requires_rgb: bool
    allows_derived_action: bool = False


TASK_REQUIREMENTS = "/projects/{project_id}/collection-tasks/{task_id}/data-requirements"
OrgHeader = Annotated[str, Header(alias="X-Organization-Id")]


def task_exists(db, org, project, task):
    if (
        db.execute(
            (
                """SELECT 1 FROM collection_tasks.collection_tasks WHERE organization_id=%s AND
                project_id=%s AND collection_task_id=%s"""
            ),
            (org, project, task),
        ).fetchone()
        is None
    ):
        service.reject("COLLECTION_TASK_NOT_FOUND", "任务不存在", 404)


@router.get(TASK_REQUIREMENTS, operation_id="getTaskDataRequirements")
def task_requirements(
    project_id: str, task_id: str, auth: VerifiedAuth, organization_id: OrgHeader
):
    authorize_scope(auth, project_id, "upload.read", organization_id=organization_id)
    with connections()() as db:
        task_exists(db, organization_id, project_id, task_id)
        row = db.execute(
            (
                """SELECT requires_rgb,allows_derived_action FROM
                ingest.robot_task_data_requirements WHERE organization_id=%s AND project_id=%s
                AND collection_task_id=%s"""
            ),
            (organization_id, project_id, task_id),
        ).fetchone()
    return {
        "requires_rgb": bool(row and row[0]),
        "allows_derived_action": bool(row and row[1]),
        "declared": bool(row),
    }


@router.put(TASK_REQUIREMENTS, operation_id="setTaskDataRequirements")
def save_task_requirements(
    project_id: str,
    task_id: str,
    command: TaskRequirements,
    auth: VerifiedAuth,
    organization_id: OrgHeader,
):
    authorize_scope(auth, project_id, "upload.manage", organization_id=organization_id)
    with connections()() as db:
        task_exists(db, organization_id, project_id, task_id)
        db.execute(
            (
                """INSERT INTO ingest.robot_task_data_requirements
                VALUES(%s,%s,%s,%s,%s,%s,now()) ON
                CONFLICT(organization_id,project_id,collection_task_id) DO UPDATE SET
                requires_rgb=EXCLUDED.requires_rgb,
                allows_derived_action=EXCLUDED.allows_derived_action,actor=EXCLUDED.actor,updated_at=now()"""
            ),
            (
                organization_id,
                project_id,
                task_id,
                command.requires_rgb,
                command.allows_derived_action,
                auth.subject_id,
            ),
        )
        db.commit()
    return command

"""Shared assets use organization permissions; tasks and samples retain project scope."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Query

from hc_data_platform.core.context import select_organization_scope
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from . import associations, plans, schemes
from .common import digest, reject
from .components import CaptureServices
from .dependencies import Services, require_services
from .models import (
    AddParticipants,
    AssociateScheme,
    DeviceMapping,
    Identifier,
    Revision,
    SchemeDraft,
    SetTaskPlan,
    SourceFormat,
    ValidateDraft,
    ValidateMapping,
    VersionReference,
)
from .responses import (
    CaptureAssociationRemoved,
    CaptureSchemeDetail,
    CaptureSchemeLinks,
    CaptureSchemeList,
    CaptureSchemeReferences,
    CaptureSchemeVersion,
    CaptureTaskPlanEnvelope,
)

router = APIRouter(prefix="/api/v1", tags=["capture-schemes"])
Organization = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
Region = Annotated[str, Header(alias="X-Region-Code", min_length=1, max_length=128)]
BASE = "/organizations/{organization_id}/capture-schemes"
LINKS = "/organizations/{organization_id}/robots/{robot_id}/capture-schemes"
PLAN = "/projects/{project_id}/collection-tasks/{task_id}/capture-plan"


def authorize(
    available: CaptureServices | None, auth: AuthContext, org: str, *, manage: bool = False
) -> CaptureServices:
    if not auth.is_platform_admin and org not in auth.organization_ids:
        reject("ORGANIZATION_SCOPE_DENIED", "当前身份无权访问该组织。", 403)
    auth.require_organization_capability(org, "robot.manage" if manage else "robot.read")
    select_organization_scope(org)
    services = require_services(available)
    if auth.is_platform_admin and not services.organization_exists(org):
        reject("ORGANIZATION_NOT_FOUND", "组织不存在。", 404)
    return services


@router.get(
    BASE,
    operation_id="listCaptureSchemes",
    response_model=CaptureSchemeList,
    response_model_exclude_unset=True,
)
def list_schemes(
    organization_id: Identifier,
    auth: VerifiedAuth,
    services: Services,
    q: Annotated[str, Query(max_length=200)] = "",
    source_format: SourceFormat | None = None,
    include_archived: bool = False,
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id)
    with services.connections() as db:
        return {
            "items": schemes.list_schemes(
                db,
                organization_id,
                query=q,
                source_format=source_format,
                include_archived=include_archived,
            )
        }


@router.get(
    BASE + "/{scheme_id}",
    operation_id="getCaptureScheme",
    response_model=CaptureSchemeDetail,
    response_model_exclude_unset=True,
)
def get_scheme(
    organization_id: Identifier, scheme_id: Identifier, auth: VerifiedAuth, services: Services
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id)
    with services.connections() as db:
        return schemes.detail(db, organization_id, scheme_id)


@router.get(
    BASE + "/{scheme_id}/versions/{version}",
    operation_id="getCaptureSchemeVersion",
    response_model=CaptureSchemeVersion,
    response_model_exclude_unset=True,
)
def get_version(
    organization_id: Identifier,
    scheme_id: Identifier,
    version: Annotated[int, Path(ge=1)],
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id)
    with services.connections() as db:
        return schemes.published_version(db, organization_id, scheme_id, version)


@router.put(
    BASE + "/{scheme_id}",
    operation_id="saveCaptureSchemeDraft",
    response_model=CaptureSchemeDetail,
    response_model_exclude_unset=True,
)
def save_scheme(
    organization_id: Identifier,
    scheme_id: Identifier,
    command: SchemeDraft,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id, manage=True)
    with services.connections() as db:
        return schemes.save(db, organization_id, scheme_id, command, auth.subject_id)


@router.post(
    BASE + "/{scheme_id}:validate",
    operation_id="validateCaptureScheme",
    response_model=CaptureSchemeDetail,
    response_model_exclude_unset=True,
)
def validate_scheme(
    organization_id: Identifier,
    scheme_id: Identifier,
    command: ValidateDraft,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id, manage=True)
    with services.connections() as db:
        current = schemes.detail(db, organization_id, scheme_id)
    authorize_scope(
        auth, command.sample.project_id, "upload.read", command.sample.region_code, organization_id
    )
    evidence = services.samples.validate(organization_id, current["draft"], command.sample)
    select_organization_scope(organization_id)
    with services.connections() as db:
        return schemes.record_validation(
            db, organization_id, scheme_id, command.expected_revision, evidence, auth.subject_id
        )


@router.post(
    BASE + "/{scheme_id}:publish",
    operation_id="publishCaptureScheme",
    response_model=CaptureSchemeVersion,
    response_model_exclude_unset=True,
)
def publish_scheme(
    organization_id: Identifier,
    scheme_id: Identifier,
    command: Revision,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id, manage=True)
    with services.connections() as db:
        return schemes.publish(
            db, organization_id, scheme_id, command.expected_revision, auth.subject_id
        )


@router.post(
    BASE + "/{scheme_id}:archive",
    operation_id="archiveCaptureScheme",
    response_model=CaptureSchemeDetail,
    response_model_exclude_unset=True,
)
def archive_scheme(
    organization_id: Identifier,
    scheme_id: Identifier,
    command: Revision,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id, manage=True)
    with services.connections() as db:
        return schemes.archive(
            db, organization_id, scheme_id, command.expected_revision, auth.subject_id
        )


@router.get(
    BASE + "/{scheme_id}/references",
    operation_id="getCaptureSchemeReferences",
    response_model=CaptureSchemeReferences,
    response_model_exclude_unset=True,
)
def get_references(
    organization_id: Identifier,
    scheme_id: Identifier,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id)
    with services.connections() as db:
        return schemes.references(db, organization_id, scheme_id)


@router.get(
    LINKS,
    operation_id="listRobotCaptureSchemes",
    response_model=CaptureSchemeLinks,
    response_model_exclude_unset=True,
)
def get_links(
    organization_id: Identifier,
    robot_id: Identifier,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id)
    with services.connections() as db:
        return {"items": associations.list_links(db, organization_id, robot_id)}


@router.put(
    LINKS,
    operation_id="associateRobotCaptureScheme",
    response_model=CaptureSchemeLinks,
    response_model_exclude_unset=True,
)
def associate(
    organization_id: Identifier,
    robot_id: Identifier,
    command: AssociateScheme,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id, manage=True)
    with services.connections() as db:
        return {
            "items": associations.associate(db, organization_id, robot_id, command, auth.subject_id)
        }


@router.post(
    LINKS + "/{scheme_id}:validate",
    operation_id="validateRobotCaptureMapping",
    response_model=CaptureSchemeLinks,
    response_model_exclude_unset=True,
)
def validate_mapping(
    organization_id: Identifier,
    robot_id: Identifier,
    scheme_id: Identifier,
    command: ValidateMapping,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    services = authorize(services, auth, organization_id, manage=True)
    with services.connections() as db:
        mapping = associations.mapping_version(
            db, organization_id, robot_id, scheme_id, command.mapping_version
        )
        document = schemes.version(
            db, organization_id, VersionReference.model_validate(mapping["scheme"])
        )
    authorize_scope(
        auth, command.sample.project_id, "upload.read", command.sample.region_code, organization_id
    )
    evidence = services.samples.validate(
        organization_id,
        document,
        command.sample,
        DeviceMapping.model_validate(mapping["mapping"]),
    )
    evidence["mapping_sha256"] = mapping["content_sha256"]
    evidence["scheme_sha256"] = digest(document)
    select_organization_scope(organization_id)
    with services.connections() as db:
        return {
            "items": associations.record_validation(
                db,
                organization_id,
                robot_id,
                scheme_id,
                command.mapping_version,
                evidence,
                auth.subject_id,
            )
        }


@router.post(
    LINKS + "/{scheme_id}:remove",
    operation_id="removeRobotCaptureScheme",
    response_model=CaptureAssociationRemoved,
    response_model_exclude_unset=True,
)
def remove_link(
    organization_id: Identifier,
    robot_id: Identifier,
    scheme_id: Identifier,
    command: Revision,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, bool]:
    services = authorize(services, auth, organization_id, manage=True)
    with services.connections() as db:
        associations.remove(
            db, organization_id, robot_id, scheme_id, command.expected_revision, auth.subject_id
        )
    return {"removed": True}


@router.get(
    PLAN,
    operation_id="getTaskCapturePlan",
    response_model=CaptureTaskPlanEnvelope,
    response_model_exclude_unset=True,
)
def get_plan(
    project_id: Identifier,
    task_id: Identifier,
    organization_id: Organization,
    region_code: Region,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    services = require_services(services)
    with services.connections() as db:
        plan = plans.get_plan(db, (organization_id, project_id, region_code), task_id)
        return {"plan": plan}


@router.put(
    PLAN,
    operation_id="setTaskCapturePlan",
    response_model=CaptureTaskPlanEnvelope,
    response_model_exclude_unset=True,
)
def set_plan(
    project_id: Identifier,
    task_id: Identifier,
    command: SetTaskPlan,
    organization_id: Organization,
    region_code: Region,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    authorize_scope(auth, project_id, "upload.manage", region_code, organization_id)
    services = require_services(services)
    with services.connections() as db:
        return {
            "plan": plans.save_plan(
                db, (organization_id, project_id, region_code), task_id, command, auth.subject_id
            )
        }


@router.post(
    PLAN + ":add-participants",
    operation_id="addTaskCaptureParticipants",
    response_model=CaptureTaskPlanEnvelope,
    response_model_exclude_unset=True,
)
def add_task_participants(
    project_id: Identifier,
    task_id: Identifier,
    command: AddParticipants,
    organization_id: Organization,
    region_code: Region,
    auth: VerifiedAuth,
    services: Services,
) -> dict[str, Any]:
    authorize_scope(auth, project_id, "upload.manage", region_code, organization_id)
    services = require_services(services)
    with services.connections() as db:
        return {
            "plan": plans.add_participants(
                db, (organization_id, project_id, region_code), task_id, command, auth.subject_id
            )
        }

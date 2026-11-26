"""Project model settings, reusable instructions and Episode pre-annotation."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.episode_store.dependencies import PackageServices
from hc_data_platform.episode_store.preview_access import authorize_episode_preview
from hc_data_platform.security.http import VerifiedAuth
from hc_data_platform.security.scope import ScopeGuard

from .model_episodes import ModelEpisode, model_episodes
from .model_inference import completion, failure, predict
from .model_settings import ConnectionUpdate, Guide, ModelConnectionStatus, ProjectModelSettings
from .service import AnnotationService

router = APIRouter(prefix="/projects/{project_id}/regions/{region_code}/model-annotation")
Organization = Annotated[str, Header(alias="X-Organization-ID", min_length=1)]


async def settings(
    project_id: str,
    region_code: str,
    organization: Organization,
    auth: VerifiedAuth,
    request: Request,
    response: Response,
    packages: PackageServices,
):
    ScopeGuard.require(auth, project_id, region_code, organization)
    select_request_scope(project_id, region_code, organization_id=organization)
    response.headers["Cache-Control"] = "private, no-store"
    if packages is None:
        raise failure(503, "EPISODE_PACKAGES_UNAVAILABLE", "Episode 服务尚未配置")
    return ProjectModelSettings(
        packages.repository,
        organization,
        project_id,
        request.app.state.settings.data_source_credential_key.get_secret_value(),
    )


Settings = Annotated[ProjectModelSettings, Depends(settings)]


@router.get("/connection", response_model=ModelConnectionStatus)
def model_connection(store: Settings, auth: VerifiedAuth):
    auth.require_exact_platform_capability("platform.admin")
    return store.connection()


@router.put("/connection", response_model=ModelConnectionStatus)
def save_model_connection(body: ConnectionUpdate, store: Settings, auth: VerifiedAuth):
    auth.require_exact_platform_capability("platform.admin")
    return store.save_connection(body)


@router.post("/connection/test")
def test_model_connection(store: Settings, auth: VerifiedAuth):
    auth.require_exact_platform_capability("platform.admin")
    config, key = store.credentials()
    completion(config, key, [{"role": "user", "content": "Reply OK"}])
    return {"model": config.model}


@router.get("/episodes", response_model=list[ModelEpisode])
def list_model_episodes(
    project_id: str,
    region_code: str,
    organization: Organization,
    store: Settings,
    auth: VerifiedAuth,
):
    auth.require_capability("annotation_task.read", project_id, organization)
    return model_episodes(store.db, organization, project_id, region_code)


@router.get("/guides", response_model=list[Guide])
def model_guides(project_id: str, organization: Organization, store: Settings, auth: VerifiedAuth):
    auth.require_capability("annotation_task.read", project_id, organization)
    return store.guides()


@router.put("/guides", response_model=Guide)
def save_model_guide(
    project_id: str, organization: Organization, body: Guide, store: Settings, auth: VerifiedAuth
):
    auth.require_capability("annotation.save", project_id, organization)
    return store.save_guide(body)


class InferenceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    guide: str = Field(min_length=1, max_length=128)
    guide_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    mutation_id: UUID


class InferenceResult(BaseModel):
    task_id: str
    revision: int
    added: int


def annotation_service():
    from .router import get_annotation_service

    return get_annotation_service()


@router.post("/tasks/{task_id}/run", response_model=InferenceResult)
def run_model_annotation(
    project_id: str,
    region_code: str,
    organization: Organization,
    task_id: str,
    body: InferenceCommand,
    store: Settings,
    auth: VerifiedAuth,
    packages: PackageServices,
    service: Annotated[AnnotationService, Depends(annotation_service)],
    if_match: str = Header(alias="If-Match"),
):
    auth.require_capability("annotation.save", project_id, organization)
    task = service.read_task(task_id, auth)
    ref = task.data_ref
    if ref is None or (task.project_id, task.region_code, ref.identity.organization_id) != (
        project_id,
        region_code,
        organization,
    ):
        raise failure(404, "MODEL_EPISODE_UNAVAILABLE", "选中的标注任务没有可用的 Episode")
    draft = service.get_draft(task_id, auth)
    if draft.revision != body.expected_revision or draft.etag != if_match:
        raise failure(409, "MODEL_DRAFT_CHANGED", "标注草稿已更新，请刷新后重试")
    if task.status.value not in {"DRAFT", "NEEDS_REVISION"}:
        raise failure(409, "MODEL_TASK_NOT_EDITABLE", "仅可对待标注或退回的 Episode 进行预标注")
    if not task.allowed_tags:
        raise failure(422, "MODEL_VOCABULARY_EMPTY", "请先为该采集任务的方案选择标签")
    authorize_episode_preview(
        packages,
        auth,
        organization,
        project_id,
        region_code,
        task.dataset_id,
        episode_id=ref.identity.episode_id,
        version_id=ref.data_version_id,
    )
    guide = store.get_guide(body.guide, body.guide_revision)
    config, key = store.credentials()
    package = packages.files.open(ref.identity, ref.data_version_id)
    if package.receipt.data != ref:
        raise failure(409, "MODEL_EPISODE_CHANGED", "Episode 版本与标注任务不一致")
    schema = service.get_bound_tag_schema(task_id, auth)
    predictions = predict(config, key, package, task.allowed_tags, guide, schema)
    names = {node.tag_id: node.display_name for node in schema.document.nodes}
    seen = {
        (tag.label or names.get(tag.tag_id), tag.start_step, tag.end_step) for tag in draft.tags
    }
    added = []
    for tag in predictions:
        interval = (tag.label, tag.start_step, tag.end_step)
        if interval not in seen:
            added.append(tag)
            seen.add(interval)
    revision = draft.revision
    if added:
        saved = service.save_draft(
            task_id,
            auth,
            (),
            tags=(*draft.tags, *added),
            expected_revision=draft.revision,
            if_match=if_match,
            client_mutation_id=f"model-annotation:{body.mutation_id}",
        )
        revision = saved.revision
    return InferenceResult(task_id=task_id, revision=revision, added=len(added))

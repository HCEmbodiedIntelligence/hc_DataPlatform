from typing import Annotated, TypeVar
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import ValidationError

from hc_data_platform.core.config import Settings
from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .models import (
    TrainingConfigs,
    TrainingId,
    TrainingOptions,
    TrainingRun,
    TrainingRunPage,
    TrainingStart,
    TrainingWire,
)

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}/model-training",
    tags=["model-training"],
)
Wire = TypeVar("Wire", bound=TrainingWire)

UPSTREAM_ERRORS = {
    404: ("TRAINING_OPTION_NOT_FOUND", "所选资源已不存在，请重新选择。"),
    409: ("TRAINING_CONFLICT", "资源状态或请求标识冲突，请刷新选项并检查任务列表。"),
    422: ("TRAINING_INVALID_SELECTION", "所选数据、模型、训练卡或参数配置无效。"),
    429: ("TRAINING_RATE_LIMITED", "训练服务繁忙，请稍后重试。"),
    503: ("TRAINING_UNAVAILABLE", "训练服务暂时不可用。"),
}


class TrainingGateway:
    def __init__(self, settings: Settings, headers: dict[str, str]):
        self.settings = settings
        self.headers = headers

    async def call(
        self,
        resource: str,
        schema: type[Wire],
        *,
        query: dict[str, str | int] | None = None,
        command: TrainingStart | None = None,
        key: UUID | None = None,
    ) -> Wire:
        settings = self.settings
        if not settings.training_service_url or not settings.training_service_token:
            raise problem(
                status=503,
                code="TRAINING_NOT_CONFIGURED",
                title="训练服务尚未配置",
                detail="请配置训练服务地址和服务凭证。",
            )
        headers = {
            **self.headers,
            "Authorization": "Bearer " + settings.training_service_token.get_secret_value(),
        }
        if key is not None:
            headers["Idempotency-Key"] = str(key)
        url = str(settings.training_service_url).rstrip("/") + "/v1/training/" + resource
        try:
            async with httpx.AsyncClient(
                timeout=settings.training_service_timeout_seconds, trust_env=False
            ) as client:
                if command is None:
                    response = await client.get(url, headers=headers, params=query)
                else:
                    response = await client.post(url, headers=headers, json=command.model_dump())
        except httpx.TimeoutException as exc:
            raise problem(
                status=504,
                code="TRAINING_TIMEOUT",
                title="训练服务响应超时",
                detail="请刷新任务状态或重试同一请求。",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise problem(
                status=503,
                code="TRAINING_UNAVAILABLE",
                title="训练服务连接失败",
                detail="暂时无法连接训练服务。",
                retryable=True,
            ) from exc

        if response.status_code in UPSTREAM_ERRORS:
            code, detail = UPSTREAM_ERRORS[response.status_code]
            raise problem(
                status=response.status_code,
                code=code,
                title=detail,
                detail=detail,
                retryable=response.status_code in (429, 503),
            )
        if response.status_code != (202 if command else 200):
            raise problem(
                status=502,
                code="TRAINING_BAD_RESPONSE",
                title="训练服务响应异常",
                detail="训练服务返回了非预期的 HTTP 状态。",
            )
        try:
            return schema.model_validate_json(response.content)
        except ValidationError as exc:
            raise problem(
                status=502,
                code="TRAINING_BAD_RESPONSE",
                title="训练服务响应异常",
                detail="训练服务响应与 v1 接口定义不符。",
            ) from exc


def training_gateway(
    request: Request,
    project_id: str,
    region_code: str,
    auth: VerifiedAuth,
    organization_id: Annotated[
        str, Header(alias="X-Organization-Id", min_length=1, max_length=128)
    ],
) -> TrainingGateway:
    authorize_scope(auth, project_id, "training.read", region_code, organization_id)
    if request.method == "POST":
        auth.require_capability("training.create", project_id, organization_id)
    return TrainingGateway(
        request.app.state.settings,
        {
            "X-Organization-Id": organization_id,
            "X-Project-Id": project_id,
            "X-Region-Code": region_code,
        },
    )


Gateway = Annotated[TrainingGateway, Depends(training_gateway)]


@router.get("/datasets", operation_id="listTrainingDatasets")
async def datasets(gateway: Gateway) -> TrainingOptions:
    return await gateway.call("datasets", TrainingOptions)


@router.get("/models", operation_id="listTrainingModels")
async def models(dataset_id: TrainingId, gateway: Gateway) -> TrainingOptions:
    return await gateway.call("models", TrainingOptions, query={"dataset_id": dataset_id})


@router.get("/cards", operation_id="listTrainingCards")
async def cards(dataset_id: TrainingId, model_id: TrainingId, gateway: Gateway) -> TrainingOptions:
    return await gateway.call(
        "cards", TrainingOptions, query={"dataset_id": dataset_id, "model_id": model_id}
    )


@router.get("/configs", operation_id="listTrainingConfigs")
async def configs(
    dataset_id: TrainingId, model_id: TrainingId, card_id: TrainingId, gateway: Gateway
) -> TrainingConfigs:
    return await gateway.call(
        "configs",
        TrainingConfigs,
        query={"dataset_id": dataset_id, "model_id": model_id, "card_id": card_id},
    )


@router.post("/runs", status_code=202, operation_id="startTrainingRun")
async def start(
    command: TrainingStart, gateway: Gateway, key: Annotated[UUID, Header(alias="Idempotency-Key")]
) -> TrainingRun:
    return await gateway.call("runs", TrainingRun, command=command, key=key)


@router.get("/runs", operation_id="listTrainingRuns")
async def runs(
    gateway: Gateway,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TrainingRunPage:
    return await gateway.call("runs", TrainingRunPage, query={"page": page, "page_size": page_size})

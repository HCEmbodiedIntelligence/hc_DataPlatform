"""Explicit browser configuration of labels before a native G1 import."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from hc_data_platform.annotation.models import TagSchemaDocument, TagSchemaTarget
from hc_data_platform.annotation.router import get_annotation_service
from hc_data_platform.core.config import get_settings
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.errors import problem
from hc_data_platform.security.auth import AuthContext


class NativeConfiguration(BaseModel):
    configured: bool
    schema_id: str | None = None
    schema_snapshot_id: str | None = None


def connections() -> Callable[[], Any]:
    return psycopg_connection_factory(get_settings().postgres_dsn)


def processing_configuration(
    project_id: str, region_code: str, dataset_id: str
) -> NativeConfiguration:
    with connections()() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT DISTINCT binding.schema_id, binding.dataset_schema_snapshot_id
            FROM annotation.tag_schema_bindings binding
            WHERE binding.project_id=%s AND binding.region_code=%s AND binding.dataset_id=%s
              AND binding.task_kind='TAGGING'""",
            (project_id, region_code, dataset_id),
        )
        rows = cursor.fetchall()
    return NativeConfiguration(
        configured=len(rows) == 1,
        schema_id=str(rows[0][0]) if len(rows) == 1 else None,
        schema_snapshot_id=str(rows[0][1]) if len(rows) == 1 else None,
    )


def validate_target(organization_id: str, project_id: str, region_code: str, manifest: Any) -> None:
    if manifest.processing_mode == "STORE_ONLY":
        with connections()() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT 1 FROM dataset_registry.datasets
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
                (organization_id, project_id, region_code, manifest.dataset_id),
            )
            if cursor.fetchone() is None:
                raise problem(
                    status=404,
                    code="DATASET_NOT_FOUND",
                    title="目标数据集不存在",
                    detail="请先在当前项目和区域创建或选择一个数据集。",
                )
        return
    with connections()() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT dataset_id, status FROM collection_tasks.collection_tasks
            WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
            (organization_id, project_id, manifest.collection_task_id),
        )
        row = cursor.fetchone()
        cursor.execute(
            """SELECT 1 FROM ingest.data_sources WHERE organization_id=%s AND project_id=%s
            AND region_code=%s AND source_type='ROBOT' AND administrative_state='ENABLED'
            AND source_document->'binding'->>'robot_id'=%s LIMIT 1""",
            (organization_id, project_id, region_code, manifest.robot_id),
        )
        robot = cursor.fetchone()
    if row is None or str(row[0]) != manifest.dataset_id or row[1] != "ACTIVE":
        raise problem(
            status=409,
            code="LEROBOT_TARGET_INVALID",
            title="LeRobot target unavailable",
            detail="请选择当前项目中与目标数据集关联的 ACTIVE 采集任务。",
        )
    if robot is None:
        raise problem(
            status=409,
            code="LEROBOT_ROBOT_UNAVAILABLE",
            title="机器人不可用",
            detail="请选择当前项目和区域中已启用的机器人数据源。",
        )
    if not processing_configuration(project_id, region_code, manifest.dataset_id).configured:
        raise problem(
            status=409,
            code="LEROBOT_PROCESSING_PLAN_REQUIRED",
            title="G1 标注规则未配置",
            detail="请先在上传确认窗口配置并发布 G1 标注规则，再开始上传。",
        )


def configure_labels(
    auth: AuthContext,
    organization_id: str,
    project_id: str,
    region_code: str,
    dataset_id: str,
    labels: list[str],
) -> NativeConfiguration:
    labels = [label.strip() for label in labels]
    if (
        not labels
        or len(labels) > 50
        or any(not value or len(value) > 100 for value in labels)
        or len(set(labels)) != len(labels)
    ):
        raise problem(
            status=422,
            code="LEROBOT_LABELS_INVALID",
            title="标注标签无效",
            detail="请输入 1–50 个不重复的标签，每个标签不超过 100 个字符。",
        )
    with connections()() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT 1 FROM collection_tasks.collection_tasks
            WHERE organization_id=%s AND project_id=%s AND dataset_id=%s
            AND status='ACTIVE' LIMIT 1""",
            (organization_id, project_id, dataset_id),
        )
        if cursor.fetchone() is None:
            raise problem(
                status=404,
                code="DATASET_NOT_FOUND",
                title="Dataset not found",
                detail="目标数据集没有当前项目的有效采集任务。",
            )
    existing = processing_configuration(project_id, region_code, dataset_id)
    if existing.configured:
        return existing
    identity = hashlib.sha256(
        f"{organization_id}/{project_id}/{region_code}/{dataset_id}".encode()
    ).hexdigest()[:24]
    schema_id, snapshot_id = f"native-g1-labels-{identity}", "native-g1-v3-30hz-v1"
    service = get_annotation_service()
    versions = service.list_tag_schema_versions(
        project_id=project_id, schema_id=schema_id, actor=auth
    )
    if not versions:
        service.create_tag_schema_version(
            project_id=project_id,
            schema_id=schema_id,
            version=1,
            name="G1 原生数据人工标注",
            actor=auth,
            document=TagSchemaDocument(
                nodes=tuple(
                    {"tag_id": f"label-{i + 1}", "code": f"label-{i + 1}", "display_name": name}
                    for i, name in enumerate(labels)
                )
            ),
            compatible_targets=(
                TagSchemaTarget(
                    region_code=region_code,
                    dataset_id=dataset_id,
                    dataset_schema_snapshot_id=snapshot_id,
                    task_kind="TAGGING",
                ),
            ),
        )
    service.publish_tag_schema_version(
        project_id=project_id, schema_id=schema_id, version=1, actor=auth
    )
    return processing_configuration(project_id, region_code, dataset_id)

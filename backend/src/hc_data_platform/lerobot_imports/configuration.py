"""Optional label configuration bound to an independently frozen data schema."""

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

from .robot_eligibility import import_robots


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
              AND binding.task_kind='TAGGING'
              AND binding.dataset_schema_snapshot_id=COALESCE(
                (SELECT schema_snapshot_id FROM episode_store.dataset_schemas s
                 WHERE s.organization_id=binding.organization_id
                   AND s.project_id=binding.project_id AND s.region_code=binding.region_code
                   AND s.dataset_id=binding.dataset_id),
                (SELECT 'capture-'||contract_sha256 FROM episode_store.dataset_capture_contracts c
                 WHERE c.organization_id=binding.organization_id
                   AND c.project_id=binding.project_id AND c.region_code=binding.region_code
                   AND c.dataset_id=binding.dataset_id), 'native-lerobot-v3-v1')""",
            (project_id, region_code, dataset_id),
        )
        rows = cursor.fetchall()
    return NativeConfiguration(
        configured=len(rows) == 1,
        schema_id=str(rows[0][0]) if len(rows) == 1 else None,
        schema_snapshot_id=str(rows[0][1]) if len(rows) == 1 else None,
    )


def validate_target(
    organization_id: str,
    project_id: str,
    region_code: str,
    manifest: Any,
    *,
    connection_factory: Callable[[], Any] | None = None,
) -> None:
    factory = connection_factory or connections()
    if manifest.processing_mode == "STORE_ONLY":
        with factory() as connection, connection.cursor() as cursor:
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
    with factory() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT t.dataset_id, t.status FROM collection_tasks.collection_tasks t
            JOIN dataset_registry.datasets d ON d.organization_id=t.organization_id
              AND d.project_id=t.project_id AND d.dataset_id=t.dataset_id
            WHERE t.organization_id=%s AND t.project_id=%s AND t.collection_task_id=%s
              AND d.region_code=%s AND t.upload_region_code=%s""",
            (organization_id, project_id, manifest.collection_task_id, region_code, region_code),
        )
        row = cursor.fetchone()
        robot = import_robots(
            connection, organization_id, project_id, region_code, manifest.robot_id
        )
    if row is None or str(row[0]) != manifest.dataset_id or row[1] != "ACTIVE":
        raise problem(
            status=409,
            code="LEROBOT_TARGET_INVALID",
            title="LeRobot target unavailable",
            detail="请选择当前项目中与目标数据集关联的 ACTIVE 采集任务。",
        )
    if not robot:
        raise problem(
            status=409,
            code="LEROBOT_ROBOT_UNAVAILABLE",
            title="机器人不可用",
            detail="请选择当前组织的 ACTIVE 机器人，并确认项目使用资格未被停用。",
        )
    # Raw intake does not depend on annotation rules. Processing separately
    # requires the server-owned recording snapshot frozen by the upload owner.


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
        cursor.execute(
            """SELECT schema_snapshot_id FROM episode_store.dataset_schemas
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
            (organization_id, project_id, region_code, dataset_id),
        )
        stored = cursor.fetchone()
        if stored:
            snapshot_id = str(stored[0])
        else:
            cursor.execute(
                """SELECT contract_sha256 FROM episode_store.dataset_capture_contracts
                WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
                (organization_id, project_id, region_code, dataset_id),
            )
            frozen = cursor.fetchone()
            snapshot_id = "capture-" + frozen[0] if frozen else "native-lerobot-v3-v1"
    existing = processing_configuration(project_id, region_code, dataset_id)
    if existing.configured and existing.schema_snapshot_id == snapshot_id:
        return existing
    identity = hashlib.sha256(
        f"{organization_id}/{project_id}/{region_code}/{dataset_id}/{snapshot_id}".encode()
    ).hexdigest()[:24]
    schema_id = f"native-lerobot-labels-{identity}"
    service = get_annotation_service()
    versions = service.list_tag_schema_versions(
        project_id=project_id, schema_id=schema_id, actor=auth
    )
    if not versions:
        service.create_tag_schema_version(
            project_id=project_id,
            schema_id=schema_id,
            version=1,
            name="LeRobot 原生数据人工标注",
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

"""Active association evidence; immutable capture provenance is retained separately."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from hc_data_platform.security.auth import AuthContext

Kind = Literal["raw", "upload", "configuration", "component", "calibration", "source"]


class DeletionBlocker(BaseModel):
    kind: Kind
    label: str
    count: int
    resolution: str


class DeletionReference(BaseModel):
    kind: Kind
    id: str
    name: str
    status: str | None = None
    project_id: str | None = None
    region_code: str | None = None
    collection_task_id: str | None = None
    task_code: str | None = None
    task_name: str | None = None
    raw_source_id: str | None = None
    dataset_id: str | None = None
    scheme_id: str | None = None
    version: int | None = None
    origin_robot_id: str | None = None


class RobotDeletionCheck(BaseModel):
    organization_id: str
    robot_id: str
    can_delete: bool
    blockers: list[DeletionBlocker] = Field(default_factory=list)
    items: list[DeletionReference] = Field(default_factory=list)
    total: int = 0
    restricted_count: int = 0
    offset: int = 0
    limit: int = 20


@dataclass(frozen=True)
class Reference:
    kind: Kind
    label: str
    table: str
    condition: str
    projection: str
    resolution: str
    capability: str | None = None
    joins: str = ""


TASK_RESOLUTION = "使用处理操作中的按钮建立目标实例的新关联，或直接删除选定任务及全部数据。"

REFERENCES = (
    Reference(
        "raw",
        "原始数据",
        "ingest.raw_sources",
        "robotics.robot_reference_owner(r.organization_id,'raw',r.project_id,r.region_code,"
        "r.raw_source_id,r.robot_id)=%s",
        """r.raw_source_id AS id,
        COALESCE(u.upload_document #>> '{manifest,assets,0,path}',r.raw_source_id) AS name,
        r.processing_status AS status, r.project_id,r.region_code,r.collection_task_id,
        r.raw_source_id,r.dataset_id,NULL::text AS scheme_id,NULL::integer AS version""",
        TASK_RESOLUTION + "未关联采集任务的旧数据需由数据管理员核对归属和保留要求。",
        "upload.read",
        """LEFT JOIN ingest.robot_ingest_uploads u ON u.organization_id=r.organization_id
        AND u.project_id=r.project_id AND u.region_code=r.region_code
        AND u.upload_id=r.upload_id""",
    ),
    Reference(
        "upload",
        "上传记录",
        "ingest.robot_ingest_uploads",
        "robotics.robot_reference_owner(r.organization_id,'upload',r.project_id,r.region_code,"
        "r.upload_id,r.authenticated_robot_id)=%s",
        """r.upload_id AS id,
        COALESCE(r.upload_document #>> '{manifest,assets,0,path}',r.upload_id) AS name,
        r.state AS status,r.project_id,r.region_code,r.collection_task_id,
        r.raw_source_id,r.dataset_id,NULL::text AS scheme_id,NULL::integer AS version""",
        TASK_RESOLUTION,
        "upload.read",
    ),
    Reference(
        "configuration",
        "已发布采集配置",
        "ingest.robot_processing_versions",
        "robotics.robot_reference_owner(r.organization_id,'configuration',NULL,NULL,"
        "r.scheme_id || ':v' || r.version,r.robot_id)=%s",
        """r.scheme_id || ':v' || r.version AS id,
        COALESCE(r.document->>'name',r.scheme_id) AS name,'PUBLISHED'::text AS status,
        NULL::text AS project_id,NULL::text AS region_code,NULL::text AS collection_task_id,
        NULL::text AS raw_source_id,NULL::text AS dataset_id,r.scheme_id,r.version""",
        "可建立目标实例的新配置关联；数据清空后可清理当前关联。历史版本不可删除，保留追溯。",
    ),
    Reference(
        "component",
        "拓扑组件",
        "robotics.robot_asset_components",
        "robotics.robot_reference_owner(r.organization_id,'component',NULL,NULL,"
        "r.component_id,r.robot_id)=%s",
        """r.component_id AS id,r.display_name AS name,r.lifecycle_status AS status,
        NULL::text AS project_id,NULL::text AS region_code,NULL::text AS collection_task_id,
        NULL::text AS raw_source_id,NULL::text AS dataset_id,
        NULL::text AS scheme_id,NULL::integer AS version""",
        "转移操作会为目标实例建立历史拓扑关联，保留原始组件记录。",
    ),
    Reference(
        "calibration",
        "标定记录",
        "calibrations.calibration_sets",
        "robotics.robot_reference_owner(r.organization_id,'calibration',r.project_id,r.region_code,"
        "r.set_id,r.robot_instance_id)=%s",
        """r.set_id AS id,r.set_id AS name,r.snapshot_status AS status,
        r.project_id,r.region_code,NULL::text AS collection_task_id,
        NULL::text AS raw_source_id,NULL::text AS dataset_id,NULL::text AS scheme_id,r.version""",
        "转移操作会建立目标实例的历史标定关联，保留原始标定版本。",
        "calibration.read",
    ),
    Reference(
        "source",
        "数据源",
        "ingest.data_sources",
        "r.source_type='ROBOT' AND robotics.robot_reference_owner(r.organization_id,'source',"
        "r.project_id,r.region_code,r.source_id,r.source_document #>> '{binding,robot_id}')=%s",
        """r.source_id AS id,r.name,r.administrative_state AS status,
        r.project_id,r.region_code,NULL::text AS collection_task_id,
        NULL::text AS raw_source_id,NULL::text AS dataset_id,
        NULL::text AS scheme_id,NULL::integer AS version""",
        "前往数据源详情核对机器人绑定。不再使用的数据源可在该页单独删除；"
        "如删除仍受依赖限制，按数据源提示处理。仅停用数据源不会解除绑定。",
        "ingest_source.read",
    ),
)


def deletion_blockers(cursor, organization_id: str, robot_id: str) -> list[DeletionBlocker]:
    result = []
    for ref in REFERENCES:
        cursor.execute(
            f"SELECT count(*) FROM {ref.table} r WHERE r.organization_id=%s AND {ref.condition}",
            (organization_id, robot_id),
        )
        count = cursor.fetchone()[0]
        if count:
            result.append(
                DeletionBlocker(
                    kind=ref.kind,
                    label=ref.label,
                    count=count,
                    resolution=ref.resolution,
                )
            )
    return result


def _visible_scope(auth: AuthContext, organization_id: str, capability: str | None):
    if capability is None or auth.is_platform_admin:
        return "TRUE", []
    clauses, values = [], []
    for org, project, region in sorted(auth.organization_scope_triples, key=str):
        if org != organization_id or not auth.has_capability(capability, project, org):
            continue
        # Task names are also protected by upload.read. All task-bearing references
        # use that capability; never disclose task identities via robot.manage alone.
        if region is None and not auth.service_identity:
            clauses.append("r.project_id=%s")
            values.append(project)
        elif region is not None:
            clauses.append("(r.project_id=%s AND r.region_code=%s)")
            values.extend((project, region))
    return " OR ".join(clauses) or "FALSE", values


def read_deletion_check(cursor, organization_id, robot_id, auth, kind, offset, limit):
    blockers = deletion_blockers(cursor, organization_id, robot_id)
    queries, params = [], []
    for ref in REFERENCES:
        if kind is not None and ref.kind != kind:
            continue
        visible, values = _visible_scope(auth, organization_id, ref.capability)
        origin = {
            "raw": "r.robot_id",
            "upload": "r.authenticated_robot_id",
            "configuration": "r.robot_id",
            "component": "r.robot_id",
            "calibration": "r.robot_instance_id",
            "source": "r.source_document #>> '{binding,robot_id}'",
        }[ref.kind]
        queries.append(
            f"SELECT '{ref.kind}'::text AS kind,{ref.projection},"
            f"{origin} AS origin_robot_id FROM {ref.table} r {ref.joins} "
            f"WHERE r.organization_id=%s AND {ref.condition} AND ({visible})"
        )
        params.extend((organization_id, robot_id, *values))
    cte = "WITH refs AS (" + " UNION ALL ".join(queries) + ") "
    cursor.execute(cte + "SELECT count(*) FROM refs", params)
    total = cursor.fetchone()[0]
    cursor.execute(
        cte
        + """SELECT refs.*,task.task_code,task.name AS task_name FROM refs
        LEFT JOIN collection_tasks.collection_tasks task ON task.organization_id=%s
        AND task.project_id=refs.project_id AND task.collection_task_id=refs.collection_task_id
        ORDER BY CASE refs.kind WHEN 'raw' THEN 0 WHEN 'upload' THEN 1
        WHEN 'configuration' THEN 2 WHEN 'component' THEN 3
        WHEN 'calibration' THEN 4 ELSE 5 END,refs.project_id,refs.region_code,refs.id
        LIMIT %s OFFSET %s""",
        (*params, organization_id, limit, offset),
    )
    keys = [column.name for column in cursor.description]
    items = [
        DeletionReference.model_validate(dict(zip(keys, row, strict=True)))
        for row in cursor.fetchall()
    ]
    return RobotDeletionCheck(
        organization_id=organization_id,
        robot_id=robot_id,
        can_delete=not blockers and not pending_cleanup(cursor, organization_id, robot_id),
        blockers=blockers,
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        restricted_count=sum(b.count for b in blockers if kind is None or b.kind == kind) - total,
    )


def pending_cleanup(cursor, organization_id, robot_id):
    cursor.execute(
        """SELECT count(*) FROM robotics.robot_resolution_operations
        WHERE organization_id=%s AND robot_id=%s AND action='DELETE_TASK' AND result IS NULL""",
        (organization_id, robot_id),
    )
    return cursor.fetchone()[0]

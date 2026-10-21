"""Concrete, scope-checked actions used by both robot deletion dialogs."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hc_data_platform.collection_tasks.models import dataset_id_for_collection_task
from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.security.scope import ScopeGuard

from .deletion import REFERENCES, read_deletion_check


class ResolutionTask(BaseModel):
    project_id: str
    region_code: str
    task_id: str
    task_code: str
    name: str
    etag: str
    raw_count: int
    other_robot_raw_count: int
    can_delete: bool
    reason: str | None = None
    retry_key: str | None = None


class ResolutionTarget(BaseModel):
    id: str
    name: str
    serial_no: str


class RobotResolutionPlan(BaseModel):
    organization_id: str
    robot_id: str
    revision: str
    can_transfer: bool
    transfer_reason: str | None = None
    targets: list[ResolutionTarget] = Field(default_factory=list)
    tasks: list[ResolutionTask] = Field(default_factory=list)
    can_clean_config: bool
    config_count: int
    cleanup_reason: str | None = None


class RobotResolutionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["TRANSFER", "DELETE_TASK", "CLEAN_CONFIG"]
    revision: str = Field(min_length=1, max_length=128)
    target_robot_id: str | None = Field(default=None, min_length=1, max_length=128)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_selection(self):
        if self.action == "TRANSFER":
            valid = self.target_robot_id and not self.project_id and not self.task_id
        elif self.action == "DELETE_TASK":
            valid = self.project_id and self.task_id and not self.target_robot_id
        else:
            valid = not (self.target_robot_id or self.project_id or self.task_id)
        if not valid:
            raise ValueError("请选择该操作所需的目标实例或任务。")
        return self


class RobotResolutionResult(BaseModel):
    action: str
    robot_id: str
    target_robot_id: str | None = None
    message: str


def fail(code, detail, status=409):
    return problem(status=status, code=code, title="机器人关联处理未完成", detail=detail)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def permitted(auth, org, project, region, capability):
    try:
        ScopeGuard.require(auth, project, region, org)
        auth.require_capability(capability, project, org)
        return True
    except ProblemException:
        return False


class PostgresRobotResolution:
    def __init__(self, connections, task_deletion=None):
        self.connections = connections
        self.task_deletion = task_deletion

    def plan(self, org, robot, auth):
        with self.connections() as db:
            db.commit()
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            return self._plan(db.cursor(), org, robot, auth)

    def _plan(self, cursor, org, robot, auth):
        cursor.execute(
            "SELECT etag FROM robotics.robot_assets WHERE organization_id=%s AND robot_id=%s",
            (org, robot),
        )
        asset = cursor.fetchone()
        if not asset:
            raise fail("ROBOT_NOT_FOUND", "当前组织中没有这台机器人。", 404)
        report = read_deletion_check(cursor, org, robot, auth, None, 0, 1_000_000)
        reason = None
        if report.restricted_count:
            reason = "需要所有关联项目的数据管理权限，才能转移整台机器人的关联。"
        capabilities = {
            "raw": "upload.manage",
            "upload": "upload.manage",
            "calibration": "calibration.manage",
            "source": "ingest_source.manage",
        }
        for item in report.items:
            if item.project_id and not permitted(
                auth, org, item.project_id, item.region_code, capabilities[item.kind]
            ):
                reason = "需要所有关联项目的数据管理权限，才能转移整台机器人的关联。"
        if any(
            (r.kind == "upload" and r.status not in {"COMMITTED", "FAILED", "CANCELLED"})
            or (r.kind == "raw" and r.status in {"PROCESSING", "RUNNING"})
            for r in report.items
        ):
            reason = "仍有上传或处理正在进行，结束后可转移。"
        cursor.execute(
            """SELECT scheme_id,revision,published_version FROM ingest.robot_processing_schemes
            WHERE organization_id=%s AND robot_id=%s ORDER BY scheme_id""",
            (org, robot),
        )
        schemes = cursor.fetchall()
        cursor.execute(
            """SELECT robot_id,display_name,serial_no FROM robotics.robot_assets
            WHERE organization_id=%s AND robot_id<>%s AND lifecycle_status<>'RETIRED'
            ORDER BY display_name,robot_id""",
            (org, robot),
        )
        targets = [ResolutionTarget(id=r[0], name=r[1], serial_no=r[2]) for r in cursor.fetchall()]
        # Task retry receipts remain discoverable after relational rows were detached
        # but the object store failed. Reopening the dialog must still offer retry.
        cursor.execute(
            """SELECT operation_id,document FROM robotics.robot_resolution_operations
            WHERE organization_id=%s AND robot_id=%s AND action='DELETE_TASK'
            AND result IS NULL ORDER BY created_at""",
            (org, robot),
        )
        retries = {(r[1]["project_id"], r[1]["task_id"]): (r[0], r[1]) for r in cursor.fetchall()}
        if retries:
            reason = "请先重试并完成未结束的数据清理。"
        task_keys = {
            (r.project_id, r.collection_task_id)
            for r in report.items
            if r.collection_task_id and r.project_id
        } | retries.keys()
        tasks = []
        for project, task in sorted(task_keys):
            cursor.execute(
                """SELECT task_code,name,version,upload_region_code,dataset_id
                FROM collection_tasks.collection_tasks
                WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
                (org, project, task),
            )
            row = cursor.fetchone()
            if not row:
                retry = retries.get((project, task))
                if retry and permitted(auth, org, project, retry[1]["region_code"], "upload.read"):
                    prior = ResolutionTask.model_validate(retry[1])
                    tasks.append(
                        prior.model_copy(
                            update={
                                "retry_key": retry[0],
                                "raw_count": 0,
                                "can_delete": permitted(
                                    auth, org, project, prior.region_code, "upload.manage"
                                ),
                            }
                        )
                    )
                continue
            code, name, version, region, dataset = row
            if not permitted(auth, org, project, region, "upload.read"):
                continue
            can_delete = permitted(auth, org, project, region, "upload.manage")
            task_reason = None if can_delete else "需要该项目的数据管理权限。"
            if dataset != dataset_id_for_collection_task(org, project, task):
                can_delete = False
                task_reason = "任务使用共享或独立数据集，可选择转移关联保留数据。"
            cursor.execute(
                """SELECT count(*),count(*) FILTER(WHERE robotics.robot_reference_owner(
                organization_id,'raw',project_id,region_code,raw_source_id,robot_id)
                IS DISTINCT FROM %s)
                FROM ingest.raw_sources WHERE organization_id=%s AND project_id=%s
                AND dataset_id=%s""",
                (robot, org, project, dataset),
            )
            counts = cursor.fetchone()
            tasks.append(
                ResolutionTask(
                    project_id=project,
                    region_code=region,
                    task_id=task,
                    task_code=code,
                    name=name,
                    etag=f'"v{version}"',
                    raw_count=counts[0],
                    other_robot_raw_count=counts[1],
                    can_delete=can_delete,
                    reason=task_reason,
                    retry_key=retries.get((project, task), (None,))[0],
                )
            )
        config_count = sum(b.count for b in report.blockers if b.kind == "configuration")
        has_data = any(b.kind in {"raw", "upload"} for b in report.blockers)
        clean_reason = (
            "先删除关联数据，或选择转移关联。"
            if has_data
            else "请先完成未结束的数据清理。"
            if retries
            else None
        )
        revision = digest(
            {
                "robot": robot,
                "etag": asset[0],
                "schemes": schemes,
                "report": report.model_dump(),
                "tasks": [t.model_dump() for t in tasks],
            }
        )
        return RobotResolutionPlan(
            organization_id=org,
            robot_id=robot,
            revision=revision,
            can_transfer=bool(report.blockers) and reason is None,
            transfer_reason=reason,
            targets=targets,
            tasks=tasks,
            can_clean_config=bool(config_count) and clean_reason is None,
            config_count=config_count,
            cleanup_reason=clean_reason,
        )

    def execute(self, org, robot, auth, command, key):
        fingerprint = digest([robot, command.model_dump(exclude={"revision"})])
        with self.connections() as db:
            cursor = db.cursor()
            # Match processing publication/deletion lock order. Both robots are
            # locked in a stable order, and writes are fenced before discovery.
            for rid in sorted({robot, command.target_robot_id} - {None}):
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (json.dumps(["robot-processing", org, rid]),),
                )
            cursor.execute("SET LOCAL lock_timeout='5s'")
            lock_tables = sorted(
                {
                    *(r.table for r in REFERENCES),
                    "robotics.robot_assets",
                    "robotics.project_robot_assignments",
                    "ingest.robot_ingest_identities",
                    "ingest.robot_processing_schemes",
                    "ingest.collection_jobs",
                    "ingest.mcap_processing_files",
                    "robotics.robot_reference_links",
                }
            )
            cursor.execute(
                sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
                    sql.SQL(",").join(sql.Identifier(*t.split(".")) for t in lock_tables)
                )
            )
            cursor.execute(
                """SELECT fingerprint,result,document FROM robotics.robot_resolution_operations
                WHERE organization_id=%s AND operation_id=%s FOR UPDATE""",
                (org, key),
            )
            previous = cursor.fetchone()
            if previous and previous[0] != fingerprint:
                raise fail("IDEMPOTENCY_CONFLICT", "请求标识已用于其他操作，请刷新后重试。")
            if previous and previous[1]:
                return RobotResolutionResult.model_validate(previous[1])
            plan = self._plan(cursor, org, robot, auth)
            task = None
            if command.action == "DELETE_TASK":
                task = next(
                    (
                        t
                        for t in plan.tasks
                        if (t.project_id, t.task_id) == (command.project_id, command.task_id)
                    ),
                    None,
                )
                if not task or not task.can_delete:
                    raise fail("ROBOT_TASK_DELETE_DENIED", "该任务当前不能删除。", 403)
            if previous is None and command.revision != plan.revision:
                raise fail(
                    "ROBOT_RESOLUTION_STALE", "关联记录已变化，已重新检查，请核对后重试。", 412
                )
            document = task.model_dump() if task else {}
            if not previous:
                cursor.execute(
                    """INSERT INTO robotics.robot_resolution_operations
                    (organization_id,operation_id,robot_id,target_robot_id,action,
                     fingerprint,actor_id,document) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                    (
                        org,
                        key,
                        robot,
                        command.target_robot_id,
                        command.action,
                        fingerprint,
                        auth.subject_id,
                        json.dumps(document),
                    ),
                )
            else:
                document = previous[2]
            cursor.execute("SELECT set_config('app.robot_resolution_id',%s,true)", (key,))
            if command.action == "TRANSFER":
                if not plan.can_transfer:
                    raise fail(
                        "ROBOT_TRANSFER_BLOCKED", plan.transfer_reason or "没有需要转移的关联。"
                    )
                if command.target_robot_id not in {t.id for t in plan.targets}:
                    raise fail(
                        "ROBOT_TRANSFER_TARGET_INVALID", "请选择同组织内的其他机器人实例。", 422
                    )
                self._transfer(cursor, org, robot, command.target_robot_id, key, auth.subject_id)
                message = "目标实例的新关联已创建，旧关联已解除，可以继续删除机器人。"
            elif command.action == "CLEAN_CONFIG":
                if not plan.can_clean_config:
                    raise fail(
                        "ROBOT_CONFIG_CLEANUP_BLOCKED", plan.cleanup_reason or "没有待清理配置。"
                    )
                self._unlink_configs(cursor, org, robot, key, auth.subject_id)
                message = "实例配置关联已清理，历史配置快照仍可追溯。"
            else:
                if self.task_deletion is None:
                    raise fail("ROBOT_TASK_DELETE_UNAVAILABLE", "数据清理服务尚未就绪。", 503)
                # Persist the retry record before crossing the object-store boundary.
                db.commit()
                select_request_scope(
                    document["project_id"], document["region_code"], organization_id=org
                )
                try:
                    self.task_deletion.delete(
                        (org, document["project_id"], document["region_code"]),
                        document["task_id"],
                        document["etag"],
                        key,
                    )
                except Exception:
                    # A rejected preflight has detached nothing. Do not leave a
                    # fictitious pending purge that prevents choosing transfer instead.
                    cursor.execute(
                        """SELECT 1 FROM collection_tasks.deletions
                        WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
                        (org, document["project_id"], document["task_id"]),
                    )
                    if cursor.fetchone() is None:
                        cursor.execute(
                            """DELETE FROM robotics.robot_resolution_operations
                            WHERE organization_id=%s AND operation_id=%s AND result IS NULL""",
                            (org, key),
                        )
                        db.commit()
                    raise
                message = "任务及关联数据已删除。"
            result = RobotResolutionResult(
                action=command.action,
                robot_id=robot,
                target_robot_id=command.target_robot_id,
                message=message,
            )
            cursor.execute(
                """UPDATE robotics.robot_resolution_operations SET result=%s::jsonb
                WHERE organization_id=%s AND operation_id=%s""",
                (result.model_dump_json(), org, key),
            )
            return result

    @staticmethod
    def _link(cursor, org, kind, project, region, reference, origin, target, key, actor):
        # Create a distinct destination association and close the previous one in
        # the same transaction. Original data and authenticated identities never change.
        identity = (org, kind, project or "", region or "", reference, origin)
        cursor.execute(
            """UPDATE robotics.robot_reference_links SET active=false,ended_at=now()
            WHERE organization_id=%s AND kind=%s AND project_id=%s AND region_code=%s
            AND reference_id=%s AND origin_robot_id=%s AND active RETURNING link_id""",
            identity,
        )
        if not cursor.fetchone():
            cursor.execute(
                """INSERT INTO robotics.robot_reference_links
                (organization_id,kind,project_id,region_code,reference_id,origin_robot_id,robot_id,
                 operation_id,created_by,active,ended_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,false,now())""",
                (*identity, origin, key, actor),
            )
        cursor.execute(
            """INSERT INTO robotics.robot_reference_links
            (organization_id,kind,project_id,region_code,reference_id,origin_robot_id,robot_id,
             operation_id,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (*identity, target, key, actor),
        )

    def _unlink_configs(self, cursor, org, robot, key, actor):
        ref = next(r for r in REFERENCES if r.kind == "configuration")
        cursor.execute(
            f"SELECT r.robot_id,r.scheme_id,r.version FROM {ref.table} r "
            f"WHERE r.organization_id=%s AND {ref.condition}",
            (org, robot),
        )
        for origin, scheme, version in cursor.fetchall():
            self._link(
                cursor,
                org,
                "configuration",
                None,
                None,
                f"{scheme}:v{version}",
                origin,
                None,
                key,
                actor,
            )
        cursor.execute(
            """INSERT INTO ingest.robot_processing_audit
            (organization_id,robot_id,scheme_id,actor,action,document)
            SELECT organization_id,robot_id,scheme_id,%s,'ROBOT_ASSOCIATIONS_RESOLVED',
            jsonb_build_object('operation_id',%s::text,'last_draft',draft)
            FROM ingest.robot_processing_schemes WHERE organization_id=%s AND robot_id=%s""",
            (actor, key, org, robot),
        )
        cursor.execute(
            """DELETE FROM ingest.robot_processing_schemes s
            WHERE organization_id=%s AND robot_id=%s AND NOT EXISTS(
             SELECT 1 FROM ingest.robot_processing_versions v WHERE
             (v.organization_id,v.robot_id,v.scheme_id)=(s.organization_id,s.robot_id,s.scheme_id))""",
            (org, robot),
        )

    def _transfer(self, cursor, org, robot, target, key, actor):
        # Gather exactly the current associations, independently of provenance.
        evidence = {}
        for ref in REFERENCES:
            cursor.execute(
                f"SELECT to_jsonb(r) FROM {ref.table} r "
                f"WHERE r.organization_id=%s AND {ref.condition}",
                (org, robot),
            )
            evidence[ref.kind] = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            """UPDATE robotics.robot_resolution_operations SET document=%s::jsonb
            WHERE organization_id=%s AND operation_id=%s""",
            (
                json.dumps(
                    {
                        "counts": {k: len(v) for k, v in evidence.items()},
                        "new_association_robot_id": target,
                    }
                ),
                org,
                key,
            ),
        )
        # New destination configurations have independent keys and no default flag.
        # The exact published source snapshots remain available under their old keys.
        copied_schemes = {}
        for row in evidence["configuration"]:
            origin, scheme = row["robot_id"], row["scheme_id"]
            new_scheme = copied_schemes.get((origin, scheme))
            if new_scheme is None:
                new_scheme = f"moved-{digest([origin, scheme, key])[:24]}"
                copied_schemes[origin, scheme] = new_scheme
                cursor.execute(
                    """INSERT INTO ingest.robot_processing_schemes
                    (organization_id,robot_id,scheme_id,name,software,source_format,revision,draft,
                     validation,published_version,is_default,provenance,updated_at)
                    SELECT organization_id,%s,%s,name,software,source_format,revision,draft,
                    validation,published_version,false,
                    provenance||jsonb_build_object('transferred_from',%s::text,'scheme_id',scheme_id),
                    updated_at FROM ingest.robot_processing_schemes
                    WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s""",
                    (target, new_scheme, origin, org, origin, scheme),
                )
            cursor.execute(
                """INSERT INTO ingest.robot_processing_versions
                SELECT organization_id,%s,%s,version,
                document||jsonb_build_object('robot_id',%s::text,'scheme_id',%s::text,
                  'association_origin',jsonb_build_object('robot_id',robot_id,'scheme_id',scheme_id,
                                                         'version',version)),created_at
                FROM ingest.robot_processing_versions
                WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s AND version=%s""",
                (target, new_scheme, target, new_scheme, org, origin, scheme, row["version"]),
            )
            self._link(
                cursor,
                org,
                "configuration",
                None,
                None,
                f"{scheme}:v{row['version']}",
                origin,
                None,
                key,
                actor,
            )
            self._link(
                cursor,
                org,
                "configuration",
                None,
                None,
                f"{new_scheme}:v{row['version']}",
                target,
                target,
                key,
                actor,
            )
        identities = {
            "raw": ("raw_source_id", "robot_id"),
            "upload": ("upload_id", "authenticated_robot_id"),
            "component": ("component_id", "robot_id"),
            "calibration": ("set_id", "robot_instance_id"),
            "source": ("source_id", None),
        }
        for kind, (id_field, robot_field) in identities.items():
            for row in evidence[kind]:
                origin = (
                    row[robot_field]
                    if robot_field
                    else row["source_document"]["binding"]["robot_id"]
                )
                self._link(
                    cursor,
                    org,
                    kind,
                    row.get("project_id"),
                    row.get("region_code"),
                    row[id_field],
                    origin,
                    target,
                    key,
                    actor,
                )
        # Revoke the old device's ability to append data after the association move.
        # Its historical identity and credentials stay available for provenance FKs.
        cursor.execute(
            """UPDATE ingest.robot_ingest_credentials SET state='REVOKED',
            revoked_at=COALESCE(revoked_at,now()) WHERE organization_id=%s AND ingest_identity_id IN
            (SELECT ingest_identity_id FROM ingest.robot_ingest_identities
             WHERE organization_id=%s AND robot_id=%s)""",
            (org, org, robot),
        )
        cursor.execute(
            """UPDATE ingest.robot_ingest_identities SET state='DISABLED',
            deleted_at=COALESCE(deleted_at,now()),updated_at=now(),
            identity_document=identity_document||jsonb_build_object(
              'state','DISABLED','deleted_at',now())
            WHERE organization_id=%s AND robot_id=%s""",
            (org, robot),
        )
        cursor.execute(
            """INSERT INTO robotics.project_robot_assignments
            (organization_id,project_id,region_code,robot_id,active,assigned_by)
            SELECT organization_id,project_id,region_code,%s,active,%s
            FROM robotics.project_robot_assignments WHERE organization_id=%s AND robot_id=%s
            ON CONFLICT DO NOTHING""",
            (target, actor, org, robot),
        )

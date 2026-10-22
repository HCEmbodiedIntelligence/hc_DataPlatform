"""Explicit task plan revisions and atomic, idempotent recording preparation."""

from __future__ import annotations

from typing import Any

from hc_data_platform.lerobot_imports.robot_eligibility import import_robots

from . import associations, schemes
from .common import Document, audit, check_revision, digest, encoded, lock, now, reject
from .contracts import effective_adapter
from .models import DeviceMapping, PrepareRecording, SetTaskPlan, VersionReference
from .references import AddParticipants

Scope = tuple[str, str, str]  # organization, project, region


def task_record(db: Any, scope: Scope, task: str, *, for_update: bool = False) -> Document:
    row = db.execute(
        """SELECT dataset_id,upload_region_code,status,capture_plan_required
        FROM collection_tasks.collection_tasks WHERE organization_id=%s AND project_id=%s
        AND collection_task_id=%s"""
        + (" FOR UPDATE" if for_update else ""),
        (scope[0], scope[1], task),
    ).fetchone()
    if row is None:
        reject("COLLECTION_TASK_NOT_FOUND", "当前项目中没有此采集任务", 404)
    if row[1] != scope[2]:
        reject("CAPTURE_REGION_MISMATCH", "任务不属于当前区域", 404)
    return dict(zip(("dataset_id", "region_code", "status", "plan_required"), row, strict=True))


def get_plan(db: Any, scope: Scope, task: str) -> Document | None:
    task_record(db, scope, task)
    row = db.execute(
        """SELECT p.document,h.locked_at FROM collection_tasks.capture_plan_heads h
        JOIN collection_tasks.capture_plans p
        USING(organization_id,project_id,collection_task_id,revision)
        WHERE h.organization_id=%s AND h.project_id=%s AND h.collection_task_id=%s
        AND p.region_code=%s""",
        (scope[0], scope[1], task, scope[2]),
    ).fetchone()
    return {**row[0], "locked_at": row[1]} if row else None


def check_dataset(
    db: Any, scope: Scope, dataset: str, task: str, contract: Document, *, freeze: bool = False
) -> None:
    lock(db, "dataset", *scope, dataset)
    signature = digest(contract)
    existing = db.execute(
        """SELECT contract_sha256,document FROM episode_store.dataset_capture_contracts
        WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
        (*scope, dataset),
    ).fetchone()
    if existing and (existing[0] != signature or existing[1] != contract):
        reject(
            "CAPTURE_DATASET_INCOMPATIBLE",
            "任务数据结构与已有数据集不兼容",
            expected=existing[1],
            actual=contract,
        )
    if not existing:
        # A legacy topic->json fingerprint cannot prove axis semantics. It needs an
        # evidence-backed migration; never delete or overwrite its frozen schema.
        legacy = db.execute(
            """SELECT document FROM episode_store.dataset_schemas
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND dataset_id=%s""",
            (*scope, dataset),
        ).fetchone()
        if legacy:
            reject(
                "CAPTURE_DATASET_REVIEW_REQUIRED",
                "已有数据集需要核对历史字段语义后才能绑定方案",
                dataset_id=dataset,
            )
    other = db.execute(
        """SELECT p.document FROM collection_tasks.capture_plans p
        JOIN collection_tasks.capture_plan_heads h
        USING(organization_id,project_id,collection_task_id,revision)
        WHERE p.organization_id=%s AND p.project_id=%s AND p.region_code=%s
        AND p.collection_task_id<>%s AND p.document->>'dataset_id'=%s""",
        (*scope, task, dataset),
    ).fetchall()
    if any(r[0]["output_contract_sha256"] != signature for r in other):
        reject("CAPTURE_DATASET_INCOMPATIBLE", "同一数据集的任务必须采用相同字段语义与帧率")
    if freeze and not existing:
        db.execute(
            """INSERT INTO episode_store.dataset_capture_contracts
            (organization_id,project_id,region_code,dataset_id,contract_sha256,document)
            VALUES(%s,%s,%s,%s,%s,%s::jsonb)""",
            (*scope, dataset, signature, encoded(contract)),
        )


def save_plan(db: Any, scope: Scope, task: str, command: SetTaskPlan, actor: str) -> Document:
    record = task_record(db, scope, task, for_update=True)
    if record["status"] != "ACTIVE":
        reject("COLLECTION_TASK_INACTIVE", "任务已关闭或取消")
    current = get_plan(db, scope, task)
    payload = command.model_dump(mode="json", exclude={"expected_revision"})
    if (
        current
        and current["revision"] == command.expected_revision + 1
        and current["selection"] == payload
    ):
        return current
    check_revision(current["revision"] if current else 0, command.expected_revision)
    lock(db, "scheme", scope[0], command.scheme.scheme_id)
    document = schemes.version(db, scope[0], command.scheme, new_use=True)
    previous = None
    if current and current["locked_at"]:
        previous = schemes.version(db, scope[0], VersionReference.model_validate(current["scheme"]))
        old_adapters = {a["source_format"]: a for a in previous["adapters"]}
        new_adapters = {a["source_format"]: a for a in document["adapters"]}
        if (
            command.scheme.scheme_id != current["scheme"]["scheme_id"]
            or command.scheme.version <= current["scheme"]["version"]
            or document["output"] != current["output"]
            or document["quality_policy"] != previous["quality_policy"]
            or document["model_version_ids"] != previous["model_version_ids"]
            or not old_adapters.keys() < new_adapters.keys()
            or any(new_adapters.get(k) != v for k, v in old_adapters.items())
            or {p.robot_id for p in command.participants}
            != {p["robot_id"] for p in current["participants"]}
        ):
            reject(
                "CAPTURE_PLAN_LOCKED",
                "任务已开始采集，只能在保持原输出和已有格式不变时补充上传格式",
            )
    participants = []
    for participant in sorted(command.participants, key=lambda p: p.robot_id):
        lock(db, "robot", scope[0], participant.robot_id)
        if not import_robots(db, *scope, participant.robot_id):
            reject("CAPTURE_ROBOT_UNAVAILABLE", "参与实例在当前项目中不可用")
        mapping = associations.require_mapping(
            db, scope[0], participant.robot_id, document, participant.mapping_version, new_use=True
        )
        if previous is not None:
            old_participant = next(
                p for p in current["participants"] if p["robot_id"] == participant.robot_id
            )
            old_mapping = associations.mapping_version(
                db,
                scope[0],
                participant.robot_id,
                command.scheme.scheme_id,
                old_participant["mapping_version"],
            )
            for adapter in previous["adapters"]:
                fmt = adapter["source_format"]
                if effective_adapter(
                    previous, fmt, DeviceMapping.model_validate(old_mapping["mapping"])
                ) != effective_adapter(
                    document, fmt, DeviceMapping.model_validate(mapping["mapping"])
                ):
                    reject("CAPTURE_PLAN_LOCKED", "追加格式不能改变已有格式的设备映射")
        participants.append(
            {
                "robot_id": participant.robot_id,
                "mapping_version": participant.mapping_version,
                "mapping_sha256": mapping["content_sha256"],
            }
        )
    check_dataset(db, scope, record["dataset_id"], task, document["output"])
    content = {
        "schema_version": "capture-task-plan/v1",
        "organization_id": scope[0],
        "project_id": scope[1],
        "region_code": scope[2],
        "collection_task_id": task,
        "dataset_id": record["dataset_id"],
        "revision": command.expected_revision + 1,
        "scheme": payload["scheme"],
        "selection": payload,
        "participants": participants,
        "output": document["output"],
        "output_contract_sha256": document["output_contract_sha256"],
        "created_by": actor,
        "created_at": now(),
    }
    plan = {**content, "plan_sha256": digest(content)}
    store_plan(db, scope, task, plan)
    audit(db, scope[0], command.scheme.scheme_id, actor, "SET_TASK_PLAN", plan)
    return {**plan, "locked_at": current["locked_at"] if current else None}


def store_plan(db: Any, scope: Scope, task: str, plan: Document) -> None:
    """Append immutable history and advance the head without clearing its lock."""
    db.execute(
        """INSERT INTO collection_tasks.capture_plans
        (organization_id,project_id,collection_task_id,region_code,revision,scheme_id,
         scheme_version,document,content_sha256)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
        (
            scope[0],
            scope[1],
            task,
            scope[2],
            plan["revision"],
            plan["scheme"]["scheme_id"],
            plan["scheme"]["version"],
            encoded(plan),
            plan["plan_sha256"],
        ),
    )
    db.execute(
        """INSERT INTO collection_tasks.capture_plan_heads
        (organization_id,project_id,collection_task_id,revision) VALUES(%s,%s,%s,%s)
        ON CONFLICT(organization_id,project_id,collection_task_id)
        DO UPDATE SET revision=EXCLUDED.revision""",
        (scope[0], scope[1], task, plan["revision"]),
    )


def add_participants(
    db: Any, scope: Scope, task: str, command: AddParticipants, actor: str
) -> Document:
    """Extend participation without replacing any selected version or old mapping."""
    record = task_record(db, scope, task, for_update=True)
    current = get_plan(db, scope, task)
    if current is None:
        reject("CAPTURE_PLAN_REQUIRED", "请先为任务设置采集方案")
    additions = [
        p.model_dump(mode="json") for p in sorted(command.participants, key=lambda p: p.robot_id)
    ]
    change = {
        "kind": "ADD_PARTICIPANTS",
        "base_revision": command.expected_revision,
        "participants": additions,
    }
    if current["revision"] == command.expected_revision + 1 and current.get("change") == change:
        return current
    check_revision(current["revision"], command.expected_revision)
    if record["status"] != "ACTIVE":
        reject("COLLECTION_TASK_INACTIVE", "任务已关闭或取消")
    existing_ids = {p["robot_id"] for p in current["participants"]}
    if existing_ids.intersection(p["robot_id"] for p in additions):
        reject("CAPTURE_PARTICIPANT_EXISTS", "追加操作不能更改已有参与实例的映射")
    if len(existing_ids) + len(additions) > 100:
        reject("CAPTURE_PARTICIPANT_LIMIT", "任务最多支持100个参与实例")
    reference = VersionReference.model_validate(current["scheme"])
    lock(db, "scheme", scope[0], reference.scheme_id)
    document = schemes.version(db, scope[0], reference, new_use=True)
    participants = list(current["participants"])
    for participant in additions:
        robot = participant["robot_id"]
        lock(db, "robot", scope[0], robot)
        if not import_robots(db, *scope, robot):
            reject("CAPTURE_ROBOT_UNAVAILABLE", "参与实例在当前项目中不可用")
        mapping = associations.require_mapping(
            db, scope[0], robot, document, participant["mapping_version"], new_use=True
        )
        participants.append({**participant, "mapping_sha256": mapping["content_sha256"]})
    check_dataset(db, scope, record["dataset_id"], task, document["output"])
    participants.sort(key=lambda p: p["robot_id"])
    selection = {
        "scheme": current["scheme"],
        "participants": [
            {"robot_id": p["robot_id"], "mapping_version": p["mapping_version"]}
            for p in participants
        ],
    }
    content = {
        **{k: v for k, v in current.items() if k not in {"plan_sha256", "locked_at"}},
        "revision": command.expected_revision + 1,
        "selection": selection,
        "participants": participants,
        "change": change,
        "created_by": actor,
        "created_at": now(),
    }
    plan = {**content, "plan_sha256": digest(content)}
    store_plan(db, scope, task, plan)
    audit(db, scope[0], reference.scheme_id, actor, "ADD_TASK_PARTICIPANTS", plan)
    return {**plan, "locked_at": current["locked_at"]}


def for_robot(db: Any, scope: Scope, task: str, robot: str, source_format: str) -> Document:
    """Read-only preflight; never lock or change a task."""
    record = task_record(db, scope, task)
    plan = get_plan(db, scope, task)
    if plan is None:
        reject(
            "CAPTURE_PLAN_REQUIRED",
            "任务采集方案待配置",
            compatibility="NEW_TASK" if record["plan_required"] else "LEGACY_UNRESOLVED",
        )
    if record["dataset_id"] != plan["dataset_id"]:
        reject("CAPTURE_PLAN_TARGET_MISMATCH", "任务数据集与采集计划不一致")
    if record["status"] != "ACTIVE":
        reject("COLLECTION_TASK_INACTIVE", "任务已关闭或取消")
    if not import_robots(db, *scope, robot):
        reject("CAPTURE_ROBOT_UNAVAILABLE", "当前机器人在此项目不可用")
    participant = next((p for p in plan["participants"] if p["robot_id"] == robot), None)
    document = schemes.version(
        db, scope[0], VersionReference.model_validate(plan["scheme"]), new_use=not plan["locked_at"]
    )
    # Old plans may retain immutable mapping pins, but these are not an allowlist.
    # Other compatible robots resolve their own mapping when they start recording.
    mapping = (
        associations.require_mapping(
            db,
            scope[0],
            robot,
            document,
            participant["mapping_version"],
            new_use=not plan["locked_at"],
        )
        if participant
        else associations.task_mapping(db, scope[0], robot, document)
    )
    adapter = effective_adapter(
        document, source_format, DeviceMapping.model_validate(mapping["mapping"])
    )
    return {
        "plan": plan,
        "scheme": plan["scheme"],
        "mapping": mapping,
        "adapter": adapter,
        "output": plan["output"],
        "source_format": source_format,
    }


def prepare_recording(
    db: Any, scope: Scope, task: str, robot: str, command: PrepareRecording, actor: str
) -> Document:
    record = task_record(db, scope, task, for_update=True)
    plan = get_plan(db, scope, task)
    if plan is None:
        reject("CAPTURE_PLAN_REQUIRED", "任务采集方案待配置")
    lock(db, "scheme", scope[0], plan["scheme"]["scheme_id"])
    lock(db, "robot", scope[0], robot)
    existing = db.execute(
        """SELECT document FROM ingest.capture_recording_snapshots
        WHERE organization_id=%s AND robot_id=%s AND recording_id=%s""",
        (scope[0], robot, command.recording_id),
    ).fetchone()
    if existing:
        document = existing[0]
        if (
            document["collection_task_id"] != task
            or document["project_id"] != scope[1]
            or document["region_code"] != scope[2]
            or document["plan_revision"] != command.expected_plan_revision
            or document["source_format"] != command.source_format
        ):
            reject("CAPTURE_RECORDING_CONFLICT", "此录制已绑定不同任务或方案")
        associations.require_robot(db, scope[0], robot)
        return dict(document)
    check_revision(plan["revision"], command.expected_plan_revision)
    resolved = for_robot(db, scope, task, robot, command.source_format)
    check_dataset(db, scope, record["dataset_id"], task, plan["output"], freeze=True)
    content = {
        "schema_version": "capture-recording-snapshot/v1",
        "organization_id": scope[0],
        "project_id": scope[1],
        "region_code": scope[2],
        "collection_task_id": task,
        "dataset_id": record["dataset_id"],
        "robot_id": robot,
        "recording_id": command.recording_id,
        "plan_revision": plan["revision"],
        "plan_sha256": plan["plan_sha256"],
        "scheme": plan["scheme"],
        "mapping_version": resolved["mapping"]["mapping_version"],
        "mapping_sha256": resolved["mapping"]["content_sha256"],
        "source_format": command.source_format,
        "effective_adapter": resolved["adapter"],
        "output": plan["output"],
        "output_contract_sha256": plan["output_contract_sha256"],
        "source": "TASK_PLAN",
        "prepared_at": now(),
    }
    snapshot = {**content, "snapshot_sha256": digest(content)}
    db.execute(
        """INSERT INTO ingest.capture_recording_snapshots
        (organization_id,robot_id,recording_id,project_id,region_code,collection_task_id,
        plan_revision,document,content_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
        (
            scope[0],
            robot,
            command.recording_id,
            scope[1],
            scope[2],
            task,
            plan["revision"],
            encoded(snapshot),
            snapshot["snapshot_sha256"],
        ),
    )
    db.execute(
        """UPDATE collection_tasks.capture_plan_heads SET locked_at=COALESCE(locked_at,now())
        WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
        (scope[0], scope[1], task),
    )
    audit(
        db,
        scope[0],
        plan["scheme"]["scheme_id"],
        actor,
        "PREPARE_RECORDING",
        {
            "collection_task_id": task,
            "recording_id": command.recording_id,
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "robot_id": robot,
        },
    )
    return snapshot

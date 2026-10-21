"""Transactional configuration store. All keys include organization and robot."""

import json
from datetime import datetime, timezone

from hc_data_platform.continuous_recordings.mcap_native import McapProcessingConfig, resolve
from hc_data_platform.core.errors import problem
from hc_data_platform.security.audit import canonical_hash


def reject(code, detail, status=409):
    raise problem(status=status, code=code, title=detail, detail=detail)


def execute(db, sql, values=()):
    # psycopg cursors and the project's DbApi wrappers both support execute/fetchone.
    cursor = db.execute(sql, values)
    return cursor if cursor is not None else db


def lock(db, org, robot):
    execute(
        db,
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (json.dumps(["robot-processing", org, robot]),),
    )
    if not execute(
        db,
        "SELECT 1 FROM robotics.robot_assets WHERE organization_id=%s AND robot_id=%s",
        (org, robot),
    ).fetchone():
        reject("ROBOT_NOT_FOUND", "当前组织中没有这台机器人", 404)


def audit(db, org, robot, scheme, actor, action, document):
    execute(
        db,
        (
            """INSERT INTO ingest.robot_processing_audit
            (organization_id,robot_id,scheme_id,actor,action,document)
            VALUES(%s,%s,%s,%s,%s,%s::jsonb)"""
        ),
        (org, robot, scheme, actor, action, json.dumps(document, ensure_ascii=False)),
    )


def read(db, org, robot):
    lock(db, org, robot)
    schemes = execute(
        db,
        """SELECT scheme_id,name,software,source_format,revision,draft,validation,
        published_version,is_default,provenance FROM ingest.robot_processing_schemes
        WHERE organization_id=%s AND robot_id=%s ORDER BY scheme_id""",
        (org, robot),
    ).fetchall()
    keys = (
        "scheme_id",
        "name",
        "software",
        "source_format",
        "revision",
        "draft",
        "validation",
        "published_version",
        "is_default",
        "provenance",
    )
    versions = execute(
        db,
        """SELECT document FROM ingest.robot_processing_versions
        WHERE organization_id=%s AND robot_id=%s ORDER BY scheme_id,version DESC""",
        (org, robot),
    ).fetchall()
    history = execute(
        db,
        """SELECT actor,action,document,created_at FROM ingest.robot_processing_audit
        WHERE organization_id=%s AND robot_id=%s ORDER BY audit_id DESC LIMIT 100""",
        (org, robot),
    ).fetchall()
    return {
        "robot_id": robot,
        "schemes": [dict(zip(keys, r, strict=True)) for r in schemes],
        "versions": [r[0] for r in versions],
        "audit": [
            dict(zip(("actor", "action", "document", "created_at"), r, strict=True))
            for r in history
        ],
    }


def current(db, org, robot, scheme, expected):
    lock(db, org, robot)
    item = execute(
        db,
        """SELECT revision,draft,validation,published_version FROM ingest.robot_processing_schemes
        WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s FOR UPDATE""",
        (org, robot, scheme),
    ).fetchone()
    if (item[0] if item else 0) != expected:
        reject("ROBOT_CONFIG_REVISION_CONFLICT", "配置已被其他人修改，请刷新后比较再保存")
    return item


def save(db, org, robot, scheme, command, actor, provenance=None):
    previous = current(db, org, robot, scheme, command.expected_revision)
    document = command.model_dump(mode="json", exclude={"expected_revision"})
    if command.template_id:
        template = execute(
            db,
            """SELECT document FROM ingest.robot_processing_templates
            WHERE organization_id=%s AND template_id=%s""",
            (org, command.template_id),
        ).fetchone()
        if template is None:
            reject("ROBOT_CONFIG_TEMPLATE_UNKNOWN", "模板版本不存在")
        if template[0]["software"] != command.software:
            reject("ROBOT_CONFIG_TEMPLATE_MISMATCH", "采集软件与模板不匹配")
        model = execute(
            db,
            (
                "SELECT robot_model_version_id FROM robotics.robot_assets "
                "WHERE organization_id=%s AND robot_id=%s"
            ),
            (org, robot),
        ).fetchone()
        if model[0] != template[0]["model_version_id"]:
            reject("ROBOT_CONFIG_TEMPLATE_MISMATCH", "机器人绑定型号与模板不匹配")
    execute(
        db,
        (
            """INSERT INTO ingest.robot_processing_schemes
            (organization_id,robot_id,scheme_id,name,software,source_format,revision,draft,provenance)
            VALUES(%s,%s,%s,%s,%s,%s,1,%s::jsonb,%s::jsonb) ON
            CONFLICT(organization_id,robot_id,scheme_id) DO UPDATE SET
            name=EXCLUDED.name,software=EXCLUDED.software,source_format=EXCLUDED.source_format,
            revision=robot_processing_schemes.revision+1,draft=EXCLUDED.draft,validation=NULL,updated_at=now()"""
        ),
        (
            org,
            robot,
            scheme,
            command.name,
            command.software,
            command.source_format,
            json.dumps(document),
            json.dumps(provenance or {}),
        ),
    )
    audit(
        db,
        org,
        robot,
        scheme,
        actor,
        "SAVE_DRAFT",
        {"before": previous[1] if previous else None, "after": document},
    )
    return read(db, org, robot)


def validate_configuration(config, inventory):
    cfg = McapProcessingConfig.model_validate(config)
    if not cfg.joint_names or not cfg.joint_units or any(u == "unknown" for u in cfg.joint_units):
        raise ValueError("MCAP_JOINT_LAYOUT_MISMATCH: 请明确训练关节顺序及单位")
    if cfg.collection_mode == "JOINT_ONLY" and cfg.time_source != "STATE_LOG_TIME":
        raise ValueError("MCAP_TIME_SOURCE_REQUIRED: 纯关节方案须声明 STATE_LOG_TIME")
    if cfg.collection_mode == "RGB" and cfg.time_source != "RGB_LOG_TIME":
        raise ValueError("MCAP_TIME_SOURCE_REQUIRED: RGB 方案须声明 RGB_LOG_TIME")
    if cfg.collection_mode == "RGB" and not any(
        c.topic == cfg.primary_camera and c.kind == "rgb" and c.required for c in cfg.cameras
    ):
        raise ValueError("MCAP_MAPPING_REQUIRED: 请声明主 RGB 相机的标识、消息类型及必需标记")
    if cfg.depth_scale_m_per_unit is not None and not cfg.depth_unit_evidence:
        raise ValueError("MCAP_DEPTH_UNITS_REQUIRED: 请记录深度单位换算依据")
    if cfg.derive_action_from_next_state:
        raise ValueError("MCAP_DERIVED_ACTION_UNSUPPORTED: 首版发布仅支持明确的动作来源")
    return resolve(cfg, inventory)


def validate(db, org, robot, scheme, revision, inventory, evidence, actor):
    item = current(db, org, robot, scheme, revision)
    if item is None:
        reject("ROBOT_CONFIG_NOT_FOUND", "采集方案不存在", 404)
    error = evidence.get("decode_error")
    try:
        validate_configuration(item[1]["configuration"], inventory)
    except ValueError as exc:
        error = str(exc)
    validation = {
        "revision": revision,
        "status": "FAILED" if error else "PASSED",
        "error": error,
        "method": "SAMPLE",
        "evidence": evidence,
        "inventory": inventory,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "draft_sha256": canonical_hash(item[1]),
    }
    execute(
        db,
        """UPDATE ingest.robot_processing_schemes SET validation=%s::jsonb
        WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s""",
        (json.dumps(validation), org, robot, scheme),
    )
    audit(db, org, robot, scheme, actor, "VALIDATE_SAMPLE", validation)
    return read(db, org, robot)


def publish(db, org, robot, scheme, command, actor):
    item = current(db, org, robot, scheme, command.expected_revision)
    if (
        not item
        or not item[2]
        or item[2]["status"] != "PASSED"
        or item[2]["draft_sha256"] != canonical_hash(item[1])
    ):
        reject(
            "ROBOT_CONFIG_VALIDATION_REQUIRED",
            "请先用实际样本验证当前草稿；连接成功不等于采集验证通过",
        )
    # Replay after a lost response is idempotent, even when another draft is never saved.
    versions = execute(
        db,
        """SELECT version,document FROM ingest.robot_processing_versions
        WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s ORDER BY version DESC LIMIT 1""",
        (org, robot, scheme),
    ).fetchone()
    if versions and versions[1]["draft_revision"] == command.expected_revision:
        return read(db, org, robot)
    version = versions[0] + 1 if versions else 1
    document = {
        **item[1],
        "robot_id": robot,
        "scheme_id": scheme,
        "version": version,
        "draft_revision": command.expected_revision,
        "validation": item[2],
        "content_sha256": canonical_hash(item[1]),
        "published_by": actor,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "source": "INSTANCE_VERSION",
    }
    execute(
        db,
        """INSERT INTO ingest.robot_processing_versions
        (organization_id,robot_id,scheme_id,version,document) VALUES(%s,%s,%s,%s,%s::jsonb)""",
        (org, robot, scheme, version, json.dumps(document)),
    )
    if command.make_default:
        execute(
            db,
            """UPDATE ingest.robot_processing_schemes SET is_default=false
            WHERE organization_id=%s AND robot_id=%s AND source_format=%s AND is_default""",
            (org, robot, item[1]["source_format"]),
        )
    execute(
        db,
        (
            """UPDATE ingest.robot_processing_schemes SET
            published_version=%s,is_default=%s,updated_at=now() WHERE organization_id=%s AND
            robot_id=%s AND scheme_id=%s"""
        ),
        (version, command.make_default, org, robot, scheme),
    )
    audit(
        db,
        org,
        robot,
        scheme,
        actor,
        "PUBLISH_DEFAULT" if command.make_default else "PUBLISH",
        document,
    )
    return read(db, org, robot)


def select_snapshot(db, scope, robot, reference=None, *, legacy=True):
    org, project, region = scope
    if reference:
        reference = reference.model_dump() if hasattr(reference, "model_dump") else reference
        found = execute(
            db,
            """SELECT document FROM ingest.robot_processing_versions
            WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s AND version=%s""",
            (org, robot, reference["scheme_id"], reference["version"]),
        ).fetchone()
        if not found or found[0]["content_sha256"] != reference["content_sha256"]:
            return {"error": "ROBOT_PROCESSING_CONFIG_VERSION_UNKNOWN", "requested": reference}
        return found[0]
    found = execute(
        db,
        (
            """SELECT v.document FROM ingest.robot_processing_schemes s JOIN
            ingest.robot_processing_versions v ON
            (v.organization_id,v.robot_id,v.scheme_id,v.version)=
            (s.organization_id,s.robot_id,s.scheme_id,s.published_version) WHERE
            s.organization_id=%s AND s.robot_id=%s AND s.source_format='MCAP' AND s.is_default"""
        ),
        (org, robot),
    ).fetchall()
    if len(found) == 1:
        return found[0][0]
    schemes = execute(
        db,
        """SELECT provenance FROM ingest.robot_processing_schemes
        WHERE organization_id=%s AND robot_id=%s""",
        (org, robot),
    ).fetchall()
    if any(r[0].get("conflict") for r in schemes):
        return {"error": "ROBOT_PROCESSING_CONFIG_CONFLICT"}
    # Controlled compatibility: an exact old scope only, never most-recent scope.
    if legacy and (not schemes or all(r[0].get("migration_candidate_id") for r in schemes)):
        old = execute(
            db,
            """SELECT configuration FROM ingest.mcap_robot_profiles
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND robot_id=%s""",
            (*scope, robot),
        ).fetchone()
        if old:
            return {
                "source": "LEGACY_SCOPE_UNVERIFIED",
                "configuration": old[0],
                "content_sha256": canonical_hash(old[0]),
                "legacy_scope": {
                    "organization_id": org,
                    "project_id": project,
                    "region_code": region,
                },
            }
    return {"error": "ROBOT_PROCESSING_CONFIG_REQUIRED"}


def check_task(db, scope, task, snapshot):
    if snapshot.get("error"):
        return snapshot
    requirements = execute(
        db,
        """SELECT requires_rgb,allows_derived_action FROM ingest.robot_task_data_requirements
        WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
        (scope[0], scope[1], task),
    ).fetchone()
    cfg = snapshot["configuration"]
    if requirements and (
        (requirements[0] and cfg.get("collection_mode", "RGB") == "JOINT_ONLY")
        or (cfg.get("derive_action_from_next_state") and not requirements[1])
    ):
        return {**snapshot, "error": "ROBOT_TASK_DATA_INCOMPATIBLE"}
    return snapshot

"""Instance associations keep independent sources without copying a shared contract."""

from __future__ import annotations

from typing import Any

from . import schemes
from .automatic import validation_coverage
from .common import Document, audit, check_revision, digest, encoded, lock, now, reject
from .contracts import effective_adapter
from .models import AssociateScheme


def require_robot(db: Any, org: str, robot: str, document: Document | None = None) -> None:
    row = db.execute(
        "SELECT robot_model_version_id,lifecycle_status FROM robotics.robot_assets "
        "WHERE organization_id=%s AND robot_id=%s",
        (org, robot),
    ).fetchone()
    if row is None:
        reject("ROBOT_NOT_FOUND", "当前组织中没有这台机器人", 404)
    if row[1] != "ACTIVE":
        reject("CAPTURE_ROBOT_UNAVAILABLE", "当前机器人未启用")
    if document and document["model_version_ids"] and row[0] not in document["model_version_ids"]:
        reject("CAPTURE_MODEL_INCOMPATIBLE", "机器人型号与采集方案不兼容")


def list_links(db: Any, org: str, robot: str) -> list[Document]:
    rows = db.execute(
        """SELECT l.scheme_id,l.revision,l.scheme_version,l.mapping_version,l.is_default,
        l.active,m.document,v.document,s.archived FROM ingest.capture_scheme_links l
        JOIN ingest.capture_mapping_versions m
        USING(organization_id,robot_id,scheme_id,mapping_version)
        JOIN ingest.capture_scheme_versions v ON (v.organization_id,v.scheme_id,v.version)=
        (l.organization_id,l.scheme_id,l.scheme_version)
        JOIN ingest.capture_schemes s ON (s.organization_id,s.scheme_id)=
        (l.organization_id,l.scheme_id)
        WHERE l.organization_id=%s AND l.robot_id=%s ORDER BY l.scheme_id""",
        (org, robot),
    ).fetchall()
    items = []
    for row in rows:
        validations = db.execute(
            """SELECT document FROM ingest.capture_mapping_validations
            WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s AND mapping_version=%s
            ORDER BY created_at DESC""",
            (org, robot, row[0], row[3]),
        ).fetchall()
        passed = {
            v[0]["source_format"]
            for v in validations
            if v[0]["status"] == "PASSED" and v[0]["mapping_sha256"] == row[6]["content_sha256"]
        }
        formats = {a["source_format"] for a in row[7]["adapters"]}
        items.append(
            {
                "scheme_id": row[0],
                "revision": row[1],
                "scheme_version": row[2],
                "mapping_version": row[3],
                "is_default": row[4],
                "active": row[5],
                "mapping": row[6],
                "scheme": row[7],
                "archived": row[8],
                "status": "READY"
                if formats <= validation_coverage(row[7], passed)
                else "MAPPING_UNVERIFIED",
                "validated_formats": sorted(passed),
                "validations": [v[0] for v in validations],
            }
        )
    return items


def associate(
    db: Any, org: str, robot: str, command: AssociateScheme, actor: str
) -> list[Document]:
    # Consistent order: scheme then robot. Archive and association are mutually exclusive.
    lock(db, "scheme", org, command.scheme.scheme_id)
    lock(db, "robot", org, robot)
    document = schemes.version(db, org, command.scheme, new_use=True)
    require_robot(db, org, robot, document)
    for adapter in document["adapters"]:
        effective_adapter(document, adapter["source_format"], command.mapping)
    scheme = command.scheme.scheme_id
    row = db.execute(
        """SELECT revision,scheme_version,mapping_version,active,is_default
        FROM ingest.capture_scheme_links WHERE organization_id=%s AND robot_id=%s
        AND scheme_id=%s FOR UPDATE""",
        (org, robot, scheme),
    ).fetchone()
    # A command receipt is reconstructed from its immutable mapping and revision.
    mapping_payload = {
        "scheme": command.scheme.model_dump(mode="json"),
        "mapping": command.mapping.model_dump(mode="json"),
    }
    if row and row[0] == command.expected_revision + 1 and row[3]:
        old = mapping_version(db, org, robot, scheme, row[2])
        if (
            old["scheme"] == mapping_payload["scheme"]
            and old["mapping"] == mapping_payload["mapping"]
            and row[4] == command.make_default
        ):
            return list_links(db, org, robot)
    check_revision(row[0] if row else 0, command.expected_revision)
    mapping_revision = (row[2] if row else 0) + 1
    mapping_document = {
        **mapping_payload,
        "mapping_version": mapping_revision,
        "content_sha256": digest(mapping_payload),
        "created_by": actor,
        "created_at": now(),
    }
    if command.make_default:
        db.execute(
            """UPDATE ingest.capture_scheme_links SET is_default=false,revision=revision+1,
            updated_at=now() WHERE organization_id=%s AND robot_id=%s AND is_default
            AND scheme_id<>%s""",
            (org, robot, scheme),
        )
    db.execute(
        """INSERT INTO ingest.capture_scheme_links
        (organization_id,robot_id,scheme_id,revision,scheme_version,mapping_version,is_default)
        VALUES(%s,%s,%s,1,%s,%s,%s) ON CONFLICT(organization_id,robot_id,scheme_id)
        DO UPDATE SET revision=capture_scheme_links.revision+1,
        scheme_version=EXCLUDED.scheme_version,mapping_version=EXCLUDED.mapping_version,
        is_default=EXCLUDED.is_default,active=true,updated_at=now()""",
        (org, robot, scheme, command.scheme.version, mapping_revision, command.make_default),
    )
    db.execute(
        """INSERT INTO ingest.capture_mapping_versions
        (organization_id,robot_id,scheme_id,mapping_version,scheme_version,content_sha256,document)
        VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)""",
        (
            org,
            robot,
            scheme,
            mapping_revision,
            command.scheme.version,
            mapping_document["content_sha256"],
            encoded(mapping_document),
        ),
    )
    audit(
        db,
        org,
        scheme,
        actor,
        "ASSOCIATE_INSTANCE",
        {
            "robot_id": robot,
            "revision": command.expected_revision + 1,
            "mapping_version": mapping_revision,
            "make_default": command.make_default,
        },
    )
    return list_links(db, org, robot)


def mapping_version(db: Any, org: str, robot: str, scheme: str, version: int) -> Document:
    row = db.execute(
        """SELECT document FROM ingest.capture_mapping_versions
        WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s AND mapping_version=%s""",
        (org, robot, scheme, version),
    ).fetchone()
    if row is None:
        reject("CAPTURE_MAPPING_NOT_FOUND", "实例映射版本不存在", 404)
    return dict(row[0])


def record_validation(
    db: Any, org: str, robot: str, scheme: str, version: int, evidence: Document, actor: str
) -> list[Document]:
    lock(db, "robot", org, robot)
    mapping = mapping_version(db, org, robot, scheme, version)
    if evidence.get("sample_robot_id") != robot:
        reject("CAPTURE_MAPPING_SAMPLE_MISMATCH", "映射验证需要来自当前实例的实际样本")
    if evidence.get("mapping_sha256") != mapping["content_sha256"]:
        reject("CAPTURE_MAPPING_MISMATCH", "样本验证与映射版本不一致")
    identity = digest(evidence)
    document = {**evidence, "validation_id": identity, "validated_at": now(), "actor": actor}
    db.execute(
        """INSERT INTO ingest.capture_mapping_validations
        (organization_id,robot_id,scheme_id,mapping_version,source_format,validation_id,document)
        VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
        (org, robot, scheme, version, evidence["source_format"], identity, encoded(document)),
    )
    audit(
        db,
        org,
        scheme,
        actor,
        "VALIDATE_INSTANCE_MAPPING",
        {
            "robot_id": robot,
            "mapping_version": version,
            "validation_id": identity,
        },
    )
    return list_links(db, org, robot)


def task_mapping(db: Any, org: str, robot: str, document: Document) -> Document:
    """Resolve this robot's mapping for the task version without enrolling it.

    An active association can keep serving an older task version after an
    upgrade. Use the latest mapping for that exact version, and validate it;
    never silently fall back from an unverified mapping to an older one.
    """
    require_robot(db, org, robot, document)
    link = db.execute(
        """SELECT active FROM ingest.capture_scheme_links
        WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s""",
        (org, robot, document["scheme_id"]),
    ).fetchone()
    if not link or not link[0]:
        reject("CAPTURE_ASSOCIATION_REQUIRED", "当前机器人未关联任务采集方案")
    row = db.execute(
        """SELECT mapping_version FROM ingest.capture_mapping_versions
        WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s AND scheme_version=%s
        ORDER BY mapping_version DESC LIMIT 1""",
        (org, robot, document["scheme_id"], document["version"]),
    ).fetchone()
    if not row:
        reject("CAPTURE_MAPPING_MISMATCH", "当前机器人没有任务所需方案版本的设备映射")
    return require_mapping(db, org, robot, document, row[0], new_use=True)


def require_mapping(
    db: Any, org: str, robot: str, document: Document, version: int, *, new_use: bool
) -> Document:
    require_robot(db, org, robot, document)
    scheme = document["scheme_id"]
    if new_use:
        row = db.execute(
            """SELECT active,scheme_version,mapping_version FROM ingest.capture_scheme_links
            WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s""",
            (org, robot, scheme),
        ).fetchone()
        if not row or not row[0]:
            reject("CAPTURE_ASSOCIATION_REQUIRED", "当前机器人未关联任务采集方案")
        # Older immutable mappings remain selectable while the association is active.
    mapping = mapping_version(db, org, robot, scheme, version)
    if mapping["scheme"]["version"] != document["version"]:
        reject("CAPTURE_MAPPING_MISMATCH", "实例映射不属于任务选择的方案版本")
    if mapping["scheme"]["content_sha256"] != document["content_sha256"]:
        reject("CAPTURE_MAPPING_MISMATCH", "实例映射与共享版本内容摘要不一致")
    validations = db.execute(
        """SELECT document FROM ingest.capture_mapping_validations
        WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s AND mapping_version=%s""",
        (org, robot, scheme, version),
    ).fetchall()
    passed = {
        v[0]["source_format"]
        for v in validations
        if v[0]["status"] == "PASSED" and v[0]["mapping_sha256"] == mapping["content_sha256"]
    }
    required = {a["source_format"] for a in document["adapters"]}
    if required - validation_coverage(document, passed):
        reject(
            "CAPTURE_MAPPING_UNVERIFIED",
            "实例映射尚未通过实际样本验证",
            missing_formats=sorted(required - passed),
            robot_id=robot,
        )
    return mapping


def remove(db: Any, org: str, robot: str, scheme: str, revision: int, actor: str) -> None:
    lock(db, "robot", org, robot)
    row = db.execute(
        """SELECT revision,active FROM ingest.capture_scheme_links
        WHERE organization_id=%s AND robot_id=%s AND scheme_id=%s FOR UPDATE""",
        (org, robot, scheme),
    ).fetchone()
    if row and not row[1] and row[0] == revision + 1:
        return
    check_revision(row[0] if row else 0, revision)
    db.execute(
        """UPDATE ingest.capture_scheme_links SET active=false,is_default=false,
        revision=revision+1,updated_at=now() WHERE organization_id=%s AND robot_id=%s
        AND scheme_id=%s""",
        (org, robot, scheme),
    )
    audit(db, org, scheme, actor, "REMOVE_ASSOCIATION", {"robot_id": robot, "revision": revision})

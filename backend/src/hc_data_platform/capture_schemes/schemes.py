"""Caller-owned transactions; advisory locks serialize publication and draft writes."""

from __future__ import annotations

from typing import Any

from .automatic import validation_coverage
from .common import Document, audit, check_revision, digest, encoded, lock, now, reject
from .contracts import output_hash
from .models import CaptureDefinition, SchemeDraft, VersionReference


def detail(db: Any, org: str, scheme: str) -> Document:
    row = db.execute(
        """SELECT revision,draft,latest_version,archived FROM ingest.capture_schemes
        WHERE organization_id=%s AND scheme_id=%s""",
        (org, scheme),
    ).fetchone()
    if row is None:
        reject("CAPTURE_SCHEME_NOT_FOUND", "采集方案不存在", 404)
    validations = db.execute(
        """SELECT document FROM ingest.capture_scheme_validations
        WHERE organization_id=%s AND scheme_id=%s AND revision=%s
        ORDER BY created_at DESC""",
        (org, scheme, row[0]),
    ).fetchall()
    versions = db.execute(
        """SELECT document FROM ingest.capture_scheme_versions
        WHERE organization_id=%s AND scheme_id=%s ORDER BY version DESC""",
        (org, scheme),
    ).fetchall()
    return {
        "scheme_id": scheme,
        "revision": row[0],
        "draft": row[1],
        "latest_version": row[2],
        "archived": row[3],
        "validations": [v[0] for v in validations],
        "versions": [v[0] for v in versions],
    }


def list_schemes(
    db: Any,
    org: str,
    *,
    query: str = "",
    source_format: str | None = None,
    include_archived: bool = False,
) -> list[Document]:
    rows = db.execute(
        """SELECT s.scheme_id,s.revision,s.draft,s.latest_version,s.archived,
        (SELECT count(*) FROM ingest.capture_scheme_links l WHERE
         l.organization_id=s.organization_id AND l.scheme_id=s.scheme_id AND l.active),
        (SELECT count(DISTINCT (p.project_id,p.collection_task_id))
         FROM collection_tasks.capture_plans p
         WHERE p.organization_id=s.organization_id AND p.scheme_id=s.scheme_id)
        FROM ingest.capture_schemes s WHERE s.organization_id=%s
        AND (%s OR NOT s.archived)
        AND (s.draft->>'name' ILIKE %s OR s.draft->>'description' ILIKE %s)
        ORDER BY lower(s.draft->>'name'),s.scheme_id""",
        (org, include_archived, "%" + query + "%", "%" + query + "%"),
    ).fetchall()
    return [
        {
            "scheme_id": r[0],
            "revision": r[1],
            "draft": r[2],
            "latest_version": r[3],
            "archived": r[4],
            "instance_count": r[5],
            "task_count": r[6],
        }
        for r in rows
        if source_format is None
        or any(a["source_format"] == source_format for a in r[2]["adapters"])
    ]


def save(db: Any, org: str, scheme: str, command: SchemeDraft, actor: str) -> Document:
    lock(db, "scheme", org, scheme)
    row = db.execute(
        "SELECT revision,draft,archived FROM ingest.capture_schemes "
        "WHERE organization_id=%s AND scheme_id=%s FOR UPDATE",
        (org, scheme),
    ).fetchone()
    document = command.model_dump(mode="json", exclude={"expected_revision"})
    if row and row[2]:
        reject("CAPTURE_SCHEME_ARCHIVED", "已归档方案不能编辑")
    # Lost-response replay is allowed only for the exact one-step transition.
    if row and row[0] == command.expected_revision + 1 and row[1] == document:
        return detail(db, org, scheme)
    check_revision(row[0] if row else 0, command.expected_revision)
    db.execute(
        """INSERT INTO ingest.capture_schemes (organization_id,scheme_id,revision,draft)
        VALUES(%s,%s,1,%s::jsonb) ON CONFLICT(organization_id,scheme_id) DO UPDATE
        SET revision=capture_schemes.revision+1,draft=EXCLUDED.draft,updated_at=now()""",
        (org, scheme, encoded(document)),
    )
    audit(
        db,
        org,
        scheme,
        actor,
        "SAVE_DRAFT",
        {
            "revision": command.expected_revision + 1,
            "before": row[1] if row else None,
            "after": document,
        },
    )
    return detail(db, org, scheme)


def record_validation(
    db: Any, org: str, scheme: str, revision: int, evidence: Document, actor: str
) -> Document:
    """Evidence comes only from the server's scoped sample decoder, never a request flag."""
    lock(db, "scheme", org, scheme)
    current = detail(db, org, scheme)
    check_revision(current["revision"], revision)
    if current["archived"]:
        reject("CAPTURE_SCHEME_ARCHIVED", "已归档方案不能验证")
    if evidence.get("draft_sha256") != digest(current["draft"]):
        reject("CAPTURE_REVISION_CONFLICT", "样本验证期间草稿已改变")
    identity = digest({"revision": revision, "evidence": evidence})
    document = {
        **evidence,
        "validation_id": identity,
        "revision": revision,
        "validated_at": now(),
        "actor": actor,
    }
    db.execute(
        """INSERT INTO ingest.capture_scheme_validations
        (organization_id,scheme_id,revision,source_format,validation_id,document)
        VALUES(%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
        (org, scheme, revision, evidence["source_format"], identity, encoded(document)),
    )
    audit(db, org, scheme, actor, "VALIDATE_SAMPLE", document)
    return detail(db, org, scheme)


def publish(db: Any, org: str, scheme: str, revision: int, actor: str) -> Document:
    lock(db, "scheme", org, scheme)
    current = detail(db, org, scheme)
    # Check the immutable receipt first: a retry remains a retry even after v2 is edited.
    previous = db.execute(
        """SELECT document FROM ingest.capture_scheme_versions
        WHERE organization_id=%s AND scheme_id=%s AND draft_revision=%s""",
        (org, scheme, revision),
    ).fetchone()
    if previous:
        return dict(previous[0])
    check_revision(current["revision"], revision)
    if current["archived"]:
        reject("CAPTURE_SCHEME_ARCHIVED", "已归档方案不能发布")
    document = current["draft"]
    content_hash = digest(document)
    evidence: dict[str, Document] = {}
    for item in current["validations"]:
        if (
            item["status"] == "PASSED"
            and item["draft_sha256"] == content_hash
            and item.get("sample_sha256")
            and item.get("parser_version")
        ):
            evidence.setdefault(item["source_format"], item)
    required = {adapter["source_format"] for adapter in document["adapters"]}
    covered = validation_coverage(document, evidence.keys())
    if required - covered:
        reject(
            "CAPTURE_SAMPLE_VALIDATION_REQUIRED",
            "请用实际样本验证当前方案"
            if document.get("input_mode") == "AUTO"
            else "请用实际样本验证当前草稿的每种输入格式",
            missing_formats=sorted(required - covered),
        )
    CaptureDefinition.model_validate(document)
    version = (current["latest_version"] or 0) + 1
    published = {
        **document,
        "scheme_id": scheme,
        "version": version,
        "draft_revision": revision,
        "content_sha256": content_hash,
        "output_contract_sha256": output_hash(document),
        "validations": evidence,
        "published_at": now(),
        "published_by": actor,
        "source": "SHARED_CAPTURE_SCHEME",
    }
    db.execute(
        """INSERT INTO ingest.capture_scheme_versions
        (organization_id,scheme_id,version,draft_revision,content_sha256,document)
        VALUES(%s,%s,%s,%s,%s,%s::jsonb)""",
        (org, scheme, version, revision, content_hash, encoded(published)),
    )
    db.execute(
        "UPDATE ingest.capture_schemes SET latest_version=%s,updated_at=now() "
        "WHERE organization_id=%s AND scheme_id=%s",
        (version, org, scheme),
    )
    audit(
        db,
        org,
        scheme,
        actor,
        "PUBLISH",
        {
            "version": version,
            "draft_revision": revision,
            "content_sha256": content_hash,
        },
    )
    return published


def published_version(db: Any, org: str, scheme: str, version: int) -> Document:
    """Read one immutable version, including archived versions, in the authorized tenant."""
    row = db.execute(
        """SELECT document FROM ingest.capture_scheme_versions
        WHERE organization_id=%s AND scheme_id=%s AND version=%s""",
        (org, scheme, version),
    ).fetchone()
    if row is None:
        reject("CAPTURE_VERSION_NOT_FOUND", "当前组织中没有该采集方案版本", 404)
    return dict(row[0])


def version(db: Any, org: str, reference: VersionReference, *, new_use: bool = False) -> Document:
    row = db.execute(
        """SELECT v.document,s.archived FROM ingest.capture_scheme_versions v
        JOIN ingest.capture_schemes s USING(organization_id,scheme_id)
        WHERE v.organization_id=%s AND v.scheme_id=%s AND v.version=%s""",
        (org, reference.scheme_id, reference.version),
    ).fetchone()
    if row is None or row[0]["content_sha256"] != reference.content_sha256:
        reject("CAPTURE_VERSION_MISMATCH", "采集方案版本或内容摘要不匹配")
    if new_use and row[1]:
        reject("CAPTURE_SCHEME_ARCHIVED", "已归档方案不可用于新关联或新任务")
    return dict(row[0])


def archive(db: Any, org: str, scheme: str, revision: int, actor: str) -> Document:
    lock(db, "scheme", org, scheme)
    current = detail(db, org, scheme)
    check_revision(current["revision"], revision)
    if not current["archived"]:
        db.execute(
            "UPDATE ingest.capture_schemes SET archived=true,updated_at=now() "
            "WHERE organization_id=%s AND scheme_id=%s",
            (org, scheme),
        )
        audit(db, org, scheme, actor, "ARCHIVE", {"revision": revision})
    return detail(db, org, scheme)


def references(db: Any, org: str, scheme: str) -> Document:
    detail(db, org, scheme)
    instances = db.execute(
        """SELECT robot_id,scheme_version,mapping_version,active,is_default
        FROM ingest.capture_scheme_links WHERE organization_id=%s AND scheme_id=%s
        ORDER BY robot_id""",
        (org, scheme),
    ).fetchall()
    # Project RLS filters this list to tasks the caller can read.
    tasks = db.execute(
        """SELECT p.project_id,p.collection_task_id,p.revision,p.scheme_version,h.locked_at
        FROM collection_tasks.capture_plans p JOIN collection_tasks.capture_plan_heads h
        USING(organization_id,project_id,collection_task_id,revision)
        WHERE p.organization_id=%s AND p.scheme_id=%s ORDER BY p.created_at""",
        (org, scheme),
    ).fetchall()
    return {
        "instances": [
            dict(
                zip(
                    ("robot_id", "version", "mapping_version", "active", "is_default"),
                    r,
                    strict=True,
                )
            )
            for r in instances
        ],
        "tasks": [
            dict(
                zip(
                    ("project_id", "collection_task_id", "revision", "version", "locked_at"),
                    r,
                    strict=True,
                )
            )
            for r in tasks
        ],
    }

"""Add shared drafts and immutable provenance without reinterpreting old recordings.

Legacy validation remains historical evidence. Every imported draft must pass the
current sample validator before publication or use by a new task.
"""

from typing import Any

from pydantic import ValidationError

from . import schemes
from .common import Document, audit, digest, encoded, lock, now, reject
from .models import SchemeDraft

_SOURCES = (
    ("INSTANCE_DRAFT", "robot_processing_schemes", ("robot_id", "scheme_id"), "draft"),
    (
        "INSTANCE_VERSION",
        "robot_processing_versions",
        ("robot_id", "scheme_id", "version"),
        "document",
    ),
    ("ORGANIZATION_TEMPLATE", "robot_processing_templates", ("template_id",), "document"),
)


def source_inventory(db: Any, organization_id: str, *, for_update: bool = False) -> list[Document]:
    result = []
    for kind, table, keys, content_field in _SOURCES:
        rows = db.execute(
            f"SELECT to_jsonb(source) FROM ingest.{table} source WHERE organization_id=%s"
            + (" FOR SHARE" if for_update else ""),
            (organization_id,),
        ).fetchall()
        for (row,) in rows:
            result.append(
                {
                    "legacy_kind": kind,
                    "legacy_key": encoded([row[key] for key in keys]),
                    "source_sha256": digest(row),
                    "source": row,
                    "content_field": content_field,
                }
            )
    return sorted(result, key=lambda item: (item["legacy_kind"], item["legacy_key"]))


def _candidate(source: Document) -> tuple[Document | None, list[str]]:
    row = source["source"]
    document = row[source["content_field"]]
    config = document.get("configuration", {})
    if (document.get("source_format") or row.get("source_format") or "MCAP") != "MCAP":
        return None, ["LEGACY_FORMAT_REQUIRES_EXPLICIT_CONVERSION"]
    if config.get("cameras") or config.get("collection_mode") != "JOINT_ONLY":
        return None, ["CAMERA_DTYPE_SHAPE_AND_MEDIA_REQUIRE_SAMPLE"]
    features = {}
    for role, name in (("state", "observation.state"), ("action", "action")):
        names = config.get(role + "_joint_names") or config.get("joint_names")
        units = config.get(role + "_joint_units") or config.get("joint_units")
        if not names or not units or len(names) != len(units):
            return None, ["AXIS_NAMES_AND_UNITS_REQUIRE_REVIEW"]
        if not config.get(role + "_topic") and not (
            role == "action" and config.get("derive_action_from_next_state")
        ):
            return None, ["EXPLICIT_STATE_AND_ACTION_SOURCES_REQUIRED"]
        features[name] = {
            "dtype": "float32",  # The legacy native output contract, not inferred source dtype.
            "shape": [len(names)],
            "names": names,
            "units": units,
            "role": role,
        }
    model = document.get("model_version_id")
    try:
        draft = SchemeDraft.model_validate(
            {
                "expected_revision": 0,
                "name": document["name"],
                "description": "从旧采集配置迁入的候选草稿；发布前需要重新验证实际样本。",
                "software": document["software"],
                "model_version_ids": [model] if model else [],
                "adapters": [{"source_format": "MCAP", "configuration": config}],
                "output": {
                    "frequency_hz": config.get("fps", 30),
                    "frequency_policy": "PRESERVE",
                    "time_source": config.get("time_source", "RGB_LOG_TIME"),
                    "features": features,
                },
            }
        )
    except (KeyError, ValidationError) as exc:
        return None, ["LEGACY_DEFINITION_REQUIRES_REVIEW", str(exc)]
    return draft.model_dump(mode="json", exclude={"expected_revision"}), []


def migration_plan(db: Any, organization_id: str) -> Document:
    return plan_from_inventory(organization_id, source_inventory(db, organization_id))


def plan_from_inventory(organization_id: str, sources: list[Document]) -> Document:
    """Build the same review plan from an exact, read-only source-row inventory."""
    entries = []
    for source in sources:
        if (
            source["source"].get("organization_id") != organization_id
            or digest(source["source"]) != source["source_sha256"]
        ):
            reject("CAPTURE_MIGRATION_PLAN_INVALID", "旧来源清单的组织或摘要不匹配。")
        candidate, blockers = _candidate(source)
        entries.append(
            {
                **source,
                "candidate": candidate,
                "candidate_sha256": digest(candidate) if candidate is not None else None,
                "scheme_id": "legacy-" + digest(candidate)[:32] if candidate is not None else None,
                "action": "IMPORT_DRAFT" if candidate is not None else "NEEDS_REVIEW",
                "blockers": blockers,
                "remaining": [
                    "VALIDATE_REAL_SAMPLE",
                    "VERIFY_INSTANCE_MAPPING",
                    "EXPLICIT_TASK_BINDING",
                ],
            }
        )
    body = {
        "schema_version": "capture-legacy-migration/v1",
        "organization_id": organization_id,
        "entries": entries,
        "preserves": [
            "legacy_versions",
            "legacy_audit",
            "upload_snapshots",
            "raw_sources",
            "successful_episodes",
        ],
    }
    return {**body, "plan_sha256": digest(body)}


def apply_migration(db: Any, organization_id: str, plan: Document, actor: str) -> Document:
    """Caller owns the transaction. A changed plan or source rejects the entire batch."""
    if (
        plan.get("schema_version") != "capture-legacy-migration/v1"
        or plan.get("organization_id") != organization_id
        or digest({k: v for k, v in plan.items() if k != "plan_sha256"}) != plan.get("plan_sha256")
    ):
        reject("CAPTURE_MIGRATION_PLAN_INVALID", "迁移清单范围或摘要不匹配。")
    lock(db, "legacy-migration", organization_id)
    source_inventory(db, organization_id, for_update=True)
    if migration_plan(db, organization_id) != plan:
        reject("CAPTURE_MIGRATION_SOURCE_CHANGED", "旧配置或转换合同已变化，请重新生成并核对清单。")
    imported, replayed, pending = [], [], []
    for entry in plan["entries"]:
        if entry["action"] != "IMPORT_DRAFT":
            pending.append(
                {
                    "legacy_kind": entry["legacy_kind"],
                    "legacy_key": entry["legacy_key"],
                    "blockers": entry["blockers"],
                }
            )
            continue
        key = (organization_id, entry["legacy_kind"], entry["legacy_key"], entry["source_sha256"])
        existing = db.execute(
            """SELECT scheme_id,document FROM ingest.capture_legacy_references
            WHERE organization_id=%s AND legacy_kind=%s AND legacy_key=%s AND source_sha256=%s""",
            key,
        ).fetchone()
        if existing:
            if (
                existing[0] != entry["scheme_id"]
                or existing[1].get("candidate_sha256") != entry["candidate_sha256"]
            ):
                reject("CAPTURE_MIGRATION_REFERENCE_CONFLICT", "旧引用已迁移到不同定义，不能覆盖。")
            # Preserve later edits or publication of the imported shared draft.
            replayed.append(entry["scheme_id"])
            continue
        lock(db, "scheme", organization_id, entry["scheme_id"])
        shared = db.execute(
            "SELECT draft FROM ingest.capture_schemes WHERE organization_id=%s AND scheme_id=%s",
            (organization_id, entry["scheme_id"]),
        ).fetchone()
        if shared and shared[0] != entry["candidate"]:
            reject("CAPTURE_MIGRATION_REFERENCE_CONFLICT", "候选目标已存在不同草稿，不能覆盖。")
        if not shared:
            schemes.save(
                db,
                organization_id,
                entry["scheme_id"],
                SchemeDraft.model_validate({"expected_revision": 0, **entry["candidate"]}),
                actor,
            )
        provenance = {
            "schema_version": "capture-legacy-reference/v1",
            "source": entry["source"],
            "source_sha256": entry["source_sha256"],
            "candidate_sha256": entry["candidate_sha256"],
            "status": "DRAFT_REQUIRES_VALIDATION",
            "migrated_by": actor,
            "migrated_at": now(),
        }
        db.execute(
            """INSERT INTO ingest.capture_legacy_references
            (organization_id,legacy_kind,legacy_key,source_sha256,scheme_id,scheme_version,document)
            VALUES(%s,%s,%s,%s,%s,NULL,%s::jsonb)""",
            (*key, entry["scheme_id"], encoded(provenance)),
        )
        audit(
            db,
            organization_id,
            entry["scheme_id"],
            actor,
            "MIGRATE_LEGACY_REFERENCE",
            {
                "legacy_kind": entry["legacy_kind"],
                "legacy_key": entry["legacy_key"],
                "source_sha256": entry["source_sha256"],
                "plan_sha256": plan["plan_sha256"],
            },
        )
        imported.append(entry["scheme_id"])
    return {"imported": imported, "replayed": replayed, "needs_review": pending}

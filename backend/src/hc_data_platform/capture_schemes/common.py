from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, NoReturn

from hc_data_platform.core.errors import problem
from hc_data_platform.security.audit import canonical_hash

Document = dict[str, Any]


def reject(code: str, detail: str, status: int = 409, **details: Any) -> NoReturn:
    raise problem(status=status, code=code, title=detail, detail=detail, details=details)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encoded(document: Any) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(document: Any) -> str:
    return canonical_hash(document)


def lock(db: Any, kind: str, *identity: str) -> None:
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (encoded(["capture-schemes", kind, *identity]),),
    )


def audit(db: Any, org: str, scheme: str, actor: str, action: str, document: Document) -> None:
    db.execute(
        """INSERT INTO ingest.capture_scheme_audit
        (organization_id,scheme_id,actor,action,document) VALUES(%s,%s,%s,%s,%s::jsonb)""",
        (org, scheme, actor, action, encoded(document)),
    )


def check_revision(actual: int, expected: int) -> None:
    if actual != expected:
        reject(
            "CAPTURE_REVISION_CONFLICT",
            "内容已被修改，请刷新后比较再保存",
            expected_revision=expected,
            actual_revision=actual,
        )

"""Append-only, transactional database merge for quiescent Compose installations.

This is deliberately separate from whole-platform disaster recovery. It imports business
facts into an existing installation without adopting source sessions or deployment state.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from psycopg import Connection, sql
from psycopg.types.json import Jsonb


class MergeError(RuntimeError):
    def __init__(self, code: str, *, table: str | None = None) -> None:
        self.code = code
        self.table = table
        super().__init__(code)


ARCHIVE_ONLY = frozenset(
    {
        "core.schema_migrations",
        "core.audit_integrity_entries",
        "core.audit_integrity_heads",
        "access_control.sessions",
        "access_control.account_security_challenges",
        "access_control.auth_login_states",
        "access_control.auth_rate_limit_buckets",
        "access_control.command_idempotency",
        "aligned_media.media_capacity_slots",
        "preview.media_capacity_slots",
        "preview.sessions",
    }
)
RENUMBER = {
    "registry.audit_events": "event_id",
    "robotics.robot_asset_audit_events": "event_id",
    "storage.lifecycle_execution_logs": "sequence",
}
RECEIPT_ACTOR = "system:portable-merge"
RECEIPT_OPERATION = "platform.merge.import.v1"
MAX_ROW_BYTES = 32 * 1024 * 1024


def imported_table(name: str) -> bool:
    return not name.startswith("platform.") and name not in ARCHIVE_ONLY


def relation(name: str) -> sql.Identifier:
    return sql.Identifier(*name.split(".", 1))


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _one(cursor: Any) -> tuple[Any, ...]:
    row = cursor.fetchone()
    if row is None:
        raise MergeError("MERGE_CATALOG_ROW_MISSING")
    return cast(tuple[Any, ...], row)


def schema_description(connection: Connection[Any]) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    rows = connection.execute(
        """SELECT n.nspname || '.' || c.relname,
                  a.attname, format_type(a.atttypid, a.atttypmod),
                  a.attnotnull, a.attidentity, a.attgenerated,
                  pg_get_expr(d.adbin, d.adrelid)
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
             LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE c.relkind='r' AND n.nspname <> 'information_schema'
              AND n.nspname !~ '^pg_'
            ORDER BY n.nspname,c.relname,a.attnum"""
    )
    for name, column, kind, required, identity, generated, default in rows:
        table = tables.setdefault(name, {"columns": [], "constraints": [], "indexes": [], "pk": []})
        table["columns"].append(
            {
                "name": column,
                "type": kind,
                "required": required,
                "identity": identity,
                "generated": generated,
                "default": default,
            }
        )
    for name, constraint, definition, kind, keys in connection.execute(
        """SELECT n.nspname||'.'||c.relname, x.conname, pg_get_constraintdef(x.oid),
                  x.contype, ARRAY(SELECT a.attname FROM unnest(x.conkey) WITH ORDINALITY k(num,ord)
                      JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num ORDER BY k.ord)
             FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid
             JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
            ORDER BY n.nspname,c.relname,x.conname"""
    ):
        if name in tables:
            tables[name]["constraints"].append([constraint, definition])
            if kind == "p":
                tables[name]["pk"] = keys
    for schema, name, definition in connection.execute(
        """SELECT schemaname,tablename,indexdef FROM pg_indexes
            WHERE schemaname <> 'information_schema' AND schemaname !~ '^pg_'
            ORDER BY schemaname,tablename,indexname"""
    ):
        if f"{schema}.{name}" in tables:
            tables[f"{schema}.{name}"]["indexes"].append(definition)
    migrations = list(
        connection.execute(
            "SELECT version,checksum_sha256 FROM core.schema_migrations ORDER BY version"
        )
    )
    # JSON round-trip removes tuples, making archive and live descriptions comparable.
    return cast(dict[str, Any], json.loads(canonical({"tables": tables, "migrations": migrations})))


def schema_digest(description: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(description)).hexdigest()


def lock_tables(connection: Connection[Any], tables: dict[str, Any], *, writing: bool) -> None:
    connection.execute("SET LOCAL lock_timeout='15s'")
    # A deterministic order also serializes competing imports. PostgreSQL locks, not a
    # client flag, protect the snapshot and prevent target drift while files are copied.
    names = sql.SQL(",").join(relation(name) for name in sorted(tables))
    mode = sql.SQL("EXCLUSIVE" if writing else "SHARE")
    connection.execute(sql.SQL("LOCK TABLE {} IN {} MODE").format(names, mode))


def dump_rows(connection: Connection[Any], name: str, path: Path) -> int:
    count = 0
    with connection.cursor(name="merge_export_rows") as cursor:
        cursor.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t").format(relation(name)))
        with gzip.open(path, "wb", compresslevel=1) as output:
            for (document,) in cursor:
                raw = document.encode("utf-8")
                if len(raw) > MAX_ROW_BYTES:
                    raise MergeError("MERGE_ROW_TOO_LARGE", table=name)
                output.write(raw + b"\n")
                count += 1
    return count


def _stage(connection: Connection[Any], name: str, path: Path, index: int, count: int) -> str:
    raw_name = f"merge_raw_{index}"
    stage = f"merge_stage_{index}"
    connection.execute(
        sql.SQL("CREATE TEMP TABLE {} (document jsonb) ON COMMIT DROP").format(
            sql.Identifier(raw_name)
        )
    )
    observed = 0
    with (
        connection.cursor() as cursor,
        cursor.copy(
            sql.SQL("COPY {} (document) FROM STDIN").format(sql.Identifier(raw_name))
        ) as copy,
        gzip.open(path, "rb") as source,
    ):
        while line := source.readline(MAX_ROW_BYTES + 2):
            if len(line) > MAX_ROW_BYTES + 1 or not line.endswith(b"\n"):
                raise MergeError("MERGE_ROW_TOO_LARGE", table=name)
            copy.write_row((line.decode("utf-8"),))
            observed += 1
    if observed != count:
        raise MergeError("MERGE_ROW_COUNT_MISMATCH", table=name)
    connection.execute(
        sql.SQL(
            "CREATE TEMP TABLE {} ON COMMIT DROP AS SELECT r.* FROM {} d "
            "CROSS JOIN LATERAL jsonb_populate_record(NULL::{}, d.document) r"
        ).format(sql.Identifier(stage), sql.Identifier(raw_name), relation(name))
    )
    connection.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(raw_name)))
    return stage


def _join(keys: list[str]) -> sql.Composed:
    return sql.SQL(" AND ").join(
        sql.SQL("t.{} IS NOT DISTINCT FROM s.{}").format(sql.Identifier(k), sql.Identifier(k))
        for k in keys
    )


def _accounts(connection: Connection[Any], stage: str) -> dict[str, Any]:
    target = relation("access_control.accounts")
    incoming = sql.Identifier(stage)
    mismatch = connection.execute(
        sql.SQL(
            "SELECT 1 FROM {} s JOIN {} t USING(principal_id) "
            "WHERE s.canonical_username <> t.canonical_username AND "
            "t.canonical_username <> left(s.canonical_username,40) || '_import_' || "
            "left(replace(s.principal_id::text,'-',''),12) LIMIT 1"
        ).format(incoming, target)
    ).fetchone()
    if mismatch:
        raise MergeError("MERGE_ACCOUNT_IDENTITY_CONFLICT", table="access_control.accounts")
    # Keep the entire target identity, including password, disabled state and recovery
    # settings. Preserve target grants separately, even when a source grant is missing here.
    connection.execute(
        sql.SQL(
            "CREATE TEMP TABLE merge_existing_accounts ON COMMIT DROP AS "
            "SELECT principal_id FROM {}"
        ).format(target)
    )
    kept = connection.execute(
        sql.SQL("DELETE FROM {} s USING {} t WHERE s.principal_id=t.principal_id").format(
            incoming, target
        )
    ).rowcount
    renamed = []
    collisions = connection.execute(
        sql.SQL(
            "SELECT s.principal_id::text,s.canonical_username FROM {} s "
            "JOIN {} t USING(canonical_username) ORDER BY s.principal_id"
        ).format(incoming, target)
    ).fetchall()
    for principal, old in collisions:
        # A stable, bounded suffix changes the login name, not the identity or historical
        # actor references. Never silently attach a source account to someone else's login.
        username = old[:40] + "_import_" + principal.replace("-", "")[:12]
        occupied = connection.execute(
            sql.SQL(
                "SELECT 1 FROM {} WHERE canonical_username=%s UNION ALL "
                "SELECT 1 FROM {} WHERE canonical_username=%s LIMIT 1"
            ).format(target, incoming),
            (username, username),
        ).fetchone()
        if occupied:
            raise MergeError("MERGE_ACCOUNT_RENAME_CONFLICT", table="access_control.accounts")
        connection.execute(
            sql.SQL(
                "UPDATE {} SET canonical_username=%s,display_username=%s "
                "WHERE principal_id=%s::uuid"
            ).format(incoming),
            (username, username, principal),
        )
        renamed.append({"principal_id": principal, "username": username})
    return {"kept_target_accounts": kept, "renamed_accounts": renamed}


def _append(connection: Connection[Any], name: str, table: dict[str, Any], stage: str) -> int:
    target, source = relation(name), sql.Identifier(stage)
    keys = table["pk"]
    if not keys:
        raise MergeError("MERGE_PRIMARY_KEY_REQUIRED", table=name)
    if name == "access_control.platform_capability_grants":
        connection.execute(
            sql.SQL(
                "DELETE FROM {} s USING merge_existing_accounts t "
                "WHERE s.principal_id=t.principal_id"
            ).format(source)
        )
    identity = RENUMBER.get(name)
    if name == "registry.organization_projects":
        # Organization/project IDs are the stable scope. Keep the target's display name;
        # imported datasets, revisions and audit events retain that same explicit scope.
        connection.execute(
            sql.SQL("DELETE FROM {} s USING {} t WHERE {}").format(source, target, _join(keys))
        )
    elif identity:
        # These append-only logs have no foreign-key consumers. Retain payloads but give
        # them fresh target numbers; content matching makes a second export idempotent too.
        if _identity_referenced(connection, name, identity):
            raise MergeError("MERGE_REFERENCED_SEQUENCE_UNSUPPORTED", table=name)
        connection.execute(
            sql.SQL(
                "DELETE FROM {} s USING {} t WHERE "
                "md5((to_jsonb(t)-%s)::text)=md5((to_jsonb(s)-%s)::text) "
                "AND to_jsonb(t)-%s=to_jsonb(s)-%s"
            ).format(source, target),
            (identity,) * 4,
        )
        connection.execute(
            sql.SQL(
                "WITH numbered AS (SELECT ctid,row_number() OVER (ORDER BY {}) + "
                "(SELECT COALESCE(max({}),0) FROM {}) AS new_id FROM {}) "
                "UPDATE {} s SET {}=n.new_id FROM numbered n WHERE s.ctid=n.ctid"
            ).format(
                sql.Identifier(identity),
                sql.Identifier(identity),
                target,
                source,
                source,
                sql.Identifier(identity),
            )
        )
    else:
        mismatch = connection.execute(
            sql.SQL(
                "SELECT 1 FROM {} s JOIN {} t ON {} WHERE to_jsonb(s)<>to_jsonb(t) LIMIT 1"
            ).format(source, target, _join(keys))
        ).fetchone()
        if mismatch:
            raise MergeError("MERGE_ROW_CONFLICT", table=name)
        connection.execute(
            sql.SQL("DELETE FROM {} s USING {} t WHERE {}").format(source, target, _join(keys))
        )
    columns = sql.SQL(",").join(
        sql.Identifier(c["name"]) for c in table["columns"] if not c["generated"]
    )
    return connection.execute(
        sql.SQL("INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE SELECT {} FROM {}").format(
            target, columns, columns, source
        )
    ).rowcount


def _identity_referenced(connection: Connection[Any], name: str, column: str) -> bool:
    return (
        connection.execute(
            """SELECT 1 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.confrelid
            AND a.attnum=ANY(c.confkey) WHERE c.contype='f'
            AND c.confrelid=%s::regclass AND a.attname=%s LIMIT 1""",
            (name, column),
        ).fetchone()
        is not None
    )


def validate_foreign_keys(connection: Connection[Any]) -> None:
    constraints = connection.execute(
        """SELECT ns.nspname||'.'||src.relname, nt.nspname||'.'||dst.relname,
                  ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(num,ord)
                    JOIN pg_attribute a ON a.attrelid=src.oid AND a.attnum=k.num ORDER BY k.ord),
                  ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(num,ord)
                    JOIN pg_attribute a ON a.attrelid=dst.oid AND a.attnum=k.num ORDER BY k.ord),
                  c.confmatchtype
             FROM pg_constraint c JOIN pg_class src ON src.oid=c.conrelid
             JOIN pg_namespace ns ON ns.oid=src.relnamespace
             JOIN pg_class dst ON dst.oid=c.confrelid
             JOIN pg_namespace nt ON nt.oid=dst.relnamespace
            WHERE c.contype='f' AND ns.nspname !~ '^pg_'"""
    ).fetchall()
    for source, target, source_keys, target_keys, match_type in constraints:
        equal = sql.SQL(" AND ").join(
            sql.SQL("s.{}=t.{}").format(sql.Identifier(a), sql.Identifier(b))
            for a, b in zip(source_keys, target_keys, strict=True)
        )
        present = sql.SQL(" AND ").join(
            sql.SQL("s.{} IS NOT NULL").format(sql.Identifier(k)) for k in source_keys
        )
        invalid = sql.SQL("({}) AND NOT EXISTS (SELECT 1 FROM {} t WHERE {})").format(
            present, relation(target), equal
        )
        if match_type == "f":
            any_present = sql.SQL(" OR ").join(
                sql.SQL("s.{} IS NOT NULL").format(sql.Identifier(k)) for k in source_keys
            )
            invalid = sql.SQL("({}) OR (({}) AND NOT ({}))").format(invalid, any_present, present)
        if connection.execute(
            sql.SQL("SELECT 1 FROM {} s WHERE {} LIMIT 1").format(relation(source), invalid)
        ).fetchone():
            raise MergeError("MERGE_FOREIGN_KEY_MISMATCH", table=source)


def _finish_audits(connection: Connection[Any]) -> None:
    # Retain target chains and append imported events through the normal hash functions.
    # Original source chains remain in the checksum-verified transfer archive.
    connection.execute("""SELECT core.append_audit_integrity_entry(audit_id)
        FROM (SELECT a.audit_id FROM core.audit_events a WHERE NOT EXISTS (
                SELECT 1 FROM core.audit_integrity_entries i WHERE i.audit_id=a.audit_id)
              ORDER BY a.occurred_at,a.audit_id) missing""")
    connection.execute("""SELECT platform.append_platform_audit_integrity_entry(event_id)
        FROM (SELECT a.event_id FROM access_control.audit_events a
              WHERE scope_kind='PLATFORM' AND NOT EXISTS (
                SELECT 1 FROM platform.platform_audit_integrity_entries i
                WHERE i.event_id=a.event_id) ORDER BY a.occurred_at,a.event_id) missing""")
    validate_audit_chains(connection)


def validate_audit_chains(connection: Connection[Any]) -> None:
    """Read-only validation; reject damaged chains before adding imported events."""
    for events, entries, identity, where in (
        ("core.audit_events", "core.audit_integrity_entries", "audit_id", "true"),
        (
            "access_control.audit_events",
            "platform.platform_audit_integrity_entries",
            "event_id",
            "a.scope_kind='PLATFORM'",
        ),
    ):
        if connection.execute(
            sql.SQL(
                "SELECT 1 FROM {} a WHERE {} AND NOT EXISTS "
                "(SELECT 1 FROM {} i WHERE i.{}=a.{}) LIMIT 1"
            ).format(
                relation(events),
                sql.SQL(where),
                relation(entries),
                sql.Identifier(identity),
                sql.Identifier(identity),
            )
        ).fetchone():
            raise MergeError("MERGE_AUDIT_CHAIN_INCOMPLETE")
    invalid = connection.execute("""
        WITH entries AS (
          SELECT i.*, lag(event_hash) OVER (ORDER BY sequence_no) AS expected_previous,
                 row_number() OVER (ORDER BY sequence_no) AS expected_sequence
          FROM platform.platform_audit_integrity_entries i
        ) SELECT 1 FROM entries i JOIN access_control.audit_events a USING(event_id)
          WHERE sequence_no<>expected_sequence
             OR previous_event_hash IS DISTINCT FROM expected_previous
             OR event_hash<>platform.platform_audit_event_hash(previous_event_hash,a.event_id,
                  a.actor_id,a.action,a.resource_type,a.resource_id,a.request_id,
                  a.outcome,a.safe_details,a.occurred_at) LIMIT 1
    """).fetchone()
    if invalid:
        raise MergeError("MERGE_AUDIT_CHAIN_INVALID")
    invalid = connection.execute("""
        WITH entries AS (
          SELECT i.*, lag(event_hash) OVER (
                   PARTITION BY organization_id,project_id,region_code ORDER BY sequence_no
                 ) AS expected_previous,
                 row_number() OVER (
                   PARTITION BY organization_id,project_id,region_code ORDER BY sequence_no
                 ) AS expected_sequence FROM core.audit_integrity_entries i
        ) SELECT 1 FROM entries i JOIN core.audit_events a USING(audit_id)
          WHERE sequence_no<>expected_sequence
             OR previous_event_hash IS DISTINCT FROM expected_previous
             OR i.organization_id<>a.organization_id OR i.project_id<>a.project_id
             OR i.region_code<>a.region_code
             OR event_hash<>core.audit_integrity_event_hash(previous_event_hash,a.audit_id,
                  a.organization_id,a.project_id,a.region_code,a.actor_id,a.action,a.resource_type,
                  a.resource_id,a.request_id,a.before_hash,a.after_hash,a.details,a.occurred_at)
          LIMIT 1
    """).fetchone()
    if invalid:
        raise MergeError("MERGE_AUDIT_CHAIN_INVALID")
    for heads, entries, keys in (
        ("platform.platform_audit_integrity_head", "platform.platform_audit_integrity_entries", []),
        (
            "core.audit_integrity_heads",
            "core.audit_integrity_entries",
            ["organization_id", "project_id", "region_code"],
        ),
    ):
        scope_where: sql.Composable = sql.SQL(" AND ").join(
            sql.SQL("i.{}=h.{}").format(sql.Identifier(k), sql.Identifier(k)) for k in keys
        )
        if not keys:
            scope_where = sql.SQL("true")
        if connection.execute(
            sql.SQL(
                "SELECT 1 FROM {} h LEFT JOIN LATERAL (SELECT sequence_no,event_hash "
                "FROM {} i WHERE {} ORDER BY sequence_no DESC LIMIT 1) tail ON true "
                "WHERE h.last_sequence<>COALESCE(tail.sequence_no,0) "
                "OR h.last_event_hash IS DISTINCT FROM tail.event_hash LIMIT 1"
            ).format(relation(heads), relation(entries), scope_where)
        ).fetchone():
            raise MergeError("MERGE_AUDIT_HEAD_INVALID")
        if connection.execute(
            sql.SQL(
                "SELECT 1 FROM {} i WHERE NOT EXISTS (SELECT 1 FROM {} h WHERE {}) LIMIT 1"
            ).format(relation(entries), relation(heads), scope_where)
        ).fetchone():
            raise MergeError("MERGE_AUDIT_HEAD_INVALID")


def _sequences(connection: Connection[Any], description: dict[str, Any]) -> None:
    for name, table in description["tables"].items():
        if not imported_table(name):
            continue
        for column in table["columns"]:
            if not column["identity"] and not str(column["default"]).startswith("nextval("):
                continue
            sequence = _one(
                connection.execute("SELECT pg_get_serial_sequence(%s,%s)", (name, column["name"]))
            )[0]
            if not sequence:
                continue
            # ALTER ... RESTART is transactional, unlike setval/nextval. A dry run or
            # failed import therefore also rolls back every sequence change.
            parts = _one(
                connection.execute(
                    "SELECT n.nspname,c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE c.oid=%s::regclass",
                    (sequence,),
                )
            )
            ident = sql.Identifier(*parts)
            previous = _one(
                connection.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(ident))
            )
            maximum = connection.execute(
                sql.SQL("SELECT COALESCE(max({}),0) FROM {}").format(
                    sql.Identifier(column["name"]), relation(name)
                )
            )
            next_value = max(_one(maximum)[0] + 1, previous[0] + int(previous[1]))
            connection.execute(
                sql.SQL("ALTER SEQUENCE {} RESTART WITH {}").format(ident, sql.Literal(next_value))
            )


def merge_database(
    connection: Connection[Any],
    bundle: Path,
    manifest: dict[str, Any],
    *,
    dry_run: bool,
    before_commit: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """All target row/sequence mutations share one transaction; caller uses autocommit."""
    if not connection.autocommit:
        raise ValueError("merge requires an autocommit connection with explicit transactions")
    with connection.transaction(force_rollback=dry_run):
        description = schema_description(connection)
        if schema_digest(description) != manifest["schema_sha256"]:
            raise MergeError("MERGE_SCHEMA_MISMATCH")
        if not _one(connection.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"))[
            0
        ]:
            raise MergeError("MERGE_MAINTENANCE_ROLE_REQUIRED")
        lock_tables(connection, description["tables"], writing=True)
        validate_audit_chains(connection)
        receipt = connection.execute(
            "SELECT result_payload FROM access_control.command_idempotency WHERE actor_id=%s "
            "AND operation=%s AND idempotency_key=%s",
            (RECEIPT_ACTOR, RECEIPT_OPERATION, manifest["bundle_id"]),
        ).fetchone()
        if receipt:
            if receipt[0]["manifest_sha256"] != hashlib.sha256(canonical(manifest)).hexdigest():
                raise MergeError("MERGE_BUNDLE_ID_REUSED")
            return {**receipt[0], "status": "already_imported"}
        report: dict[str, Any] = {
            "status": "planned" if dry_run else "imported",
            "bundle_id": manifest["bundle_id"],
            "inserted": {},
            "archived_only": {},
            "manifest_sha256": hashlib.sha256(canonical(manifest)).hexdigest(),
        }
        stages = {}
        for index, (name, info) in enumerate(sorted(manifest["tables"].items())):
            if not imported_table(name):
                report["archived_only"][name] = info["rows"]
                continue
            stages[name] = _stage(connection, name, bundle / info["path"], index, info["rows"])
        report.update(_accounts(connection, stages["access_control.accounts"]))
        # Snapshot rows include derived facts; replaying runtime triggers would mutate
        # target state or double-reserve quota. Unique/check/not-null constraints remain
        # active; every FK is explicitly verified before commit below.
        connection.execute("SET LOCAL session_replication_role=replica")
        for name, stage in stages.items():
            report["inserted"][name] = _append(connection, name, description["tables"][name], stage)
        connection.execute("SET LOCAL session_replication_role=origin")
        _finish_audits(connection)
        validate_foreign_keys(connection)
        _sequences(connection, description)
        report["project_count"] = _one(
            connection.execute("SELECT count(*) FROM registry.organization_projects")
        )[0]
        report["dataset_count"] = _one(
            connection.execute("SELECT count(*) FROM dataset_registry.datasets")
        )[0]
        if not dry_run:
            if before_commit:
                before_commit()
            connection.execute(
                "INSERT INTO access_control.command_idempotency "
                "(actor_id,operation,idempotency_key,request_fingerprint,result_payload) "
                "VALUES (%s,%s,%s,%s,%s)",
                (
                    RECEIPT_ACTOR,
                    RECEIPT_OPERATION,
                    manifest["bundle_id"],
                    report["manifest_sha256"],
                    Jsonb(report),
                ),
            )
        return report

"""Resumable task purge: freeze writers, detach facts, delete objects, finish.

The durable deletion manifest survives object-store failures. A failed purge
leaves a cancelled task visible, so the same delete action can resume cleanup.
No object is removed until the complete relational deletion has committed.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from psycopg import sql
from pydantic import BaseModel

from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.security.versioning import ResourceVersion

from .deletion_plan import TABLES, build_plan, conflict
from .repository import task_not_found


class TaskDeletionResult(BaseModel):
    collection_task_id: str
    task_code: str
    status: str = "DELETED"
    deleted_rows: int
    deleted_objects: int
    deleted_bytes: int


class TaskObjectPurger:
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    def list(self, prefix):
        if not prefix or prefix.count("/") < 2 or not prefix.endswith("/"):
            raise ValueError("refusing an unbounded object prefix")
        args = {"Bucket": self.bucket, "Prefix": prefix}
        while True:
            page = self.client.list_objects_v2(**args)
            for item in page.get("Contents", []):
                yield item["Key"], item["Size"]
            if not page.get("IsTruncated"):
                break
            args["ContinuationToken"] = page["NextContinuationToken"]

    def size(self, key):
        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                return None
            raise

    def purge(self, manifest):
        for key, upload in manifest["multiparts"]:
            try:
                self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload)
            except Exception as exc:
                if getattr(exc, "response", {}).get("Error", {}).get("Code") != "NoSuchUpload":
                    raise
        # Versioned stores need version-aware deletion; never report reclaimed
        # bytes when a provider has only created delete markers.
        if hasattr(self.client, "get_bucket_versioning"):
            state = self.client.get_bucket_versioning(Bucket=self.bucket).get("Status")
            if state in {"Enabled", "Suspended"}:
                raise conflict(
                    "TASK_DELETE_VERSIONED_STORAGE",
                    "当前存储启用了历史版本，需配置版本清理后重试。",
                )

        def remove(key):
            self.client.delete_object(Bucket=self.bucket, Key=key)
            if self.size(key) is not None:
                raise RuntimeError("object deletion was not confirmed")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(remove, manifest["objects"]))


class PostgresTaskDeletion:
    def __init__(self, connections, objects):
        self.connections, self.objects = connections, objects

    def delete(self, scope, task_id, if_match, idempotency_key):
        org, project, region = scope
        connection = self.connections()
        try:
            # A session lock serializes resume calls across the DB/object-store
            # boundary, without holding table locks while deleting bytes.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s,0))",
                    (f"task-delete:{org}:{project}:{task_id}",),
                )
                connection.commit()
                cursor.execute(
                    """SELECT manifest, result, state FROM collection_tasks.deletions
                    WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s
                      AND region_code=%s""",
                    (org, project, task_id, region),
                )
                previous = cursor.fetchone()
                if previous:
                    manifest, result, state = previous
                    if state == "DELETED":
                        return TaskDeletionResult.model_validate(result)
                else:
                    # Plan and detach under short-lived table write locks. This
                    # closes the race between dependency discovery and new uploads.
                    cursor.execute("SET LOCAL lock_timeout='5s'")
                    cursor.execute(
                        sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
                            sql.SQL(",").join(sql.Identifier(*t.split(".")) for t in sorted(TABLES))
                        )
                    )
                    cursor.execute(
                        """SELECT to_jsonb(t) FROM collection_tasks.collection_tasks t
                        WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s
                        FOR UPDATE""",
                        (org, project, task_id),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise task_not_found()
                    task = row[0]
                    if task["upload_region_code"] != region:
                        raise task_not_found()
                    ResourceVersion(task["version"]).require(if_match)
                    plan = build_plan(cursor, task, scope, self.objects.bucket, self.objects)
                    result = TaskDeletionResult(
                        collection_task_id=task_id,
                        task_code=task["task_code"],
                        deleted_rows=sum(plan.counts.values()),
                        deleted_objects=plan.present_objects,
                        deleted_bytes=sum(plan.objects.values()),
                    ).model_dump()
                    manifest = {
                        "objects": plan.objects,
                        "multiparts": plan.multiparts,
                        "counts": plan.counts,
                    }
                    cursor.execute(
                        """UPDATE collection_tasks.collection_tasks SET status='CANCELLED'
                        WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
                        (org, project, task_id),
                    )
                    cursor.execute(
                        """INSERT INTO collection_tasks.deletions
                        (organization_id,project_id,region_code,collection_task_id,idempotency_key,
                         state,manifest,result,requested_by)
                        VALUES (%s,%s,%s,%s,%s,'DELETING',%s::jsonb,%s::jsonb,%s)""",
                        (
                            org,
                            project,
                            region,
                            task_id,
                            idempotency_key,
                            json.dumps(manifest),
                            json.dumps(result),
                            current_request_context().subject_id,
                        ),
                    )
                    for table, selectors in plan.selectors.items():
                        cursor.executemany(
                            """INSERT INTO collection_tasks.deletion_targets
                            (organization_id,project_id,collection_task_id,relation,selector)
                            VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                            [(org, project, task_id, table, json.dumps(s)) for s in selectors],
                        )
                    cursor.execute(
                        "SELECT set_config('app.collection_task_purge_id',%s,true)", (task_id,)
                    )
                    cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                    cursor.execute("""UPDATE episode_store.bindings t
                        SET current_data_version_id=NULL
                        WHERE collection_tasks.purge_permitted(
                            'episode_store.bindings',to_jsonb(t))""")
                    for table in deletion_order(cursor):
                        if table == "collection_tasks.collection_tasks":
                            continue
                        cursor.execute(
                            sql.SQL(
                                "DELETE FROM {} t WHERE "
                                "collection_tasks.purge_permitted(%s,to_jsonb(t))"
                            ).format(sql.Identifier(*table.split("."))),
                            (table,),
                        )
                    self._audit(cursor, scope, task_id, "collection_task.deletion_started", result)
                connection.commit()
            try:
                self.objects.purge(manifest)
            except Exception as exc:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """UPDATE collection_tasks.deletions SET state='FAILED',updated_at=now()
                        WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
                        (org, project, task_id),
                    )
                connection.commit()
                raise problem(
                    status=503,
                    code="TASK_DELETE_CLEANUP_PENDING",
                    title="Task cleanup pending",
                    detail="任务已停止接收数据，存储清理尚未完成。请重试删除，系统会继续清理剩余文件。",
                ) from exc
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.collection_task_purge_id',%s,true)", (task_id,)
                )
                cursor.execute(
                    """DELETE FROM collection_tasks.collection_tasks
                    WHERE organization_id=%s AND project_id=%s AND collection_task_id=%s""",
                    (org, project, task_id),
                )
                cursor.execute(
                    """UPDATE collection_tasks.deletions SET state='DELETED',manifest='{}'::jsonb,
                    updated_at=now() WHERE organization_id=%s AND project_id=%s
                    AND collection_task_id=%s""",
                    (org, project, task_id),
                )
                self._audit(cursor, scope, task_id, "collection_task.deleted", result)
            connection.commit()
            return TaskDeletionResult.model_validate(result)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()  # releases the session advisory lock, including on error

    @staticmethod
    def _audit(cursor, scope, task_id, action, result):
        context = current_request_context()
        cursor.execute(
            """INSERT INTO core.audit_events
            (audit_id,organization_id,project_id,region_code,actor_id,action,resource_type,
             resource_id,request_id,details,occurred_at)
            VALUES (%s,%s,%s,%s,%s,%s,'collection_task',%s,%s,%s::jsonb,now())""",
            (
                str(uuid4()),
                *scope,
                context.subject_id or "unknown",
                action,
                task_id,
                context.request_id,
                json.dumps(result),
            ),
        )


def deletion_order(cursor):
    """Children first; nullable current-version pointers are cleared beforehand."""
    cursor.execute("""SELECT nc.nspname||'.'||c.relname,np.nspname||'.'||p.relname
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace nc ON nc.oid=c.relnamespace
        JOIN pg_class p ON p.oid=k.confrelid JOIN pg_namespace np ON np.oid=p.relnamespace
        WHERE k.contype='f' AND NOT k.condeferrable""")
    edges = {
        (child, parent)
        for child, parent in cursor.fetchall()
        if child in TABLES
        and parent in TABLES
        and child != parent
        and (child, parent) != ("episode_store.bindings", "episode_store.versions")
    }
    remaining = set(TABLES)
    ordered = []
    while remaining:
        ready = sorted(t for t in remaining if not any(p == t and c in remaining for c, p in edges))
        if not ready:
            raise RuntimeError("task deletion ownership graph contains a non-deferrable cycle")
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered

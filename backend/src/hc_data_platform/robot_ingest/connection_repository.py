"""Persistence for connection receipts and organization-scoped task projections."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager

from .connections import reject


class ConnectionStore:
    def __init__(self, repository):
        self.repo = repository
        self.memory = not hasattr(repository, "_bootstrap")
        if self.memory:
            with repository._lock:
                if not hasattr(repository, "connection_tickets"):
                    repository.connection_tickets = {}
                    repository.connection_locals = {}
                    repository.task_labels = {}

    def insert(self, key, value):
        if self.memory:
            with self.repo._lock:
                self.repo.connection_tickets[key] = copy.deepcopy(value)
        else:
            with self.repo._scoped() as db, db.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO ingest.robot_connection_tickets
                    (ticket_digest, organization_id, document) VALUES (%s,%s,%s::jsonb)""",
                    (key, value["organization_id"], json.dumps(value)),
                )

    @contextmanager
    def credential_lock(self, organization_id, identity_id):
        """Serialize pairing across different tickets for the same identity."""
        if self.memory:
            with self.repo._lock:
                yield
        else:
            with self.repo._scoped() as db, db.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (json.dumps(["robot-connection", organization_id, identity_id]),),
                )
                yield

    @contextmanager
    def locked(self, key):
        if self.memory:
            with self.repo._lock:
                value = copy.deepcopy(self.repo.connection_tickets.get(key))
                yield value
                if value is not None:
                    self.repo.connection_tickets[key] = value
        else:
            with self.repo._bootstrap() as db, db.cursor() as cursor:
                cursor.execute(
                    "SELECT document FROM ingest.robot_connection_tickets "
                    "WHERE ticket_digest=%s FOR UPDATE",
                    (key,),
                )
                row = cursor.fetchone()
                value = row[0] if row else None
                if isinstance(value, str):
                    value = json.loads(value)
                yield value
                if value is not None:
                    cursor.execute(
                        "UPDATE ingest.robot_connection_tickets SET document=%s::jsonb "
                        "WHERE ticket_digest=%s",
                        (json.dumps(value), key),
                    )

    def bind_local(self, ticket, local):
        key = (ticket["organization_id"], ticket["identity_id"])
        if self.memory:
            with self.repo._lock:
                current = self.repo.connection_locals.setdefault(key, copy.deepcopy(local))
        else:
            with self.repo._scoped() as db, db.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO ingest.robot_connection_locals
                    (organization_id, ingest_identity_id, local_robot) VALUES (%s,%s,%s::jsonb)
                    ON CONFLICT (organization_id,ingest_identity_id) DO NOTHING""",
                    (*key, json.dumps(local)),
                )
                cursor.execute(
                    "SELECT local_robot FROM ingest.robot_connection_locals "
                    "WHERE organization_id=%s AND ingest_identity_id=%s",
                    key,
                )
                current = cursor.fetchone()[0]
        if current != local:
            reject("LOCAL_ROBOT_MISMATCH", "已有采集配置映射与当前机器人不一致，请检查机器人配置")

    def local(self, organization_id, identity_id):
        if self.memory:
            with self.repo._lock:
                return copy.deepcopy(
                    self.repo.connection_locals.get((organization_id, identity_id))
                )
        with self.repo._scoped() as db, db.cursor() as cursor:
            cursor.execute(
                "SELECT local_robot FROM ingest.robot_connection_locals "
                "WHERE organization_id=%s AND ingest_identity_id=%s",
                (organization_id, identity_id),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def model_ready(self, organization_id, version_id):
        if self.memory:
            return True
        with self.repo._scoped() as db, db.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM registry.robot_model_versions WHERE organization_id=%s "
                "AND version_id=%s AND lifecycle='PUBLISHED' AND asset_availability='AVAILABLE'",
                (organization_id, version_id),
            )
            return cursor.fetchone() is not None

    def task_row(self, organization_id, task_id):
        return next(iter(self.task_rows(organization_id, task_id)), None)

    def task_rows(self, organization_id, task_id=None):
        if self.memory:
            with self.repo._lock:
                return [
                    {
                        **t.model_dump(),
                        "name": t.collection_task_id,
                        "task_code": t.collection_task_id,
                        "description": "",
                        "project_name": t.project_id,
                        "ready": True,
                        **self.repo.task_labels.get(t.collection_task_id, {}),
                    }
                    for t in self.repo._targets.values()
                    if t.organization_id == organization_id
                    and (task_id is None or task_id == t.collection_task_id)
                ]
        # Machine auth has an organization, never a human project-management scope.
        # Use the same narrow bootstrap connection as resolve_upload_target, with
        # explicit organization predicates on every relation.
        with self.repo._bootstrap() as db, db.cursor() as cursor:
            cursor.execute(
                """SELECT t.collection_task_id,t.name,t.task_code,t.description,
                     t.project_id,t.status,
                     EXISTS(SELECT 1 FROM dataset_registry.datasets d
                         WHERE d.organization_id=t.organization_id AND d.project_id=t.project_id
                           AND d.dataset_id=t.dataset_id AND d.region_code=r.region_code
                           AND d.availability='ACTIVE') AS ready,
                     COALESCE(p.display_name,t.project_id),r.organization_id,r.dataset_id,r.region_code
                FROM collection_tasks.collection_tasks t
                LEFT JOIN registry.organization_projects p
                  ON p.organization_id=t.organization_id AND p.project_id=t.project_id
                CROSS JOIN LATERAL
                    collection_tasks.resolve_robot_upload_target(t.collection_task_id) r
                WHERE t.organization_id=%s AND (%s::text IS NULL OR t.collection_task_id=%s)
                AND NOT EXISTS (SELECT 1 FROM collection_tasks.deletions x
                    WHERE x.organization_id=t.organization_id AND x.project_id=t.project_id
                      AND x.collection_task_id=t.collection_task_id)
                ORDER BY t.name,t.collection_task_id""",
                (organization_id, task_id, task_id),
            )
            return [
                {
                    "collection_task_id": r[0],
                    "name": r[1],
                    "task_code": r[2],
                    "description": r[3],
                    "project_id": r[4],
                    "project_name": r[7],
                    "task_status": r[5],
                    "ready": r[6],
                    "organization_id": r[8],
                    "dataset_id": r[9],
                    "region_code": r[10],
                }
                for r in cursor.fetchall()
            ]

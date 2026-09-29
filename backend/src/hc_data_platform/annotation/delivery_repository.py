"""Transactional delivery commands and fenced worker leases on the existing outbox."""

import hashlib
import json
from uuid import uuid4

from hc_data_platform.core.events import DomainEventEnvelope

from .delivery_models import AnnotationDeliveryJob
from .postgres import _row
from .revision_files import _SCOPE, PostgresRevisionFiles, SnapshotBusy, _failure


class DeliveryLeaseLost(RuntimeError):
    pass


def public_job(row):
    return AnnotationDeliveryJob.model_validate(
        {key: row[key] for key in AnnotationDeliveryJob.model_fields}
    )


class PostgresAnnotationDeliveries(PostgresRevisionFiles):
    @staticmethod
    def _get_job(cursor, task_id, job_id, *, lock=False):
        cursor.execute(
            "SELECT * FROM annotation.delivery_jobs WHERE task_id = %s AND job_id = %s "
            f"AND {_SCOPE}" + (" FOR UPDATE" if lock else ""),
            (task_id, job_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise _failure(
                "ANNOTATION_DELIVERY_NOT_FOUND", "Delivery unavailable in this scope.", 404
            )
        return _row(cursor, raw)

    def get_job(self, task_id, job_id):
        with self.transaction() as cursor:
            return self._get_job(cursor, task_id, job_id)

    def list_jobs(self, task_id, limit, before=None):
        with self.transaction() as cursor:
            extra, args = "", [task_id]
            if before:
                previous = self._get_job(cursor, task_id, before)
                extra = " AND (created_at, job_id) < (%s, %s)"
                args += [previous["created_at"], before]
            cursor.execute(
                f"SELECT * FROM annotation.delivery_jobs WHERE task_id = %s AND {_SCOPE}"
                + extra
                + " ORDER BY created_at DESC, job_id DESC LIMIT %s",
                (*args, limit),
            )
            return [public_job(_row(cursor, raw)) for raw in cursor.fetchall()]

    def request_delivery(self, task_id, actor_id, key, request_hash, freeze):
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        with self.transaction() as cursor:
            # Serialize commands for this task, including first-time idempotency keys.
            # Approval/default-revision changes use the same owner row lock.
            cursor.execute(
                f"SELECT * FROM annotation.annotation_tasks WHERE task_id = %s AND {_SCOPE} "
                "FOR UPDATE",
                (task_id,),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise _failure("ANNOTATION_NOT_FOUND", "Task unavailable in this scope.", 404)
            owner = _row(cursor, raw)
            cursor.execute(
                f"SELECT * FROM annotation.delivery_requests WHERE task_id = %s AND actor_id = %s "
                f"AND key_sha256 = %s AND {_SCOPE}",
                (task_id, actor_id, key_hash),
            )
            old = cursor.fetchone()
            if old:
                old = _row(cursor, old)
                if old["request_sha256"] != request_hash:
                    raise _failure(
                        "ANNOTATION_DELIVERY_IDEMPOTENCY_CONFLICT",
                        "Use a new key for a different selection.",
                    )
                return self._get_job(cursor, task_id, old["job_id"])
            plan, annotation_key = freeze()
            cursor.execute(
                """INSERT INTO annotation.delivery_jobs
                    (organization_id, project_id, region_code, task_id, job_id, plan,
                     annotation_key, actor_id)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s) ON CONFLICT (job_id) DO NOTHING""",
                (
                    owner["organization_id"],
                    owner["project_id"],
                    owner["region_code"],
                    task_id,
                    plan.job_id,
                    plan.model_dump_json(),
                    annotation_key,
                    actor_id,
                ),
            )
            job = self._get_job(cursor, task_id, plan.job_id)
            if public_job(job).plan != plan:
                raise ValueError("delivery cache plan identity differs")
            cursor.execute(
                """INSERT INTO annotation.delivery_requests
                    (organization_id, project_id, region_code, task_id, actor_id, key_sha256,
                     request_sha256, job_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    owner["organization_id"],
                    owner["project_id"],
                    owner["region_code"],
                    task_id,
                    actor_id,
                    key_hash,
                    request_hash,
                    plan.job_id,
                ),
            )
            self.audit(cursor, job, actor_id, "annotation.delivery.requested", {})
            return job

    def command(self, task_id, job_id, generation, actor_id, *, retry):
        with self.transaction() as cursor:
            row = self._get_job(cursor, task_id, job_id, lock=True)
            if row["generation"] != generation:
                raise _failure(
                    "ANNOTATION_DELIVERY_GENERATION_CONFLICT",
                    "Refresh delivery state before retrying.",
                )
            if retry:
                if row["status"] not in {"FAILED", "CANCELLED"}:
                    raise _failure(
                        "ANNOTATION_DELIVERY_RETRY_NOT_ALLOWED",
                        "Only failed or cancelled deliveries can retry.",
                    )
                update = "status = 'PENDING', phase = 'queued', generation = generation + 1"
            else:
                if row["status"] == "CANCELLED":
                    return row
                if row["status"] not in {"PENDING", "RUNNING"}:
                    raise _failure(
                        "ANNOTATION_DELIVERY_CANCEL_NOT_ALLOWED", "Delivery has already finished."
                    )
                update = "status = 'CANCELLED', phase = 'cancelled'"
            cursor.execute(
                f"UPDATE annotation.delivery_jobs SET {update}, "
                "lease_token = NULL, lease_expires_at = NULL, "
                f"error_code = NULL, updated_at = clock_timestamp() WHERE job_id = %s AND {_SCOPE}",
                (job_id,),
            )
            self.audit(
                cursor,
                row,
                actor_id,
                "annotation.delivery.retried" if retry else "annotation.delivery.cancelled",
                {},
            )
            return self._get_job(cursor, task_id, job_id)

    def validate_event(self, event):
        with self.transaction() as cursor:
            cursor.execute(
                f"SELECT envelope FROM core.outbox_events WHERE event_id = %s AND {_SCOPE}",
                (event.event_id,),
            )
            stored = cursor.fetchone()
            if stored is None or DomainEventEnvelope.model_validate(stored[0]) != event:
                raise ValueError("delivery event differs from its persisted scope and command")

    def claim_delivery(self, task_id, job_id, generation):
        with self.transaction() as cursor:
            row = self._get_job(cursor, task_id, job_id, lock=True)
            if row["generation"] != generation or row["status"] in {"READY", "FAILED", "CANCELLED"}:
                return None
            cursor.execute("SELECT clock_timestamp()")
            if (
                row["lease_expires_at"] is not None
                and row["lease_expires_at"] > cursor.fetchone()[0]
            ):
                raise SnapshotBusy("delivery generation is leased")
            cursor.execute(
                f"""UPDATE annotation.delivery_jobs SET status = 'RUNNING', phase = 'verifying',
                    lease_token = %s, lease_expires_at = clock_timestamp() + interval '5 minutes',
                    attempts = attempts + 1, updated_at = clock_timestamp()
                    WHERE job_id = %s AND {_SCOPE}""",
                (uuid4(), job_id),
            )
            return self._get_job(cursor, task_id, job_id)

    def leased_update(self, row, update, values=()):
        with self.transaction() as cursor:
            cursor.execute(
                f"UPDATE annotation.delivery_jobs SET {update}, updated_at = clock_timestamp() "
                f"WHERE job_id = %s AND generation = %s AND lease_token = %s "
                "AND status = 'RUNNING' AND lease_expires_at > clock_timestamp() "
                f"AND {_SCOPE} RETURNING job_id",
                (*values, row["job_id"], row["generation"], row["lease_token"]),
            )
            if cursor.fetchone() is None:
                raise DeliveryLeaseLost("delivery was cancelled or its worker lease was replaced")

    def heartbeat(self, row):
        self.leased_update(row, "lease_expires_at = clock_timestamp() + interval '5 minutes'")

    def phase(self, row, phase):
        self.leased_update(row, "phase = %s", (phase,))

    def pin_artifact(self, row, key, sha256, size, manifest_sha256):
        self.leased_update(
            row,
            "artifact_key = %s, artifact_sha256 = %s, size_bytes = %s, manifest_sha256 = %s",
            (key, sha256, size, manifest_sha256),
        )

    def finish_delivery(self, row, error_code=None):
        self.leased_update(
            row,
            "status = %s, phase = %s, error_code = %s, lease_token = NULL, lease_expires_at = NULL",
            (
                "READY" if error_code is None else "FAILED",
                "completed" if error_code is None else "failed",
                error_code,
            ),
        )

    @staticmethod
    def audit(cursor, row, actor_id, action, details):
        cursor.execute(
            """INSERT INTO core.audit_events
                (audit_id, organization_id, project_id, region_code, actor_id, action,
                 resource_type, resource_id, request_id, details, occurred_at)
                VALUES (%s,%s,%s,%s,%s,%s,'annotation_delivery',%s,
                    NULLIF(current_setting('app.request_id', true), ''),
                    %s::jsonb,clock_timestamp())""",
            (
                uuid4(),
                row["organization_id"],
                row["project_id"],
                row["region_code"],
                actor_id,
                action,
                row["job_id"],
                json.dumps(details),
            ),
        )

    def audit_delivery_download(self, row, actor_id):
        with self.transaction() as cursor:
            self.audit(
                cursor,
                row,
                actor_id,
                "annotation.delivery.downloaded",
                {
                    "artifact_sha256": row["artifact_sha256"],
                    "generation": row["generation"],
                },
            )

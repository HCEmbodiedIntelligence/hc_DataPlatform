"""Scoped commands, durable outbox dispatch and fenced complete-package producers."""

from hashlib import sha256
from uuid import uuid4

from hc_data_platform.core.artifact_tasks import DeliveryLeaseLost
from hc_data_platform.core.context import current_request_context
from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope

from .delivery_models import EpisodeDeliveryJob
from .models import canonical, digest
from .postgres import PostgresEpisodeStore

SCOPE = "organization_id=%s AND project_id=%s AND region_code=%s"


class EpisodeDeliveryBusy(RuntimeError):
    pass


def scope_key():
    context = current_request_context()
    scope = context.organization_id, context.project_id, context.region_code
    if not all(scope):
        raise PermissionError("package delivery requires an exact tenant/project/region")
    return scope


def failure(code, detail, status=409):
    return problem(
        status=status,
        code="EPISODE_DELIVERY_" + code,
        title="Episode delivery unavailable",
        detail=detail,
    )


def record(cursor, row):
    return dict(zip((column.name for column in cursor.description), row, strict=True))


def public_job(row):
    job = EpisodeDeliveryJob.model_validate(
        {key: row[key] for key in EpisodeDeliveryJob.model_fields}
    )
    if job.plan.job_id != job.job_id:
        raise failure("INTEGRITY_FAILED", "Delivery plan differs from its content identity.")
    return job


class PostgresEpisodeDeliveries(PostgresEpisodeStore):
    @staticmethod
    def _get(cursor, job_id, *, lock=False):
        cursor.execute(
            f"SELECT * FROM episode_store.delivery_jobs WHERE {SCOPE} AND job_id=%s"
            + (" FOR UPDATE" if lock else ""),
            (*scope_key(), job_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise failure("NOT_FOUND", "Delivery unavailable in this scope.", 404)
        return record(cursor, row)

    def get_job(self, job_id):
        with self.transaction() as cursor:
            return self._get(cursor, job_id)

    def list_jobs(self, *, data_version_id=None, limit=25, before=None):
        if not 1 <= limit <= 100:
            raise ValueError("page limit must be 1..100")
        with self.transaction() as cursor:
            extra, args = "", list(scope_key())
            if data_version_id:
                extra += " AND plan @> %s::jsonb"
                args.append(
                    canonical(
                        {"members": [{"data": {"data_version_id": data_version_id}}]}
                    ).decode()
                )
            if before:
                prior = self._get(cursor, before)
                extra += " AND (created_at,job_id)<(%s,%s)"
                args += [prior["created_at"], before]
            cursor.execute(
                f"SELECT * FROM episode_store.delivery_jobs WHERE {SCOPE}"
                + extra
                + " ORDER BY created_at DESC,job_id DESC LIMIT %s",
                (*args, limit),
            )
            return tuple(public_job(record(cursor, row)) for row in cursor.fetchall())

    def request_delivery(self, plan, key, actor_id, total_files, total_bytes):
        scope = scope_key()
        if any(m.data.identity.scope != scope for m in plan.members):
            raise PermissionError("delivery selection differs from request scope")
        key_hash, request_hash = (
            sha256(key.encode()).hexdigest(),
            digest(plan.model_dump(mode="json")),
        )
        with self.transaction() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("episode-delivery-command/1:" + digest((*scope, actor_id, key_hash)),),
            )
            cursor.execute(
                f"SELECT request_sha256,job_id FROM episode_store.delivery_requests WHERE {SCOPE} "
                "AND actor_id=%s AND key_sha256=%s",
                (*scope, actor_id, key_hash),
            )
            previous = cursor.fetchone()
            if previous:
                if previous[0] != request_hash:
                    raise failure(
                        "IDEMPOTENCY_CONFLICT", "Use a new key for a different selection."
                    )
                return self._get(cursor, previous[1])
            cursor.execute(
                """INSERT INTO episode_store.delivery_jobs
                (organization_id,project_id,region_code,job_id,plan,actor_id,total_files,total_bytes)
                VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT (job_id) DO NOTHING""",
                (*scope, plan.job_id, plan.model_dump_json(), actor_id, total_files, total_bytes),
            )
            row = self._get(cursor, plan.job_id)
            if public_job(row).plan != plan:
                raise failure("INTEGRITY_FAILED", "Cached delivery has a different plan.")
            cursor.execute(
                """INSERT INTO episode_store.delivery_requests
                (organization_id,project_id,region_code,actor_id,key_sha256,request_sha256,job_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (*scope, actor_id, key_hash, request_hash, plan.job_id),
            )
            self.audit(cursor, row, actor_id, "episode.delivery.requested", {})
            return row

    def command(self, job_id, generation, actor_id, *, retry):
        with self.transaction() as cursor:
            row = self._get(cursor, job_id, lock=True)
            if row["generation"] != generation:
                raise failure("GENERATION_CONFLICT", "Refresh delivery state before retrying.")
            if retry:
                if row["status"] not in {"FAILED", "CANCELLED"}:
                    raise failure(
                        "RETRY_NOT_ALLOWED", "Only failed or cancelled deliveries can retry."
                    )
                change = (
                    "status='PENDING',phase='queued',generation=generation+1,"
                    "processed_bytes=0,processed_files=0,uploaded_bytes=0"
                )
            else:
                if row["status"] == "CANCELLED":
                    return row
                if row["status"] not in {"PENDING", "RUNNING"}:
                    raise failure("CANCEL_NOT_ALLOWED", "Delivery has already finished.")
                change = "status='CANCELLED',phase='cancelled'"
            cursor.execute(
                f"UPDATE episode_store.delivery_jobs SET {change},lease_token=NULL,"
                "lease_expires_at=NULL,error_code=NULL,updated_at=clock_timestamp() "
                f"WHERE {SCOPE} AND job_id=%s",
                (*scope_key(), job_id),
            )
            self.audit(
                cursor,
                row,
                actor_id,
                "episode.delivery.retried" if retry else "episode.delivery.cancelled",
                {},
            )
            return self._get(cursor, job_id)

    def claim(self, job_id, generation):
        with self.transaction() as cursor:
            row = self._get(cursor, job_id, lock=True)
            if row["generation"] != generation or row["status"] in {"READY", "FAILED", "CANCELLED"}:
                return None
            cursor.execute("SELECT clock_timestamp()")
            if row["lease_expires_at"] and row["lease_expires_at"] > cursor.fetchone()[0]:
                raise EpisodeDeliveryBusy("delivery is already leased")
            cursor.execute(
                f"""UPDATE episode_store.delivery_jobs SET status='RUNNING',phase='verifying',
                lease_token=%s,lease_expires_at=clock_timestamp()+interval '5 minutes',
                attempts=attempts+1,
                processed_bytes=0,processed_files=0,uploaded_bytes=0,updated_at=clock_timestamp()
                WHERE {SCOPE} AND job_id=%s""",
                (uuid4(), *scope_key(), job_id),
            )
            return self._get(cursor, job_id)

    def leased_update(self, row, update, values=()):
        with self.transaction() as cursor:
            cursor.execute(
                f"UPDATE episode_store.delivery_jobs SET {update},updated_at=clock_timestamp() "
                f"WHERE {SCOPE} AND job_id=%s AND generation=%s AND lease_token=%s "
                "AND status='RUNNING' AND lease_expires_at>clock_timestamp() RETURNING job_id",
                (*values, *scope_key(), row["job_id"], row["generation"], row["lease_token"]),
            )
            if cursor.fetchone() is None:
                raise DeliveryLeaseLost("delivery cancelled or worker lease replaced")

    def heartbeat(self, row):
        self.leased_update(row, "lease_expires_at=clock_timestamp()+interval '5 minutes'")

    def phase(self, row, phase):
        self.leased_update(row, "phase=%s", (phase,))

    def progress(self, row, files, size):
        self.leased_update(row, "processed_files=%s,processed_bytes=%s", (files, size))

    def pin_artifact(self, row, key, checksum, size):
        self.leased_update(
            row, "artifact_key=%s,artifact_sha256=%s,size_bytes=%s", (key, checksum, size)
        )

    def finish(self, row, error_code=None):
        completed = (
            ",processed_files=total_files,processed_bytes=total_bytes,uploaded_bytes=size_bytes"
            if error_code is None
            else ""
        )
        self.leased_update(
            row,
            "status=%s,phase=%s,error_code=%s,lease_token=NULL,lease_expires_at=NULL" + completed,
            (
                "READY" if error_code is None else "FAILED",
                "completed" if error_code is None else "failed",
                error_code,
            ),
        )

    def validate_event(self, event):
        with self.transaction() as cursor:
            cursor.execute(
                f"SELECT envelope FROM core.outbox_events WHERE {SCOPE} AND event_id=%s",
                (*scope_key(), event.event_id),
            )
            row = cursor.fetchone()
            if row is None or DomainEventEnvelope.model_validate(row[0]) != event:
                raise ValueError("delivery event differs from the persisted command")

    @staticmethod
    def audit(cursor, row, actor_id, action, details):
        cursor.execute(
            """INSERT INTO core.audit_events
            (audit_id,organization_id,project_id,region_code,actor_id,action,
             resource_type,resource_id,request_id,details,occurred_at)
            VALUES (%s,%s,%s,%s,%s,%s,'episode_delivery',%s,
                NULLIF(current_setting('app.request_id',true),''),%s::jsonb,clock_timestamp())""",
            (uuid4(), *scope_key(), actor_id, action, row["job_id"], canonical(details).decode()),
        )

    def audit_download(self, row, actor_id):
        with self.transaction() as cursor:
            self.audit(
                cursor,
                row,
                actor_id,
                "episode.delivery.downloaded",
                {"artifact_sha256": row["artifact_sha256"], "generation": row["generation"]},
            )

"""Scoped Tag creation commands and recoverable, leased primary publication."""

from hashlib import sha256
from uuid import uuid4

from hc_data_platform.core.artifact_tasks import DeliveryLeaseLost
from hc_data_platform.core.errors import problem
from hc_data_platform.core.events import DomainEventEnvelope

from .delivery_repository import SCOPE, record, scope_key
from .derivation_jobs import TagDerivationJob
from .models import canonical, digest
from .postgres import PostgresEpisodeStore


class TagDerivationBusy(RuntimeError):
    pass


def failure(code, detail, status=409):
    return problem(
        status=status, code="TAG_EPISODE_" + code, title="Tag Episode unavailable", detail=detail
    )


def public_job(row):
    return TagDerivationJob.model_validate({k: row[k] for k in TagDerivationJob.model_fields})


class PostgresTagDerivations(PostgresEpisodeStore):
    @staticmethod
    def _get(cursor, job_id, *, lock=False):
        cursor.execute(
            f"SELECT * FROM episode_store.tag_derivation_jobs WHERE {SCOPE} AND job_id=%s"
            + (" FOR UPDATE" if lock else ""),
            (*scope_key(), job_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise failure("NOT_FOUND", "Tag creation task is unavailable in this scope.", 404)
        return record(cursor, row)

    def get_job(self, job_id):
        with self.transaction() as cursor:
            return self._get(cursor, job_id)

    def list_jobs(self, task_id, *, limit=25, before=None):
        if not 1 <= limit <= 100:
            raise ValueError("Tag task page limit must be 1..100")
        with self.transaction() as cursor:
            extra, args = "", [*scope_key(), task_id]
            if before:
                prior = self._get(cursor, before)
                if prior["task_id"] != task_id:
                    raise failure("NOT_FOUND", "Cursor belongs to another annotation task.", 404)
                extra = " AND (created_at,job_id)<(%s,%s)"
                args += [prior["created_at"], before]
            cursor.execute(
                f"SELECT * FROM episode_store.tag_derivation_jobs WHERE {SCOPE} AND task_id=%s"
                + extra
                + " ORDER BY created_at DESC,job_id DESC LIMIT %s",
                (*args, limit),
            )
            return tuple(public_job(record(cursor, row)) for row in cursor.fetchall())

    def request(self, task_id, key, actor_id, request_hash, freeze):
        scope, key_hash = scope_key(), sha256(key.encode()).hexdigest()
        with self.transaction() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("tag-episode-command/1:" + digest((*scope, task_id, actor_id, key_hash)),),
            )
            cursor.execute(
                "SELECT request_sha256,job_id FROM episode_store.tag_derivation_requests "
                f"WHERE {SCOPE} "
                "AND task_id=%s AND actor_id=%s AND key_sha256=%s",
                (*scope, task_id, actor_id, key_hash),
            )
            previous = cursor.fetchone()
            if previous:
                if previous[0] != request_hash:
                    raise failure(
                        "IDEMPOTENCY_CONFLICT", "Use a new key for a different Tag selection."
                    )
                return self._get(cursor, previous[1])
            plan = freeze()
            if (
                plan.episode.parent.identity.scope != scope
                or plan.episode.annotation.binding.task_id != task_id
            ):
                raise PermissionError("Tag selection differs from request scope/task")
            source = plan.episode.input
            cursor.execute(
                """INSERT INTO episode_store.tag_derivation_jobs
                (organization_id,project_id,region_code,job_id,plan,actor_id,task_id,episode_id,data_version_id)
                VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s) ON CONFLICT (job_id) DO NOTHING""",
                (
                    *scope,
                    plan.job_id,
                    plan.model_dump_json(),
                    actor_id,
                    task_id,
                    source.identity.episode_id,
                    source.data_version_id,
                ),
            )
            row = self._get(cursor, plan.job_id)
            if public_job(row).plan != plan:
                raise failure("INTEGRITY_FAILED", "Stored Tag job differs from its frozen plan.")
            cursor.execute(
                """INSERT INTO episode_store.tag_derivation_requests
                (organization_id,project_id,region_code,task_id,actor_id,key_sha256,request_sha256,job_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (*scope, task_id, actor_id, key_hash, request_hash, plan.job_id),
            )
            self.audit(cursor, row, actor_id, "episode.tag.requested")
            return row

    def command(self, job_id, generation, actor_id, *, retry):
        with self.transaction() as cursor:
            row = self._get(cursor, job_id, lock=True)
            if row["generation"] != generation:
                raise failure("GENERATION_CONFLICT", "Refresh Tag creation state before retrying.")
            if retry:
                if row["status"] not in {"FAILED", "CANCELLED"}:
                    raise failure(
                        "RETRY_NOT_ALLOWED", "Only failed or cancelled Tag jobs can retry."
                    )
                change = "status='PENDING',phase='queued',generation=generation+1,error_code=NULL"
            else:
                if row["status"] == "CANCELLED":
                    return row
                if row["status"] != "PENDING" or row["attempts"] != 0:
                    raise failure(
                        "CANCEL_NOT_ALLOWED",
                        "Primary publication may have started; "
                        "only never-started jobs can be cancelled.",
                    )
                change = "status='CANCELLED',phase='cancelled'"
            cursor.execute(
                f"UPDATE episode_store.tag_derivation_jobs SET {change},"
                "updated_at=clock_timestamp() "
                f"WHERE {SCOPE} AND job_id=%s",
                (*scope_key(), job_id),
            )
            self.audit(
                cursor, row, actor_id, "episode.tag.retried" if retry else "episode.tag.cancelled"
            )
            return self._get(cursor, job_id)

    def claim(self, job_id, generation):
        with self.transaction() as cursor:
            row = self._get(cursor, job_id, lock=True)
            if row["generation"] != generation or row["status"] in {"READY", "FAILED", "CANCELLED"}:
                return None
            cursor.execute("SELECT clock_timestamp()")
            if row["lease_expires_at"] and row["lease_expires_at"] > cursor.fetchone()[0]:
                raise TagDerivationBusy("Tag creation is already leased")
            cursor.execute(
                f"""UPDATE episode_store.tag_derivation_jobs SET status='RUNNING',phase='verifying',
                lease_token=%s,lease_expires_at=clock_timestamp()+interval '5 minutes',
                attempts=attempts+1,updated_at=clock_timestamp() WHERE {SCOPE} AND job_id=%s""",
                (uuid4(), *scope_key(), job_id),
            )
            return self._get(cursor, job_id)

    def leased_update(self, row, update, values=()):
        with self.transaction() as cursor:
            cursor.execute(
                f"UPDATE episode_store.tag_derivation_jobs SET {update},"
                "updated_at=clock_timestamp() "
                f"WHERE {SCOPE} AND job_id=%s AND generation=%s AND lease_token=%s "
                "AND status='RUNNING' AND lease_expires_at>clock_timestamp() RETURNING job_id",
                (*values, *scope_key(), row["job_id"], row["generation"], row["lease_token"]),
            )
            if cursor.fetchone() is None:
                raise DeliveryLeaseLost("Tag worker lease was replaced")

    def heartbeat(self, row):
        self.leased_update(row, "lease_expires_at=clock_timestamp()+interval '5 minutes'")

    def phase(self, row, phase):
        self.leased_update(row, "phase=%s", (phase,))

    def pin_data(self, row, data):
        self.leased_update(row, "committed_data=%s::jsonb", (data.model_dump_json(),))

    def pin_projection(self, row, version, viewer):
        self.leased_update(
            row,
            "collection_version_id=%s,viewer_target=%s::jsonb",
            (version.collection_version_id, viewer.model_dump_json()),
        )

    def finish(self, row, *, annotation_task_id=None, error_code=None):
        self.leased_update(
            row,
            "status=%s,phase=%s,error_code=%s,annotation_task_id=%s,lease_token=NULL,lease_expires_at=NULL",
            (
                "READY" if error_code is None else "FAILED",
                "completed" if error_code is None else "failed",
                error_code,
                annotation_task_id,
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
                raise ValueError("Tag event differs from the persisted command")

    @staticmethod
    def audit(cursor, row, actor_id, action):
        cursor.execute(
            """INSERT INTO core.audit_events
            (audit_id,organization_id,project_id,region_code,actor_id,action,resource_type,resource_id,
             request_id,details,occurred_at)
            VALUES (%s,%s,%s,%s,%s,%s,'tag_episode',%s,
              NULLIF(current_setting('app.request_id',true),''),%s::jsonb,clock_timestamp())""",
            (
                uuid4(),
                *scope_key(),
                actor_id,
                action,
                row["job_id"],
                canonical({"generation": row["generation"]}).decode(),
            ),
        )

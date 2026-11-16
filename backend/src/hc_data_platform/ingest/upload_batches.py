"""Stable submission numbers shared by web uploads and OSS imports."""

from hc_data_platform.core.config import get_settings
from hc_data_platform.core.dbapi import psycopg_connection_factory

SCOPE = "(organization_id, project_id, region_code) = (%s, %s, %s)"


def register_batch(scope, channel, submission_id, upload_id=None):
    key = f"{channel}:{submission_id}"
    with psycopg_connection_factory(get_settings().postgres_dsn)() as db:
        return register(db, scope, channel, key, upload_id)


def register(db, scope, channel, key, upload_id=None):
    # Serialize number allocation per scope. Retries keep the same number and
    # overlapping submissions can each retain their own membership.
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("upload-batch:" + "/".join(scope),),
    )
    db.execute(
        f"""INSERT INTO ingest.upload_batches
        (organization_id, project_id, region_code, batch_key, channel, batch_number)
        SELECT %s,%s,%s,%s,%s,COALESCE(MAX(batch_number),0)+1
        FROM ingest.upload_batches WHERE {SCOPE}
        ON CONFLICT (organization_id,project_id,region_code,batch_key) DO NOTHING""",
        (*scope, key, channel, *scope),
    )
    if upload_id is not None:
        db.execute(
            """INSERT INTO ingest.upload_batch_members
            (organization_id, project_id, region_code, batch_key, upload_id)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (*scope, key, str(upload_id)),
        )
    return db.execute(
        f"SELECT batch_number FROM ingest.upload_batches WHERE {SCOPE} AND batch_key=%s",
        (*scope, key),
    ).fetchone()[0]

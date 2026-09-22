"""Apply the C-owned migration ONLY to the isolated C test database.

E owns the shared migration manifest; this is a development test bootstrap, not
a deployment migration runner. The C database records its extra schema here.
"""

import hashlib
from pathlib import Path

import psycopg

path = Path("migrations/ingest/017_robot_processing.sql")
digest = hashlib.sha256(path.read_bytes()).hexdigest()
with psycopg.connect("postgresql://hc:hc@postgres:5432/hc_data") as connection:
    exists = connection.execute("SELECT to_regclass('ingest.robot_processing')").fetchone()[0]
    if not exists:
        connection.execute(path.read_text())
    constraint = connection.execute(
        "SELECT 1 FROM pg_constraint WHERE conname='raw_source_episodes_ready_receipt'"
    ).fetchone()
    if not constraint:
        raise RuntimeError("C schema is incomplete; use a new isolated test database")
print(f"C migration available: {path} sha256={digest}")

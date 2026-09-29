"""Read-only evidence from the unmodified isolated delivery daemon; synthetic input."""

import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path

from hc_data_platform.annotation.delivery_models import AnnotationDeliveryJob
from hc_data_platform.annotation.delivery_repository import PostgresAnnotationDeliveries, public_job
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.runtime import _s3

assert os.environ["HC_TEMPORAL_NAMESPACE"] == "chayi-g1"
pending = AnnotationDeliveryJob.model_validate_json(Path(sys.argv[1]).read_bytes())
identity = pending.plan.binding.data.identity
assert identity.organization_id.startswith("bundle-")
connect = psycopg_connection_factory(os.environ["HC_G1_TEST_POSTGRES_DSN"])
repository = PostgresAnnotationDeliveries(connect)
_, _, storage = _s3(
    Settings(object_store_endpoint="http://minio:9000", object_store_bucket="hc-data-local")
)
token = bind_request_context(
    RequestContext(
        organization_id=identity.organization_id,
        project_id=identity.project_id,
        region_code=identity.region_code,
        subject_id="delivery-daemon-evidence",
        service_identity=True,
    )
)
try:
    for _ in range(45):
        row = repository.get_job(pending.plan.binding.task_id, pending.job_id)
        if row["status"] not in {"PENDING", "RUNNING"}:
            break
        time.sleep(1)
    job = public_job(row)
    assert job.status == "READY", job.status
    assert job.plan == pending.plan
    root = Path(os.environ["HC_G1_WRITER_EVIDENCE_ROOT"]) / "annotation-delivery-live" / job.job_id
    root.mkdir(parents=True, exist_ok=True)
    checksum = hashlib.sha256()
    with (root / "episode.zip").open("wb") as output:
        for chunk in storage.read_chunks(row["artifact_key"]):
            checksum.update(chunk)
            output.write(chunk)
    assert checksum.hexdigest() == job.artifact_sha256
    with zipfile.ZipFile(root / "episode.zip") as archive:
        # Verify a bounded, path-safe inventory before extracting for the offline reader.
        assert len(archive.infolist()) < 10000
        for member in archive.infolist():
            path = Path(member.filename)
            assert path.parts[0] == "episode" and not path.is_absolute() and ".." not in path.parts
        archive.extractall(root)
    with repository.transaction() as cursor:
        cursor.execute(
            """SELECT published_at, envelope FROM core.outbox_events
            WHERE event_type = 'annotation.delivery.requested.v1'
            AND organization_id = %s AND project_id = %s AND region_code = %s
            AND envelope->'payload'->>'job_id' = %s""",
            (*identity.scope, job.job_id),
        )
        outbox = cursor.fetchall()
        assert len(outbox) == 1 and outbox[0][0] is not None
    evidence = {
        "synthetic_input": True,
        "execution": "unmodified isolated outbox daemon",
        "job": job.model_dump(mode="json"),
        "outbox_published_at": outbox[0][0].isoformat(),
        "outbox": outbox[0][1],
        "archive_hash_verified": True,
        "http_browser_verified": False,
        "model_depth_training_support": "NOT_VALIDATED",
    }
    (root / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"directory": str(root), "job_id": job.job_id, "status": job.status}))
finally:
    reset_request_context(token)

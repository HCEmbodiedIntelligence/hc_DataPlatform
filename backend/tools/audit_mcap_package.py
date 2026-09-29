"""Read-only evidence audit against the isolated MCAP services and object store."""

import hashlib
import json
import os
import sys
from pathlib import Path

from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.episode_store.models import DataVersionRef
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.runtime import _s3

assert os.environ["HC_TEMPORAL_NAMESPACE"] == "chayi-g1"
directory = Path(sys.argv[1])
reference = json.loads((directory / "services.json").read_text())
data = DataVersionRef.model_validate(reference["ready"]["data"])
assert reference["fixture"] == "synthetic-MCAP-RGB-16UC1-32FC1"
assert data.identity.organization_id.startswith("mcap-")
_, _, storage = _s3(
    Settings(object_store_endpoint="http://minio:9000", object_store_bucket="hc-data-local")
)
packages = EpisodePackageService(
    psycopg_connection_factory(os.environ["HC_G1_TEST_POSTGRES_DSN"]),
    storage,
    directory / "audit-cache",
)
token = bind_request_context(
    RequestContext(
        organization_id=data.identity.organization_id,
        project_id=data.identity.project_id,
        region_code=data.identity.region_code,
        service_identity=True,
        subject_id="g1-mcap-audit",
    )
)
try:
    package = packages.files.open(data.identity, data.data_version_id)
    assert package.receipt.data == data
    for entry in package.manifest.files:
        with package.file(entry.path):
            pass
    source = package.document("hc/source.json")
    with packages.repository.transaction() as cursor:
        cursor.execute(
            """SELECT object_key,expected_sha256,expected_size FROM ingest.upload_sessions
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND session_id=%s
              AND status='RAW_COMMITTED'""",
            (*data.identity.scope, source["session_id"]),
        )
        original = cursor.fetchone()
        assert original is not None
        sha, size = hashlib.sha256(), 0
        for block in storage.read_chunks(original[0]):
            sha.update(block)
            size += len(block)
        assert (sha.hexdigest(), size) == (original[1], original[2])
        assert sha.hexdigest() == reference["source_sha256"]
        cursor.execute(
            """SELECT status,receipt FROM episode_store.media_previews
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND data_version_id=%s
            ORDER BY feature""",
            (*data.identity.scope, data.data_version_id),
        )
        previews = cursor.fetchall()
        assert len(previews) == 3 and all(status == "READY" for status, _ in previews)
        for _, preview in previews:
            sha = hashlib.sha256()
            for block in storage.read_chunks(preview["object_key"]):
                sha.update(block)
            assert sha.hexdigest() == preview["file"]["sha256"]
        cursor.execute(
            """SELECT published_at,publish_attempts FROM core.outbox_events
            WHERE organization_id=%s AND project_id=%s AND region_code=%s
              AND event_type='ingest.workflow.requested.v1' AND envelope->>'session_id' IS NULL
              AND envelope->'payload'->>'session_id'=%s""",
            (*data.identity.scope, source["session_id"]),
        )
        delivery = cursor.fetchone()
        assert delivery is not None and delivery[0] is not None, (
            "live worker has not acknowledged dispatch"
        )
        cursor.execute(
            """SELECT status,workflow_run_id FROM workflow.jobs
            WHERE organization_id=%s AND project_id=%s AND workflow_id=%s""",
            (
                data.identity.organization_id,
                data.identity.project_id,
                reference["job"]["workflow_id"],
            ),
        )
        job = cursor.fetchone()
        assert job is not None and job[0] == "SUCCEEDED"
        cursor.execute(
            "SELECT count(*) FROM lance_dataset_versions "
            "WHERE organization_id=%s AND project_id=%s AND dataset_id=%s",
            (data.identity.organization_id, data.identity.project_id, data.identity.dataset_id),
        )
        assert cursor.fetchone()[0] == 0
    report = {
        "synthetic_input": True,
        "data": data.model_dump(mode="json"),
        "package_files_verified": len(package.manifest.files),
        "original_raw_verified_unchanged": True,
        "raw_sha256": original[1],
        "raw_bytes": size,
        "source": source,
        "calibration": package.document("hc/calibration.json"),
        "outbox": {"published_at": delivery[0].isoformat(), "attempts": delivery[1]},
        "workflow": {"status": job[0], "run_id": job[1]},
        "lance_versions": 0,
        "previews_verified": [receipt for _, receipt in previews],
        "temporal_history_sha256": hashlib.sha256(
            (directory / "temporal-history.json").read_bytes()
        ).hexdigest(),
        "model_depth_training_support": "NOT_VALIDATED",
    }
    (directory / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "data_version_id": data.data_version_id,
                "verified_files": len(package.manifest.files),
                "raw_unchanged": True,
                "outbox": report["outbox"],
                "lance_versions": 0,
            }
        )
    )
finally:
    reset_request_context(token)
    packages.close()

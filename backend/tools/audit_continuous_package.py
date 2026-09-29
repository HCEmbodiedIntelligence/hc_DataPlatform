"""Read-only scoped evidence audit of the isolated continuous-package fixture."""

import hashlib
import json
import os
import sys
from pathlib import Path

import boto3

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.episode_store.models import DataVersionRef
from hc_data_platform.episode_store.service import EpisodePackageService
from hc_data_platform.ingest.adapters import S3ObjectStorage

assert os.environ["HC_TEMPORAL_NAMESPACE"] == "chayi-g1"
directory = Path(sys.argv[1])
reference = json.loads((directory / "services.json").read_text())
data = DataVersionRef.model_validate(reference["ready"]["data_ref"])
assert reference["synthetic_input"] is True and data.identity.organization_id.startswith("raw-pin-")
client = boto3.client(
    "s3",
    endpoint_url="http://minio:9000",
    aws_access_key_id="minio",
    aws_secret_access_key="minio-local-only",
    region_name="us-east-1",
)
storage = S3ObjectStorage(client, "hc-data-local")
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
        subject_id="g1-evidence-audit",
    )
)
try:
    package = packages.files.open(data.identity, data.data_version_id)
    assert package.receipt.data == data
    for entry in package.manifest.files:
        with package.file(entry.path):
            pass
    originals = []
    for item in reference["input"]["assets"]:
        if not item["relative_path"].startswith("source/original/"):
            continue
        sha, size = hashlib.sha256(), 0
        for block in storage.read_chunks(item["object_key"]):
            sha.update(block)
            size += len(block)
        assert (size, sha.hexdigest()) == (item["size_bytes"], item["content_sha256"])
        originals.append(dict(path=item["relative_path"], size=size, sha256=sha.hexdigest()))
    with packages.repository.transaction() as cursor:
        cursor.execute(
            """SELECT status,receipt FROM episode_store.media_previews
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND data_version_id=%s
            ORDER BY feature""",
            (*data.identity.scope, data.data_version_id),
        )
        previews = cursor.fetchall()
        assert len(previews) == 6 and all(status == "READY" for status, _ in previews)
        for _, preview in previews:
            sha = hashlib.sha256()
            for block in storage.read_chunks(preview["object_key"]):
                sha.update(block)
            assert sha.hexdigest() == preview["file"]["sha256"]
        cursor.execute(
            "SELECT count(*) FROM lance_dataset_versions WHERE project_id=%s AND dataset_id=%s",
            (data.identity.project_id, data.identity.dataset_id),
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            """SELECT published_at,publish_attempts FROM core.outbox_events
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND event_id=%s""",
            (*data.identity.scope, reference["ready"]["event_id"]),
        )
        delivered = cursor.fetchone()
        assert delivered is not None and delivered[0] is not None
        cursor.execute(
            """SELECT status,workflow_run_id FROM workflow.jobs
            WHERE organization_id=%s AND project_id=%s AND workflow_id=%s""",
            (
                data.identity.organization_id,
                data.identity.project_id,
                reference["ready"]["workflow_id"],
            ),
        )
        job = cursor.fetchone()
        assert job is not None and job[0] == "SUCCEEDED"
    report = {
        "synthetic_input": True,
        "scope": "actual isolated services; hardware/human acceptance pending",
        "data": data.model_dump(mode="json"),
        "ready": reference["ready"],
        "clock": reference["plan"]["metadata"]["source"]["clock"],
        "qc": reference["plan"]["metadata"]["quality"],
        "frame_count": reference["plan"]["frame_count"],
        "package_files_verified": len(package.manifest.files),
        "originals_verified_unchanged": originals,
        "lance_versions": 0,
        "outbox": {"published_at": delivered[0].isoformat(), "attempts": delivered[1]},
        "workflow": {"status": job[0], "run_id": job[1]},
        "previews_verified": [preview for _, preview in previews],
        "temporal_history_sha256": hashlib.sha256(
            (directory / "temporal-history.json").read_bytes()
        ).hexdigest(),
        "model_depth_training": "NOT_VALIDATED",
    }
    (directory / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "data_version_id": data.data_version_id,
                "package_files": len(package.manifest.files),
                "originals_verified": len(originals),
                "previews_verified": len(previews),
                "lance_versions": 0,
            }
        )
    )
finally:
    reset_request_context(token)
    packages.close()

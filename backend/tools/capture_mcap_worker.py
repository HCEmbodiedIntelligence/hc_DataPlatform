"""Copy verified output/history of a synthetic MCAP processed by the G1 daemon.

All service access is read-only. The evidence copy is a local output artifact.
"""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

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
organization = sys.argv[1]
assert organization.startswith("mcap-") and len(organization.removeprefix("mcap-")) == 32
scope = (organization, "mcap-project-" + organization.removeprefix("mcap-"), "cn-test")
root = Path(os.environ["HC_G1_WRITER_EVIDENCE_ROOT"]) / "mcap-package-live" / organization
connect = psycopg_connection_factory(os.environ["HC_G1_TEST_POSTGRES_DSN"])
_, _, storage = _s3(
    Settings(object_store_endpoint="http://minio:9000", object_store_bucket="hc-data-local")
)
packages = EpisodePackageService(connect, storage, root / "cache")
token = bind_request_context(
    RequestContext(
        organization_id=scope[0],
        project_id=scope[1],
        region_code=scope[2],
        service_identity=True,
        subject_id="g1-worker-evidence",
    )
)
try:
    with packages.repository.transaction() as cursor:
        cursor.execute(
            """SELECT input_document,receipt_document FROM episode_store.versions
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND status='COMMITTED'""",
            scope,
        )
        rows = cursor.fetchall()
        assert len(rows) == 1
        source, receipt = rows[0]
        data = DataVersionRef.model_validate(receipt["data"])
        cursor.execute(
            """SELECT workflow_id,status,result FROM workflow.jobs
            WHERE organization_id=%s AND project_id=%s AND resource_id=%s""",
            (*scope[:2], data.identity.rollout_id),
        )
        workflow_id, status, result = cursor.fetchone()
        assert status == "SUCCEEDED"
    package = packages.files.open(data.identity, data.data_version_id)
    assert (
        package.document("hc/source.json")["original_manifest"]["recorder_version"]
        == "hc-g1-synthetic-mcap/1"
    )
    root.mkdir(parents=True, exist_ok=True)
    for entry in package.manifest.files:
        target = root / "episode" / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        with package.file(entry.path) as path:
            shutil.copyfile(path, target)
    (root / "manifest.json").write_text(package.manifest.model_dump_json(indent=2))

    async def history():
        client = await Client.connect(
            os.environ["HC_TEMPORAL_TARGET"],
            namespace="chayi-g1",
            data_converter=pydantic_data_converter,
        )
        value = await client.get_workflow_handle(workflow_id).fetch_history()
        (root / "temporal-history.json").write_text(value.to_json())

    asyncio.run(history())
    evidence = {
        "fixture": "synthetic-MCAP-RGB-16UC1-32FC1",
        "organization_id": organization,
        "execution": "unmodified isolated daemon; no injected activity dependencies",
        "job": {"workflow_id": workflow_id, "status": status, "result": result},
        "source": source,
        "ready": {"data": data.model_dump(mode="json")},
        "source_sha256": source["source_sha256"],
    }
    (root / "services.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps(
            {
                "directory": str(root),
                "workflow_status": status,
                "data_version_id": data.data_version_id,
            }
        )
    )
finally:
    reset_request_context(token)
    packages.close()

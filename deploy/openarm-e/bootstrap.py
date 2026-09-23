"""One-time synthetic E target and robot credential bootstrap.

Run inside E migration image. Writes the token to a mode-0600 platform-only
transfer file; never prints it or records it in the evidence directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg

from hc_data_platform.annotation.models import TagSchemaDocument, TagSchemaTarget
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import RequestContext, bind_request_context, reset_request_context
from hc_data_platform.robot_ingest.models import CreateRobotIngestIdentity, IssueCredentialCommand
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.runtime import build_runtime

ORG = "openarm-e-synthetic"
PROJECT = "openarm-e-project"
REGION = "openarm-e-region"
ROBOT = "synthetic-platform-robot"
TASK = "synthetic-task-A"
DATASET = "dataset_openarm_e_synthetic"


def main() -> None:
    dsn = os.environ["HC_POSTGRES_DSN"].replace("postgresql+asyncpg://", "postgresql://")
    token_path = Path(os.environ["E_TOKEN_FILE"])
    if token_path.exists():
        raise RuntimeError("E credential already exists; keep the original queue and credential")
    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT set_config('app.platform_admin','true',false)")
        connection.execute(
            "INSERT INTO registry.organization_projects (organization_id,project_id,display_name) "
            "VALUES (%s,%s,'OpenArm E synthetic') ON CONFLICT DO NOTHING", (ORG, PROJECT))
        connection.execute(
            """INSERT INTO robotics.robot_assets
               (organization_id,robot_id,display_name,serial_no,lifecycle_status,
                connectivity_state,etag,topology_revision)
               VALUES (%s,%s,%s,%s,'ACTIVE','ONLINE','openarm-e','openarm-e')
               ON CONFLICT DO NOTHING""", (ORG, ROBOT, ROBOT, ROBOT))
        connection.execute(
            """INSERT INTO collection_tasks.collection_tasks
               (collection_task_id,organization_id,project_id,dataset_id,task_code,name,
                task_type,scenario,status,create_fingerprint,upload_region_code)
               VALUES (%s,%s,%s,%s,'93000001','OpenArm E synthetic','ROBOT_CAPTURE',
               'synthetic','ACTIVE',%s,%s) ON CONFLICT DO NOTHING""",
            (TASK, ORG, PROJECT, DATASET, "e" * 64, REGION))

    scope = RequestContext(organization_id=ORG, project_id=PROJECT, region_code=REGION,
                           service_identity=True, subject_id="openarm-e-bootstrap")
    context_token = bind_request_context(scope)
    try:
        actor = AuthContext(
            subject_id="openarm-e-bootstrap", organization_ids=frozenset({ORG}),
            project_ids=frozenset({PROJECT}), region_codes=frozenset({REGION}),
            organization_scope_triples=frozenset({(ORG, PROJECT, REGION), (ORG, PROJECT, None)}),
            scope_pairs=frozenset({(PROJECT, REGION), (PROJECT, None)}),
            capabilities=frozenset({"data_schema.publish", "data_schema.read"}),
            organization_scoped_capabilities=frozenset({
                (ORG, PROJECT, "ingest_source.manage"), (ORG, PROJECT, "ingest_source.read")}),
        )
        settings = Settings(
            environment="test", postgres_dsn=os.environ["HC_POSTGRES_DSN"],
            object_store_endpoint="http://minio:9000", object_store_public_endpoint="http://minio:9000",
            object_store_bucket="hc-e-accept", auth_abuse_enabled=False,
            robot_model_asset_root="/tmp/openarm-e-models",
            alignment_staging_root="/tmp/openarm-e-alignment",
            aligned_media_staging_root="/tmp/openarm-e-media",
            lance_root_uri="/tmp/openarm-e-lance",
        )
        runtime = build_runtime(settings, include_media=True)
        schema = runtime.annotation.create_tag_schema_version(
            project_id=PROJECT, name="OpenArm E outcomes", actor=actor,
            schema_id="openarm-e-outcomes",
            document=TagSchemaDocument(nodes=tuple(
                {"tag_id": value, "code": value, "display_name": value}
                for value in ("success", "failure"))),
            compatible_targets=(TagSchemaTarget(
                region_code=REGION, dataset_id=DATASET,
                dataset_schema_snapshot_id="native-v1", task_kind="TAGGING"),),
        )
        runtime.annotation.publish_tag_schema_version(
            project_id=PROJECT, schema_id=schema.schema_id,
            version=schema.version, actor=actor)
        identity = runtime.robot_ingest.create_identity(
            auth=actor, organization_id=ORG, project_id=PROJECT,
            command=CreateRobotIngestIdentity(
                robot_id=ROBOT, allowed_formats=("LEROBOT_V3",),
                allowed_transports=("HTTP", "HTTPS")),
        ).data
        credential = runtime.robot_ingest.issue_credential(
            auth=actor, organization_id=ORG, project_id=PROJECT,
            ingest_identity_id=identity.ingest_identity_id,
            command=IssueCredentialCommand(),
        ).credential
        token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(token_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(credential.token + "\n")
        print(json.dumps({"organization_id": ORG, "project_id": PROJECT,
                          "region_code": REGION, "robot_id": ROBOT, "task_id": TASK,
                          "dataset_id": DATASET, "credential_file": str(token_path)}))
    finally:
        reset_request_context(context_token)


if __name__ == "__main__":
    main()

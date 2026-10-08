"""G1-only fixture/configuration setup and read-only receipt audit.

The installed independent uploader must perform upload/commit over HTTP. This
helper never inserts Rawdata, Episode results, jobs, or package publications.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from hc_data_platform.annotation.models import TagSchemaDocument, TagSchemaTarget
from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import RequestContext, bind_request_context
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.robot_ingest.models import CreateRobotIngestIdentity, IssueCredentialCommand
from hc_data_platform.runtime import build_runtime
from hc_data_platform.security.auth import AuthContext
from tests.robot_ingest.native_client_fixture import make_robot_native_rgbd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "inspect", "diagnose-plan"])
    parser.add_argument("--run", required=True)
    parser.add_argument("--host-backend", required=True, type=Path)
    args = parser.parse_args()
    if not args.run.replace("-", "").isalnum():
        raise ValueError("invalid run name")
    settings = Settings()
    if (
        os.environ.get("HC_TEMPORAL_NAMESPACE") != "chayi-g1"
        or settings.data_source_credential_key.get_secret_value() != "isolated-chayi-g1-source"
        or str(settings.postgres_dsn) != "postgresql+asyncpg://hc:hc@postgres:5432/hc_data"
    ):
        raise RuntimeError("requires explicit isolated G1 services; no production fallback")
    root = Path("/app/.g1-artifacts/robot-native") / args.run
    host = args.host_backend / ".g1-artifacts/robot-native" / args.run
    if args.operation == "prepare":
        root.mkdir(parents=True, exist_ok=False)
        suffix = uuid4().hex[:12]
        ids = dict(
            organization_id="r3-org-" + suffix,
            project_id="r3-project-" + suffix,
            region_code="chayi-cn",
            robot_id="r3-synthetic-" + suffix,
            task_id="r3-task-" + suffix,
            local_robot_id="r3-synthetic-local",
            dataset_id="dataset_task_" + hashlib.sha256(suffix.encode()).hexdigest()[:32],
        )
        (root / "identity.json").write_text(json.dumps(ids, indent=2) + "\n")
    else:
        ids = json.loads((root / "identity.json").read_text())
    org, project, region = (ids[k] for k in ("organization_id", "project_id", "region_code"))
    bind_request_context(
        RequestContext(
            organization_id=org,
            project_id=project,
            region_code=region,
            subject_id="r3-probe",
            request_id=args.run,
        )
    )
    connect = psycopg_connection_factory(str(settings.postgres_dsn).replace("+asyncpg", ""))
    runtime = build_runtime(
        settings.model_copy(
            update={
                "alignment_staging_root": str(root / "reader-cache"),
                "aligned_media_staging_root": str(root / "media-cache"),
                "robot_model_asset_root": str(root / "models"),
            }
        )
    )
    if args.operation in {"inspect", "diagnose-plan"}:
        inspect(root, ids, connect, runtime, diagnose=args.operation == "diagnose-plan")
        return
    with connect() as connection:
        connection.execute(
            "INSERT INTO registry.organization_projects VALUES (%s,%s,%s)",
            (org, project, "G1 R3 synthetic client probe"),
        )
        connection.execute(
            """INSERT INTO robotics.robot_assets
            (organization_id,robot_id,display_name,serial_no,lifecycle_status,connectivity_state,etag,topology_revision)
            VALUES (%s,%s,'Synthetic software fixture',%s,'ACTIVE','ONLINE',
                    'synthetic','synthetic')""",
            (org, ids["robot_id"], ids["robot_id"]),
        )
        connection.execute(
            """INSERT INTO collection_tasks.collection_tasks
            (collection_task_id,organization_id,project_id,dataset_id,task_code,name,task_type,
             scenario,status,create_fingerprint,upload_region_code)
            VALUES (%s,%s,%s,%s,%s,'G1 R3 synthetic','ROBOT_CAPTURE','synthetic',
                    'ACTIVE',%s,%s)""",
            (
                ids["task_id"],
                org,
                project,
                ids["dataset_id"],
                str(int(ids["task_id"].rsplit("-", 1)[1], 16) % 100_000_000).zfill(8),
                "a" * 64,
                region,
            ),
        )
    actor = AuthContext(
        subject_id="r3-probe",
        organization_ids=frozenset({org}),
        project_ids=frozenset({project}),
        region_codes=frozenset({region}),
        scope_pairs=frozenset({(project, region), (project, None)}),
        organization_scope_triples=frozenset({(org, project, region), (org, project, None)}),
        capabilities=frozenset(
            {"data_schema.publish", "data_schema.read", "dataset_version.publish"}
        ),
        organization_scoped_capabilities=frozenset(
            (org, project, cap) for cap in ("ingest_source.read", "ingest_source.manage")
        ),
    )
    schema = runtime.annotation.create_tag_schema_version(
        project_id=project,
        name="Synthetic outcomes",
        actor=actor,
        schema_id="r3-labels-" + args.run,
        document=TagSchemaDocument(
            nodes=tuple(dict(tag_id=t, code=t, display_name=t) for t in ("success", "failure"))
        ),
        compatible_targets=(
            TagSchemaTarget(
                region_code=region,
                dataset_id=ids["dataset_id"],
                dataset_schema_snapshot_id="native-v1",
                task_kind="TAGGING",
            ),
        ),
    )
    runtime.annotation.publish_tag_schema_version(
        project_id=project, schema_id=schema.schema_id, version=schema.version, actor=actor
    )
    identity = runtime.robot_ingest.create_identity(
        auth=actor,
        organization_id=org,
        project_id=project,
        command=CreateRobotIngestIdentity(
            robot_id=ids["robot_id"],
            allowed_formats=("LEROBOT_V3",),
            allowed_transports=("HTTP", "HTTPS"),
        ),
    ).data
    credential = runtime.robot_ingest.issue_credential(
        auth=actor,
        organization_id=org,
        project_id=project,
        ingest_identity_id=identity.ingest_identity_id,
        command=IssueCredentialCommand(),
    ).credential
    descriptor = os.open(root / "credential", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(credential.token)
    make_robot_native_rgbd(
        root / "inputs/episode",
        robot_id=ids["robot_id"],
        task_id=ids["task_id"],
        local_robot_id=ids["local_robot_id"],
    )
    config = {key: ids[key] for key in ("robot_id", "local_robot_id", "task_id")}
    config.update(
        platform_base_url="http://127.0.0.1:48000",
        credential_ref="file:" + str(host / "credential"),
        data_directory=str(host / "inputs"),
        queue_directory=str(host / "queue"),
        processing_endpoint=True,
        poll_seconds=1,
        bandwidth_bytes_per_second=16 * 1024**2,
    )
    (root / "uploader.json").write_text(json.dumps(config, indent=2) + "\n")
    print(
        json.dumps(
            {
                "synthetic": True,
                "run": args.run,
                "config": str(host / "uploader.json"),
                "input": str(host / "inputs/episode"),
            }
        )
    )


def inspect(root, ids, connect, runtime, *, diagnose=False):
    # Read-only evidence; no emulated READY receipts or server state changes.
    with connect() as connection:
        rows = connection.execute(
            """SELECT raw_source_id,episode_id,status,quality_status,
            frame_count,sample_count,lance_version,data_ref FROM ingest.raw_source_episodes
            WHERE organization_id=%s AND project_id=%s""",
            (ids["organization_id"], ids["project_id"]),
        ).fetchall()
        evidence = {
            "synthetic": True,
            "episodes": [
                dict(
                    zip(
                        (
                            "raw_source_id",
                            "episode_id",
                            "status",
                            "quality_status",
                            "frame_count",
                            "sample_count",
                            "lance_version",
                            "data_ref",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in rows
            ],
        }
        evidence["jobs"] = [
            dict(zip(("workflow_id", "status", "error_code", "error_message"), row, strict=True))
            for row in connection.execute(
                "SELECT workflow_id,status,error_code,error_message "
                "FROM workflow.jobs WHERE organization_id=%s AND project_id=%s",
                (ids["organization_id"], ids["project_id"]),
            ).fetchall()
        ]
    pipeline = runtime.activities.lerobot_pipeline
    if evidence["jobs"]:
        from temporalio.client import Client
        from temporalio.contrib.pydantic import pydantic_data_converter

        async def histories():
            client = await Client.connect(
                os.environ["HC_TEMPORAL_TARGET"],
                namespace="chayi-g1",
                data_converter=pydantic_data_converter,
            )
            for job in evidence["jobs"]:
                history = await client.get_workflow_handle(job["workflow_id"]).fetch_history()
                body = history.to_json()
                (root / (job["workflow_id"] + ".history.json")).write_text(body)
                messages = []
                for event in history.events:
                    if event.HasField("activity_task_failed_event_attributes"):
                        failure = event.activity_task_failed_event_attributes.failure
                        while failure:
                            messages.append(failure.message)
                            if not failure.HasField("cause"):
                                break
                            failure = failure.cause
                job["activity_failure_messages"] = messages

        asyncio.run(histories())
    if rows:
        from hc_data_platform.lerobot_imports.reader import read_episode

        raw = pipeline.raw.get_source(
            organization_id=ids["organization_id"],
            project_id=ids["project_id"],
            region_code=ids["region_code"],
            raw_source_id=rows[0][0],
        )
        manifest, _ = pipeline._read_manifest(raw)
        with pipeline.cache.pin(raw.content_hash):
            localized = pipeline._localize(raw, manifest, 0)
            read_episode(localized, 0, preserve_features=True)
        evidence["committed_raw_readable"] = True
        if rows[0][2] == "READY":
            import pyarrow.parquet as pq

            from hc_data_platform.data_formats.depth_evidence import file_hash
            from hc_data_platform.episode_store.download import complete_archive
            from hc_data_platform.episode_store.models import DataVersionRef
            from hc_data_platform.episode_store.validation import validate_package
            from hc_data_platform.robot_ingest.processing_contract import processing_result

            assert len(rows) == 1 and rows[0][3] == "PASS" and rows[0][6] is None
            data = DataVersionRef.model_validate(rows[0][7])
            upload = runtime.robot_ingest.get_upload(
                token=(root / "credential").read_text(), upload_id=raw.upload_id
            ).data
            _, document = runtime.robot_processing.read(upload)
            result = processing_result(upload, document)
            assert result.terminal and result.processing_status == "READY"
            package = runtime.episode_packages.files.open(data.identity, data.data_version_id)
            assert document.episodes[0].receipt.package_receipt == package.receipt
            path, temporary = complete_archive(package, root / "downloads")
            try:
                offline = root / "offline" / data.data_version_id
                if not offline.exists():
                    with zipfile.ZipFile(path) as archive:
                        archive.extractall(offline)
                downloaded = offline / "episode"
                validate_package(downloaded, package.manifest.input)
                shutil.copyfile(path, root / "episode.zip")
            finally:
                temporary.cleanup()
            videos = {}
            for camera in ("head", "head_depth"):
                relative = f"videos/observation.images.{camera}/chunk-000/file-000.mp4"
                assert file_hash(downloaded / relative) == file_hash(
                    root / "inputs/episode" / relative
                )
                videos[camera] = file_hash(downloaded / relative)
            original_rows = pq.read_table(root / "inputs/episode/data").to_pylist()
            package_rows = pq.read_table(downloaded / "data").to_pylist()
            for feature in ("observation.state", "action", "observation.extra.matrix"):
                assert [r[feature] for r in original_rows] == [r[feature] for r in package_rows]
            modalities = json.loads((downloaded / "hc/modalities.json").read_text())
            depth_root = (
                downloaded / modalities["depth"]["observation.images.head_depth"]["evidence_path"]
            )
            for source_file in (root / "inputs/episode/hc/depth/head").rglob("*"):
                if source_file.is_file():
                    assert (
                        source_file.read_bytes()
                        == (
                            depth_root
                            / source_file.relative_to(root / "inputs/episode/hc/depth/head")
                        ).read_bytes()
                    )
            depth_report = json.loads((depth_root / "report.json").read_text())
            for item in manifest["files"]:
                received = b"".join(pipeline.storage.read_chunks(item["object_key"]))
                assert hashlib.sha256(received).hexdigest() == item["sha256"]
                assert received == (root / "inputs/episode" / item["path"]).read_bytes()
            with connect() as connection:
                assert (
                    connection.execute(
                        "SELECT count(*) FROM lance_rollout_lineage WHERE organization_id=%s",
                        (ids["organization_id"],),
                    ).fetchone()[0]
                    == 0
                )
                assert connection.execute(
                    "SELECT data_ref,base_lance_version FROM annotation.annotation_tasks "
                    "WHERE organization_id=%s AND rollout_id=%s",
                    (ids["organization_id"], rows[0][1]),
                ).fetchone() == (data.model_dump(mode="json"), None)
            evidence.update(
                data=data.model_dump(mode="json"),
                upload_id=raw.upload_id,
                offline_package=str(downloaded),
                rgb_key="observation.images.head",
                fps=30,
                public_processing=result.model_dump(mode="json"),
                lance_lineage_count=0,
                raw_assets_unchanged=len(manifest["files"]),
                video_sha256=videos,
                parquet_numeric_values_exact=True,
                depth_evidence_unchanged=True,
                depth_encoding=depth_report["video_encoding"],
                depth_quantization=depth_report["quantization"],
                download_via="authorized package service ZIP; human HTTP download not exercised",
                model_depth_training_support="NOT_VALIDATED",
            )
        if diagnose:
            from hc_data_platform.lerobot_imports.committed import discover_committed_source

            task = discover_committed_source(pipeline.storage, raw).plan.episode_tasks[0]
            task = task.model_copy(
                update={
                    "source": task.source.model_copy(update={"platform_episode_id": rows[0][1]})
                }
            )
            # Runs the normal idempotent plan builder, not processing/publication.
            pipeline.prepare(task)
            evidence["diagnostic_plan_valid"] = True
    (root / "receipt-audit.json").write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(json.dumps(evidence, default=str))


if __name__ == "__main__":
    main()

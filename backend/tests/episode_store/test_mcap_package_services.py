"""Real G1 upload/PG/MinIO/Temporal; synthetic input, not sensor acceptance."""

import asyncio
import hashlib
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, Worker

from hc_data_platform.core.config import Settings
from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import psycopg_connection_factory
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.episode_store.models import EpisodeIdentity
from hc_data_platform.ingest.models import CompletedPart
from hc_data_platform.quality.models import QualityProfileV1
from hc_data_platform.runtime import _s3, build_runtime
from hc_data_platform.workflow import activities
from hc_data_platform.workflow.ingest_dispatch import IngestOutboxHandler, IngestWorkflowPlanBlocked
from hc_data_platform.workflow.models import JobRecord
from hc_data_platform.workflow.service import TemporalWorkflowLauncher
from hc_data_platform.workflow.temporal_workflows import IngestRolloutWorkflow
from tests.episode_store.mcap_package_fixture import TOPICS, make_mcap
from tests.episode_store.test_annotation_bindings_services import publish_schema
from tests.episode_store.test_recording_package_services import ForbiddenLegacy


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("missing_units,legacy", [(False, False), (True, False), (False, True)])
async def test_mcap_upload_temporal_package(
    isolated_dsn, tmp_path, monkeypatch, missing_units, legacy
):
    assert os.environ["HC_TEMPORAL_NAMESPACE"] == "chayi-g1"
    suffix = uuid4().hex
    org, project, region = "mcap-" + suffix, "mcap-project-" + suffix, "cn-test"
    dataset, task = "dataset_" + suffix, "mcap-task-" + suffix
    queue = "chayi-g1-mcap-" + suffix
    monkeypatch.setenv("HC_MEDIA_TEMPORAL_TASK_QUEUE", queue)
    settings = Settings(
        environment="test",
        postgres_dsn=isolated_dsn.replace("postgresql://", "postgresql+asyncpg://"),
        auth_abuse_enabled=False,
        object_store_endpoint="http://minio:9000",
        object_store_public_endpoint="http://minio:9000",
        object_store_bucket="hc-data-local",
        lance_root_uri=str(tmp_path / "lance"),
        alignment_staging_root=str(tmp_path / "alignment"),
        aligned_media_staging_root=str(tmp_path / "media"),
        media_temporal_task_queue=queue,
        robot_model_asset_root=str(tmp_path / "models"),
    )
    runtime = build_runtime(settings, include_media=True)
    _, _, storage = _s3(settings)
    connect = psycopg_connection_factory(isolated_dsn)
    context = RequestContext(
        organization_id=org,
        project_id=project,
        region_code=region,
        subject_id="g1-test",
        request_id=suffix,
        service_identity=True,
    )
    token = bind_request_context(context)
    try:
        body, manifest = make_mcap(project, task, suffix, missing_units=missing_units)
        quality = QualityProfileV1(
            profile_id="mcap-package",
            required_topics=frozenset(TOPICS),
        )
        with connect() as connection:
            connection.execute(
                "INSERT INTO registry.organization_projects VALUES (%s,%s,%s)",
                (org, project, "G1 synthetic MCAP"),
            )
            connection.execute(
                """INSERT INTO collection_tasks.collection_tasks
                (organization_id,project_id,collection_task_id,dataset_id,task_code,name,task_type,
                 scenario,description,target_json,status,version,create_fingerprint,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,'G1 MCAP','ROBOT','synthetic','',
                        '{"package_count":1}','ACTIVE',1,%s,now(),now())""",
                (
                    org,
                    project,
                    task,
                    dataset,
                    str(int(suffix, 16) % 100_000_000).zfill(8),
                    "a" * 64,
                ),
            )
            connection.execute(
                """INSERT INTO quality_profiles
                (project_id,profile_id,profile_version,schema_version,profile_sha256,profile_json)
                VALUES (%s,%s,1,%s,%s,%s::jsonb)""",
                (
                    project,
                    quality.profile_id,
                    quality.schema_version,
                    quality.content_sha256(),
                    quality.content_bytes().decode(),
                ),
            )
        identity = EpisodeIdentity(
            organization_id=org,
            project_id=project,
            region_code=region,
            dataset_id=dataset,
            episode_id="episode_test",
            rollout_id=manifest.rollout_id,
        )
        publish_schema(
            SimpleNamespace(identity=identity, schema_snapshot_id="mcap-v1"), runtime.annotation
        )
        service = runtime.ingest
        upload = service.create_session(
            manifest=manifest, region_code=region, idempotency_key=suffix
        )
        authorization = service.renew_part_authorizations(upload.session_id, [1])[0]
        with urlopen(Request(authorization.url, data=body, method="PUT")) as response:
            etag = response.headers["ETag"]
        service.complete_upload(upload.session_id, [CompletedPart(part_number=1, etag=etag)])
        service.commit_manifest(
            organization_id=org, session_id=upload.session_id, manifest=manifest
        )
        with connect() as connection:
            row = connection.execute(
                """SELECT envelope FROM core.outbox_events
                WHERE organization_id=%s AND event_type='ingest.workflow.requested.v1'""",
                (org,),
            ).fetchone()
        event = DomainEventEnvelope.model_validate(row[0])
        resolver = runtime.activities.ingest_projection
        resolver._catalog = ForbiddenLegacy()
        if legacy:
            original_resolve = resolver.resolve

            def historical_plan(**kwargs):
                current = original_resolve(**kwargs)
                return current.model_copy(
                    update={
                        "alignment": current.alignment.model_copy(
                            update={
                                "profile": current.alignment.profile.model_copy(
                                    update={
                                        "converter_version": "mcap-episode-package/1",
                                    }
                                ),
                            }
                        )
                    }
                )

            monkeypatch.setattr(resolver, "resolve", historical_plan)
        request = resolver.resolve(
            project_id=project,
            region_code=region,
            session_id=upload.session_id,
            rollout_id=manifest.rollout_id,
            data_package_id=manifest.data_package_id,
        )
        assert request.alignment.storage_format == "lerobot_v3"
        for update, reason in (
            ({"organization_id": "different-tenant"}, "Worker scope"),
            ({"object_key": "unauthorized-object"}, "changed after dispatch"),
        ):
            with (
                pytest.raises(IngestWorkflowPlanBlocked, match=reason),
                resolver.open_local_session(request.alignment.source.model_copy(update=update)),
            ):
                pytest.fail("forged MCAP source was localized")
        monkeypatch.setattr(
            activities, "_dependencies", replace(runtime.activities, catalog=ForbiddenLegacy())
        )
        projector = runtime.activities.dataset_ingest_projection
        project_real, attempts = projector.project, []

        def lose_response(**arguments):
            result = project_real(**arguments)
            attempts.append(1)
            if len(attempts) == 1:
                raise InterruptedError("lost MCAP package projection response")
            return result

        monkeypatch.setattr(projector, "project", lose_response)
        encoded, encode = [], runtime.episode_packages.previews._encode

        def preview(*args, **kwargs):
            encoded.append(1)
            return encode(*args, **kwargs)

        monkeypatch.setattr(runtime.episode_packages.previews, "_encode", preview)
        captured = {}
        original_commit = runtime.episode_packages.write_and_publish

        def capture(*args, **kwargs):
            captured["source"] = args[0]
            captured["result"] = original_commit(*args, **kwargs)
            return captured["result"]

        monkeypatch.setattr(runtime.episode_packages, "write_and_publish", capture)
        client = await Client.connect(
            os.environ["HC_TEMPORAL_TARGET"],
            namespace="chayi-g1",
            data_converter=pydantic_data_converter,
        )
        handler = IngestOutboxHandler(
            TemporalWorkflowLauncher("unused", task_queue=queue, client=client), resolver
        )
        async with Worker(
            client,
            task_queue=queue,
            workflows=[IngestRolloutWorkflow],
            activities=activities.ALL_ACTIVITIES,
        ):
            started = await handler(event)
            handle = client.get_workflow_handle(started.workflow_id)
            try:
                job = JobRecord.model_validate(await asyncio.wait_for(handle.result(), timeout=100))
            except BaseException:
                await handle.cancel()
                raise
            history = await handle.fetch_history()
        await Replayer(
            workflows=[IngestRolloutWorkflow], data_converter=pydantic_data_converter
        ).replay_workflow(history)
        terminal = await handler(event)
        assert terminal["status"] == job.status.value
        with pytest.raises(ValueError, match="event lineage"):
            await handler(event.model_copy(update={"organization_id": "different-tenant"}))
        with pytest.raises(IngestWorkflowPlanBlocked, match="persisted scoped trigger"):
            await handler(
                event.model_copy(update={"payload": {**event.payload, "session_id": str(uuid4())}})
            )
        if missing_units:
            assert job.status.value == "TECHNICAL_FAILED", job
            assert not captured and not encoded
            with connect() as connection:
                assert (
                    connection.execute(
                        "SELECT count(*) FROM episode_store.versions WHERE organization_id=%s",
                        (org,),
                    ).fetchone()[0]
                    == 0
                )
        else:
            assert job.status.value == "SUCCEEDED", job.model_dump()
            # RGB H264 is reused; only the two SDK depth previews are encoded.
            assert len(attempts) == 2 and len(encoded) == 2
            source = captured["source"]
            assert len(source.required_depth_features) == 2
            version, ready = captured["result"]
            assert ready.data.storage_format == "lerobot_v3"
            root = Path(os.environ["HC_G1_WRITER_EVIDENCE_ROOT"]) / "mcap-package" / org
            root.mkdir(parents=True)
            package = runtime.episode_packages.files.open(source.identity, source.data_version_id)
            info = package.document("meta/info.json")
            import pyarrow.parquet as pq

            from hc_data_platform.data_formats.lerobot_layout import DATA_PATH

            with package.file(DATA_PATH) as file:
                table = pq.read_table(file, columns=["action", "observation.state"])
            for feature in ("action", "observation.state"):
                assert info["features"][feature]["dtype"] == ("string" if legacy else "float64")
            if not legacy:
                assert info["features"]["action"]["shape"] == [2]
                assert info["features"]["observation.state"]["names"] is None
                rows = table.to_pylist()
                assert [r["action"] for r in rows] == [[i / 100, i / 200] for i in range(30)]
                assert [r["observation.state"] for r in rows] == [
                    [i / 100, -i / 100] for i in range(30)
                ]
                with package.file("hc/recording/mcap_numeric.jsonl") as file:
                    originals = [json.loads(line) for line in file.read_text().splitlines()]
                assert originals[12]["messages"]["/joint_states"] == {"position": [0.12, -0.12]}
                assert originals[12]["messages"]["/action"] == {"value": [0.12, 0.06]}

            import httpx

            from tests.episode_store.test_http_services import register_and_grant

            with httpx.Client(base_url="http://gateway:8080", timeout=45, trust_env=False) as http:
                _, headers = register_and_grant(
                    http,
                    runtime.episode_packages.repository,
                    source.identity,
                    capabilities=["upload.manage", "dataset_version.read", "episode.read"],
                )
                base = f"/api/v1/projects/{project}/regions/{region}"
                status_path = base + f"/upload-sessions/{upload.session_id}/processing"
                status = http.get(status_path, headers=headers)
                assert status.status_code == 200, status.text
                result = status.json()
                assert result["aligned_media"] is None and result["status"] == "SUCCEEDED"
                assert result["episode"]["data_version_id"] == ready.data.data_version_id
                assert result["episode"]["content_hash"] == ready.data.content_hash
                assert result["episode"]["fps"] == 30
                assert result["episode"]["frame_count"] == 30
                assert "package_prefix" not in status.text
                assert http.get(status_path).status_code == 401
                assert (
                    http.get(
                        status_path, headers={**headers, "X-Organization-Id": "foreign"}
                    ).status_code
                    == 403
                )
                viewer = result["viewer"]
                bootstrap = http.get(
                    f"/api/v1/projects/{project}/datasets/{dataset}/versions/{viewer['version_id']}/bootstrap",
                    headers=headers,
                )
                assert bootstrap.status_code == 200, bootstrap.text
                params = {"snapshot_token": bootstrap.json()["data"]["snapshot_token"]}
                logical_path = (
                    base
                    + f"/datasets/{dataset}/versions/{viewer['version_id']}"
                    + f"/episode-revisions/{viewer['revision_id']}/logical"
                )
                logical = http.get(logical_path, params=params, headers=headers)
                assert logical.status_code == 200, logical.text
                streams = {s["name"]: s for s in logical.json()["snapshot"]["streams"]}
                for topic, kind in [("/joint_states", "JOINT_STATE"), ("/action", "ACTION")]:
                    stream = streams[topic]
                    assert stream["kind"] == ("EVENT" if legacy else kind)
                    assert stream["value_kind"] == ("EVENT" if legacy else "VECTOR")
                    samples = http.get(
                        logical_path + f"/streams/{stream['stream_id']}/samples",
                        headers=headers,
                        params={
                            **params,
                            "snapshot_id": logical.json()["snapshot_id"],
                            "start": 0,
                            "end": 30,
                        },
                    )
                    assert samples.status_code == 200, samples.text
                    if not legacy:
                        expected = [
                            [i / 100, -i / 100] if topic == "/joint_states" else [i / 100, i / 200]
                            for i in range(30)
                        ]
                        assert [
                            row["value"]["values"] for row in samples.json()["samples"]
                        ] == expected
            from hc_data_platform.episode_store.quality import read_episode_quality

            qc = read_episode_quality(package)
            assert (
                qc.evidence_status == "AVAILABLE"
                and qc.evidence.report_sha256 == qc.report.content_sha256
            )
            assert any(rule.status == "skip" for rule in qc.rules)
            for entry in package.manifest.files:
                target = root / "episode" / entry.path
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.file(entry.path) as file:
                    shutil.copyfile(file, target)
            (root / "manifest.json").write_text(package.manifest.model_dump_json(indent=2))
            (root / "temporal-history.json").write_text(history.to_json())
            (root / "services.json").write_text(
                json.dumps(
                    {
                        "fixture": "synthetic-MCAP-RGB-16UC1-32FC1",
                        "organization_id": org,
                        "job": job.model_dump(mode="json"),
                        "source": source.model_dump(mode="json"),
                        "version": version.model_dump(mode="json"),
                        "ready": ready.model_dump(mode="json"),
                        "source_sha256": manifest.sha256,
                        "commit_projection_attempts": len(attempts),
                        "official_preview_encodes": len(encoded),
                    },
                    indent=2,
                )
            )
        raw = service.authorize_raw_media(
            session_id=upload.session_id, actor_id="g1-test", request_id=suffix
        )
        with urlopen(raw.download_url) as response:
            assert hashlib.sha256(response.read()).hexdigest() == manifest.sha256
        with connect() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM lance_dataset_versions WHERE organization_id=%s", (org,)
                ).fetchone()[0]
                == 0
            )
    finally:
        runtime.episode_packages.close()
        reset_request_context(token)


@pytest.fixture
def isolated_dsn():
    value = os.getenv("HC_G1_TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("explicit G1 database required")
    return value

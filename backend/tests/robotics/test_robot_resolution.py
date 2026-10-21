"""Real database tests for new associations, provenance and resumable cleanup."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.robot_assets.resolution import (
    PostgresRobotResolution,
    RobotResolutionCommand,
)
from hc_data_platform.robot_processing import service as processing_service
from tests.robot_ingest.test_raw_derivation_services import uploaded_raw  # noqa: F401
from tests.robot_processing.test_configuration import draft, publish
from tests.robotics.test_configured_robot_deletion import robot  # noqa: F401
from tests.robotics.test_robot_deletion_check import actor, setup

pytest_plugins = ["tests.episode_store.test_commit_services"]
pytestmark = pytest.mark.integration


def target(db, org, rid="destination"):
    db.execute(
        """INSERT INTO robotics.robot_assets
        (organization_id,robot_id,display_name,serial_no,lifecycle_status,
         connectivity_state,etag,topology_revision)
        VALUES(%s,%s,%s,%s,'ACTIVE','OFFLINE','1','1')""",
        (org, rid, rid, rid),
    )
    return rid


def manager(auth):
    return replace(
        auth,
        organization_scoped_capabilities=auth.organization_scoped_capabilities
        | frozenset((o, p, "upload.manage") for o, p, _ in auth.organization_scope_triples),
    )


def test_transfer_creates_new_links_preserves_raw_upload_and_published_history(uploaded_raw):  # noqa: F811
    service, args, repo = setup(uploaded_raw)
    org, rid = args["organization_id"], args["robot_id"]
    args["auth"] = manager(args["auth"])
    resolution = PostgresRobotResolution(repo.connections)
    with repo.connections() as db:
        destination = target(db, org)
        processing_service.save(db, org, rid, "original-config", draft(), "test")
        publish(db, org, rid, "original-config")

    def snapshot():
        with repo.connections() as db:
            return {
                table: db.execute(
                    f"SELECT to_jsonb(r) FROM {table} r WHERE organization_id=%s "
                    + ("AND robot_id=%s" if table.endswith("versions") else ""),
                    (org, rid) if table.endswith("versions") else (org,),
                ).fetchall()
                for table in (
                    "ingest.raw_sources",
                    "ingest.robot_ingest_uploads",
                    "ingest.robot_processing_versions",
                )
            }

    before = snapshot()
    plan = resolution.plan(org, rid, args["auth"])
    assert plan.can_transfer and not plan.can_clean_config
    command = RobotResolutionCommand(
        action="TRANSFER", revision=plan.revision, target_robot_id=destination
    )
    key = uuid4().hex
    result = resolution.execute(org, rid, args["auth"], command, key)
    assert resolution.execute(org, rid, args["auth"], command, key) == result
    assert snapshot() == before
    assert service.deletion_check(**args).can_delete
    moved = service.deletion_check(**{**args, "robot_id": destination})
    assert {b.kind: b.count for b in moved.blockers} == {
        "raw": 1,
        "upload": 1,
        "configuration": 1,
    }
    with repo.connections() as db:
        links = db.execute(
            """SELECT robot_id,active FROM robotics.robot_reference_links
            WHERE organization_id=%s AND kind='raw' ORDER BY link_id""",
            (org,),
        ).fetchall()
        assert links == [(rid, False), (destination, True)]
    service.delete_robot(**args, request_id="remove-original-instance")
    assert snapshot() == before
    with repo.connections() as db:
        assert not db.execute(
            "SELECT 1 FROM robotics.robot_assets WHERE organization_id=%s AND robot_id=%s",
            (org, rid),
        ).fetchone()
        assert db.execute(
            "SELECT 1 FROM robotics.robot_history_anchors WHERE organization_id=%s AND robot_id=%s",
            (org, rid),
        ).fetchone()
        assert not db.execute(
            "SELECT 1 FROM ingest.robot_ingest_credentials "
            "WHERE organization_id=%s AND state='ACTIVE'",
            (org,),
        ).fetchone()
    assert service.deletion_check(**{**args, "robot_id": destination}).total == 3


@pytest.mark.parametrize("fault", ["permissions", "same_robot", "foreign_target", "stale"])
def test_transfer_rejects_invalid_requests_without_new_links(uploaded_raw, fault):  # noqa: F811
    service, args, repo = setup(uploaded_raw)
    org, rid = args["organization_id"], args["robot_id"]
    auth = manager(args["auth"])
    resolution = PostgresRobotResolution(repo.connections)
    with repo.connections() as db:
        destination = target(db, org)
    plan = resolution.plan(org, rid, auth)
    if fault == "permissions":
        auth = args["auth"]
        plan = resolution.plan(org, rid, auth)
    command = RobotResolutionCommand(
        action="TRANSFER",
        revision="old" if fault == "stale" else plan.revision,
        target_robot_id=rid
        if fault == "same_robot"
        else "other-org"
        if fault == "foreign_target"
        else destination,
    )
    with pytest.raises(ProblemException):
        resolution.execute(org, rid, auth, command, uuid4().hex)
    with repo.connections() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM robotics.robot_reference_links WHERE organization_id=%s",
                (org,),
            ).fetchone()[0]
            == 0
        )
    assert not service.deletion_check(**args).can_delete


def test_config_cleanup_unlinks_but_never_changes_published_versions(robot):  # noqa: F811
    import psycopg

    dsn, org, rid, repo = robot
    auth = actor(org, "p", "region")
    resolution = PostgresRobotResolution(lambda: psycopg.connect(dsn))
    with psycopg.connect(dsn) as db:
        publish(db, org, rid, "scheme")
        before = db.execute(
            "SELECT document FROM ingest.robot_processing_versions WHERE organization_id=%s", (org,)
        ).fetchall()
    plan = resolution.plan(org, rid, auth)
    assert plan.can_clean_config
    resolution.execute(
        org,
        rid,
        auth,
        RobotResolutionCommand(action="CLEAN_CONFIG", revision=plan.revision),
        uuid4().hex,
    )
    assert not resolution.plan(org, rid, auth).can_clean_config
    from hc_data_platform.robot_assets.service import OrganizationRobotAssetService

    service = OrganizationRobotAssetService(repo)
    args = dict(auth=auth, organization_id=org, robot_id=rid)
    assert service.deletion_check(**args).can_delete
    service.delete_robot(**args, request_id="config-cleanup")
    with psycopg.connect(dsn) as db:
        assert (
            db.execute(
                "SELECT document FROM ingest.robot_processing_versions WHERE organization_id=%s",
                (org,),
            ).fetchall()
            == before
        )
        with pytest.raises(psycopg.Error, match="immutable"):
            db.execute(
                "DELETE FROM ingest.robot_processing_versions WHERE organization_id=%s", (org,)
            )


def test_action_endpoints_require_auth_scope_and_a_complete_selection(uploaded_raw):  # noqa: F811
    from fastapi.testclient import TestClient

    from hc_data_platform.robot_assets.router import configure_organization_robot_assets
    from hc_data_platform.robot_assets.service import OrganizationRobotAssetService
    from tests.robotics.test_robot_directory_api import _app

    service, args, repo = setup(uploaded_raw)
    org, rid = args["organization_id"], args["robot_id"]
    service = OrganizationRobotAssetService(
        service._repository, resolution=PostgresRobotResolution(repo.connections)
    )
    configure_organization_robot_assets(service)
    current = {"value": manager(args["auth"])}
    client = TestClient(_app(current))
    path = f"/api/v1/organizations/{org}/robots/{rid}/deletion-actions"
    headers = {"Authorization": "Bearer test", "Idempotency-Key": "api-selection"}
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    command = {"action": "TRANSFER", "revision": response.json()["revision"]}
    assert client.post(path, headers=headers, json=command).status_code == 422
    command["target_robot_id"] = rid
    assert client.post(path, headers=headers, json=command).status_code == 422
    assert client.get(path.replace(org, "foreign-org"), headers=headers).status_code == 403
    current["value"] = replace(args["auth"], organization_scoped_capabilities=frozenset())
    assert client.post(path, headers=headers, json=command).status_code == 403
    current["value"] = None
    assert client.get(path).status_code == 401


def test_transferred_mcap_can_use_new_association_without_rewriting_its_origin(
    uploaded_raw,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    from hc_data_platform.ingest.raw_sources import PostgresRawSourceRepository
    from hc_data_platform.lerobot_imports import mcap_processing
    from hc_data_platform.lerobot_imports.processing import StartStoredProcessing
    from hc_data_platform.lerobot_imports.source_browser import OriginalSourceBrowser
    from hc_data_platform.robot_processing.models import ConfigReference
    from tests.continuous_recordings.test_mcap_native import synthetic_mcap
    from tests.episode_store.test_native_mcap_pipeline_services import upload_robot_mcap

    assets, args, repo = setup(uploaded_raw)
    _, storage, old, _, command, _ = uploaded_raw
    org, rid = args["organization_id"], args["robot_id"]
    auth = manager(args["auth"])
    scope = (org, old.target.project_id, old.target.region_code)
    content = synthetic_mcap(tmp_path / "move.mcap", joint_only=True).read_bytes()
    uploaded = upload_robot_mcap(
        repo, storage, old, command, content, configure=False, queued=False
    )
    browser = OriginalSourceBrowser(storage, PostgresRawSourceRepository(repo.connections))
    raw = browser.source(*scope, uploaded.raw_source_id)
    manifest = browser.manifest(raw)
    with repo.connections() as db:
        target(db, org)
        processing_service.save(db, org, rid, "scheme", draft(), "test")
        publish(db, org, rid, "scheme")
        mcap_processing.record_rejection(
            db, scope, raw, manifest, command, {"error": "SYNTHETIC_ORIGINAL_ATTEMPT"}
        )
    resolution = PostgresRobotResolution(repo.connections)
    plan = resolution.plan(org, rid, auth)
    resolution.execute(
        org,
        rid,
        auth,
        RobotResolutionCommand(
            action="TRANSFER",
            revision=plan.revision,
            target_robot_id="destination",
        ),
        uuid4().hex,
    )
    assets.delete_robot(**{**args, "auth": auth}, request_id="mcap-origin-removed")
    monkeypatch.setattr(mcap_processing, "connections", lambda: repo.connections)
    # Fixture dataset registration is intentionally minimal; focus on the binding
    # and immutable source guard, using real SQL and configuration selection.
    monkeypatch.setattr(mcap_processing, "validate_target", lambda *args: None)
    before = mcap_processing.progress(scope, raw.raw_source_id, manifest)
    assert before["files"][0]["robot_id"] == "destination"
    assert before["history"][0]["robot_id"] == rid
    with repo.connections() as db:
        config = processing_service.read(db, org, "destination")["versions"][0]
    result = mcap_processing.start(
        scope,
        raw,
        manifest,
        StartStoredProcessing(
            collection_task_id=command.collection_task_id,
            robot_id="destination",
            processing_config=ConfigReference(
                scheme_id=config["scheme_id"],
                version=config["version"],
                content_sha256=config["content_sha256"],
            ),
        ),
    )
    assert result["files"][0]["robot_id"] == "destination"
    with repo.connections() as db:
        assert (
            db.execute(
                """SELECT robot_id FROM ingest.raw_sources WHERE organization_id=%s
            AND raw_source_id=%s""",
                (org, raw.raw_source_id),
            ).fetchone()[0]
            == rid
        )


def test_new_configuration_links_do_not_collide_between_robots(robot):  # noqa: F811
    import psycopg

    dsn, org, rid, _ = robot
    auth = actor(org, "p", "region")
    resolution = PostgresRobotResolution(lambda: psycopg.connect(dsn))
    with psycopg.connect(dsn) as db:
        target(db, org)
        publish(db, org, rid, "scheme")
        processing_service.save(db, org, "destination", "scheme", draft(), "test")
        publish(db, org, "destination", "scheme")
    plan = resolution.plan(org, rid, auth)
    resolution.execute(
        org,
        rid,
        auth,
        RobotResolutionCommand(
            action="TRANSFER", revision=plan.revision, target_robot_id="destination"
        ),
        uuid4().hex,
    )
    with psycopg.connect(dsn) as db:
        originals = db.execute(
            """SELECT robot_id FROM ingest.robot_processing_versions
            WHERE organization_id=%s AND scheme_id='scheme' ORDER BY robot_id""",
            (org,),
        ).fetchall()
        assert originals == [("destination",), (rid,)]
    assert resolution.plan(org, "destination", auth).config_count == 2


def test_data_cleanup_resumes_after_storage_failure_and_allows_instance_deletion(services, package):
    from hc_data_platform.collection_tasks.deletion import PostgresTaskDeletion, TaskObjectPurger
    from hc_data_platform.collection_tasks.models import CreateCollectionTask
    from hc_data_platform.collection_tasks.postgres import PostgresCollectionTaskRepository
    from hc_data_platform.collection_tasks.service import CollectionTaskService
    from hc_data_platform.registry.binding_queries import published_model_for_binding
    from hc_data_platform.robot_assets.repository import PostgresOrganizationRobotAssetRepository
    from hc_data_platform.robot_assets.service import OrganizationRobotAssetService
    from tests.episode_store.test_commit_services import EpisodePackageCommitter, scoped

    repo, storage = services
    root, source = package
    org, project, region = source.identity.scope
    with scoped(source):
        with repo.connections() as db:
            db.execute(
                "INSERT INTO registry.organization_projects VALUES(%s,%s,'cleanup')", (org, project)
            )
            rid = target(db, org, "cleanup-source")
        tasks = CollectionTaskService(PostgresCollectionTaskRepository(repo.connections))
        task = tasks.create(
            organization_id=org,
            project_id=project,
            command=CreateCollectionTask(name="Disposable task", type="capture", scenario="test"),
            idempotency_key=uuid4().hex,
        ).record
        source = source.model_copy(
            update={
                "identity": source.identity.model_copy(update={"dataset_id": task.dataset_id}),
                "task_id": task.collection_task_id,
            }
        )
        (root / "hc/source.json").write_text(
            json.dumps(
                {
                    "identity": source.identity.model_dump(),
                    "source_sha256": source.source_sha256,
                }
            )
        )
        receipt = EpisodePackageCommitter(repo, storage).commit(root, source)
        with repo.connections() as db:
            db.execute(
                "UPDATE collection_tasks.collection_tasks SET upload_region_code=%s "
                "WHERE collection_task_id=%s",
                (region, task.collection_task_id),
            )
            db.execute(
                """INSERT INTO ingest.raw_sources
                (organization_id,project_id,region_code,raw_source_id,upload_id,dataset_id,
                collection_task_id,robot_id,source_format,source_format_version,manifest_key,
                storage_prefix,content_hash,file_count,total_bytes,raw_status,processing_status,
                created_at,committed_at,updated_at)
                VALUES(%s,%s,%s,'raw-cleanup','upload-cleanup',%s,%s,%s,'MCAP','v1',
                %s,%s,%s,1,1,'COMMITTED','READY',now(),now(),now())""",
                (
                    org,
                    project,
                    region,
                    task.dataset_id,
                    task.collection_task_id,
                    rid,
                    f"raw/{org}/fixture/manifest.json",
                    f"raw/{org}/fixture/",
                    "a" * 64,
                ),
            )

        class FlakyObjects(TaskObjectPurger):
            broken = True
            scan_broken = True

            def list(self, prefix):
                if self.scan_broken:
                    raise OSError("synthetic preflight storage outage")
                yield from super().list(prefix)

            def purge(self, manifest):
                if self.broken:
                    raise OSError("synthetic object-store outage")
                super().purge(manifest)

        objects = FlakyObjects(storage._client, storage._bucket)
        resolution = PostgresRobotResolution(
            repo.connections,
            PostgresTaskDeletion(repo.connections, objects),
        )
        assets = OrganizationRobotAssetService(
            PostgresOrganizationRobotAssetRepository(
                repo.connections,
                published_models=published_model_for_binding,
            )
        )
        auth = manager(actor(org, project, region))
        plan = resolution.plan(org, rid, auth)
        assert len(plan.tasks) == 1 and plan.tasks[0].can_delete
        key = uuid4().hex
        command = RobotResolutionCommand(
            action="DELETE_TASK",
            revision=plan.revision,
            project_id=project,
            task_id=task.collection_task_id,
        )
        with pytest.raises(OSError, match="preflight storage outage"):
            resolution.execute(org, rid, auth, command, key)
        assert resolution.plan(org, rid, auth).tasks[0].retry_key is None
        objects.scan_broken = False
        # The new endpoint is organization-scoped, unlike the task page endpoint.
        # It must select the exact task scope before invoking the purge adapter.
        from hc_data_platform.core.context import select_organization_scope

        select_organization_scope(org)
        with pytest.raises(ProblemException, match="TASK_DELETE_CLEANUP_PENDING"):
            resolution.execute(org, rid, auth, command, key)
        retry = resolution.plan(org, rid, auth)
        assert retry.tasks[0].retry_key == key
        assert not assets.deletion_check(auth=auth, organization_id=org, robot_id=rid).can_delete
        assert storage.head(receipt.data.package_prefix + "/meta/info.json")
        objects.broken = False
        result = resolution.execute(org, rid, auth, command, key)
        assert resolution.execute(org, rid, auth, command, key) == result
        assert storage.head(receipt.data.package_prefix + "/meta/info.json") is None
        assert not resolution.plan(org, rid, auth).tasks
        assets.delete_robot(auth=auth, organization_id=org, robot_id=rid, request_id="done")

"""Real model upload/publication/binding helpers for the production ingestion test."""

import hashlib
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from hc_data_platform.registry.models import (
    CompleteRobotModelAssetFileRequest,
    CreateRobotModelAssetUploadRequest,
    CreateRobotModelRequest,
    PublishRobotModelVersionRequest,
    ReplaceRobotModelJointMappingsRequest,
)
from hc_data_platform.robot_assets.models import BindOrganizationRobotModelRequest
from hc_data_platform.robotics.models import CreateRobotRequest


def scene_setup(runtime, auth, org, project, connections):
    model_project = project + "-models"
    with connections() as db:
        db.execute(
            "INSERT INTO registry.organization_projects VALUES (%s,%s,%s)",
            (org, model_project, "Model owner project"),
        )
    manager = replace(
        auth,
        project_ids=auth.project_ids | frozenset({model_project}),
        scope_pairs=auth.scope_pairs | frozenset({(model_project, None)}),
        organization_scope_triples=auth.organization_scope_triples
        | frozenset({(org, model_project, None)}),
        organization_scoped_capabilities=auth.organization_scoped_capabilities
        | frozenset(
            (org, project, value)
            for value in ("robot.manage", "robot.read", "robot_model.manage", "robot_model.read")
        ),
        capabilities=auth.capabilities
        | frozenset(
            {
                "robot.read",
                "robot.create",
                "robot.manage",
                "robot_model.read",
                "robot_model.manage",
                "dataset_version.read",
                "episode.read",
            }
        ),
    )
    registry = runtime.registry
    scope = dict(
        auth=manager, organization_id=org, project_id=model_project, request_id=uuid4().hex
    )

    def model(joint):
        value = registry.create_robot_model(
            **scope,
            idempotency_key=uuid4().hex,
            command=CreateRobotModelRequest(
                manufacturer="synthetic",
                model_code=uuid4().hex,
                display_name="Fixed scene " + joint,
                version_label="1",
            ),
        ).data
        body = (
            f'<robot name="test"><link name="base"/><link name="tip"/>'
            f'<joint name="{joint}" type="revolute"><parent link="base"/><child link="tip"/>'
            '<limit lower="-1" upper="1" effort="1" velocity="1"/></joint></robot>'
        ).encode()
        upload = registry.create_asset_upload(
            **scope,
            version_id=value.id,
            idempotency_key=uuid4().hex,
            command=CreateRobotModelAssetUploadRequest(
                files=[
                    {
                        "relative_path": "model.urdf",
                        "role": "URDF",
                        "media_type": "application/xml",
                        "size_bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                ]
            ),
        ).data
        url = upload.files[0].part_authorizations[0].url
        token = parse_qs(urlsplit(url).query)["token"][0]
        part = registry.receive_authorized_asset_part(organization_id=org, token=token, body=body)
        registry.complete_asset_upload_file(
            **scope,
            upload_id=upload.upload_id,
            command=CompleteRobotModelAssetFileRequest(
                relative_path="model.urdf", parts=[{"part_number": 1, "etag": part.etag}]
            ),
        )
        version_scope = {**scope, "version_id": value.id}
        value = registry.get_robot_model_version(**version_scope).data
        value = registry.replace_robot_model_joint_mappings(
            **version_scope,
            expected_etag=value.etag,
            idempotency_key=uuid4().hex,
            command=ReplaceRobotModelJointMappingsRequest(
                mappings=[
                    {"source_joint_name": "x", "target_joint_name": joint, "direction": "SAME"}
                ]
            ),
        ).data
        publish_key = uuid4().hex
        proof = registry.preflight_robot_model_publish(
            **version_scope, expected_etag=value.etag, idempotency_key=publish_key
        ).data
        assert proof.allowed, proof
        registry.publish_robot_model_version(
            **version_scope,
            expected_etag=value.etag,
            idempotency_key=publish_key,
            command=PublishRobotModelVersionRequest(preflight_token=proof.preflight_token),
        )
        return value.id, body

    first, second = model("joint_one"), model("joint_two")
    robot = runtime.robot_assets.create_robot(
        auth=manager,
        organization_id=org,
        idempotency_key=uuid4().hex,
        request_id=uuid4().hex,
        command=CreateRobotRequest(display_name="Frozen scene fixture", serial_no=uuid4().hex),
    ).data

    def bind(version):
        current = runtime.robot_assets.get_robot(
            auth=manager, organization_id=org, robot_id=robot.robot.id, request_id=uuid4().hex
        ).data
        return runtime.robot_assets.bind_model(
            auth=manager,
            organization_id=org,
            robot_id=robot.robot.id,
            request_id=uuid4().hex,
            idempotency_key=uuid4().hex,
            command=BindOrganizationRobotModelRequest(version_id=version, robot_etag=current.etag),
        )

    bind(first[0])
    plans = []
    freeze = runtime.activities.robot_scenes.freeze_package

    def record(plan):
        plans.append(plan)
        return freeze(plan)

    runtime.activities.robot_scenes.freeze_package = record
    return robot.robot.id, first, second, bind, plans


def assert_frozen_scene(runtime, package, fixture, auth, connections):
    import httpx
    import psycopg
    import pytest

    from tests.episode_store.test_http_services import register_and_grant

    robot_id, first, second, bind, plans = fixture
    data = package.receipt.data
    ident = data.identity
    frozen = package.document("hc/robot_scene.json")
    assert frozen["status"] == "FROZEN" and frozen["model_version_id"] == first[0]
    with connections() as db:
        expected_model_id = db.execute(
            "SELECT robot_model_id FROM registry.robot_model_versions "
            "WHERE organization_id=%s AND version_id=%s",
            (ident.organization_id, first[0]),
        ).fetchone()[0]
    assert frozen["model_id"] == str(expected_model_id)
    assert frozen["robot_id"] == robot_id
    assert frozen["joint_mappings"][0]["target_joint_name"] == "joint_one"
    assert frozen["calibration"] == package.document("hc/calibration.json")
    collection = runtime.episode_packages.collections.publication(data)
    version_id = "version_" + collection.collection_version_id
    manager = replace(
        auth, capabilities=auth.capabilities | frozenset({"dataset_version.read", "episode.read"})
    )
    with httpx.Client(base_url="http://gateway:8080", trust_env=False, timeout=60) as client:
        principal, headers = register_and_grant(
            client,
            runtime.episode_packages.repository,
            ident,
            capabilities=["dataset_version.read", "episode.read", "robot_model.read"],
        )
        bootstrap = client.get(
            f"/api/v1/projects/{ident.project_id}/datasets/{ident.dataset_id}/versions/{version_id}/bootstrap",
            headers=headers,
        )
        assert bootstrap.status_code == 200, bootstrap.text
        snapshot_token = bootstrap.json()["data"]["snapshot_token"]
        revision = runtime.dataset_page.rollout_revision(
            auth=manager,
            organization_id=ident.organization_id,
            project_id=ident.project_id,
            region_code=ident.region_code,
            dataset_id=ident.dataset_id,
            version_id=version_id,
            rollout_id=ident.rollout_id,
            snapshot_token=snapshot_token,
            request_id=uuid4().hex,
        ).data
        root = (
            f"/api/v1/projects/{ident.project_id}/regions/{ident.region_code}"
            f"/datasets/{ident.dataset_id}/versions/{version_id}"
            f"/episode-revisions/{revision.revision_id}/logical"
        )
        snapshot = client.get(root, headers=headers, params={"snapshot_token": snapshot_token})
        assert snapshot.status_code == 200, snapshot.text
        params = dict(snapshot_token=snapshot_token, snapshot_id=snapshot.json()["snapshot_id"])
        before = client.get(root + "/robot-scene", headers=headers, params=params)
        assert before.status_code == 200, before.text
        assert before.json()["scene"] == frozen
        bind(second[0])
        after = client.get(root + "/robot-scene", headers=headers, params=params)
        assert after.status_code == 200 and after.json() == before.json()
        assert package.document("hc/robot_scene.json") == frozen
        retry = runtime.activities.robot_scenes.freeze_package(plans[0])
        assert retry.input == package.manifest.input
        assert retry.metadata["robot_scene"] == frozen
        from concurrent.futures import ThreadPoolExecutor

        from tests.episode_store.test_commit_services import scoped

        def retry_after_rebinding(_):
            with scoped(plans[0].input):
                return runtime.activities.robot_scenes.freeze_package(plans[0])

        with ThreadPoolExecutor(max_workers=4) as pool:
            concurrent = tuple(pool.map(retry_after_rebinding, range(4)))
        assert all(
            item.input == retry.input and item.metadata == retry.metadata for item in concurrent
        )
        changed = plans[0].model_copy(
            update={"metadata": {**plans[0].metadata, "calibration": {"status": "changed"}}}
        )
        with pytest.raises(ValueError, match="calibration differs"):
            runtime.activities.robot_scenes.freeze_package(changed)
        asset = frozen["assets"][0]
        endpoint = root + f"/robot-scene/assets/{asset['asset_id']}/authorize"
        granted = client.post(endpoint, headers=headers, params=params)
        assert granted.status_code == 200, granted.text
        assert granted.json()["scene_sha256"] == before.json()["scene_sha256"]
        downloaded = client.get(granted.json()["authorization"]["download_url"])
        assert downloaded.status_code == 200 and downloaded.content == first[1]
        assert hashlib.sha256(downloaded.content).hexdigest() == asset["sha256"]
        assert client.get(root + "/robot-scene", params=params).status_code == 401
        assert (
            client.get(
                root + "/robot-scene",
                params=params,
                headers={**headers, "X-Organization-Id": "foreign"},
            ).status_code
            == 403
        )
        assert (
            client.get(
                root + "/robot-scene",
                headers=headers,
                params={**params, "snapshot_id": "snapshot_" + "0" * 64},
            ).status_code
            == 409
        )
        assert (
            client.post(
                root + "/robot-scene/assets/not-frozen/authorize", headers=headers, params=params
            ).status_code
            == 404
        )
        with connections() as db:
            db.execute(
                "UPDATE access_control.capability_grants SET active=false "
                "WHERE principal_id=%s AND capability_key='robot_model.read'",
                (principal,),
            )
        assert client.post(endpoint, headers=headers, params=params).status_code == 403
        with connections() as db:
            db.execute(
                "UPDATE access_control.capability_grants SET active=false WHERE principal_id=%s",
                (principal,),
            )
        assert client.get(root + "/robot-scene", headers=headers, params=params).status_code == 403
    with pytest.raises(psycopg.Error), connections() as db:
        db.execute(
            "UPDATE robotics.data_scene_snapshots SET document='{}' WHERE organization_id=%s",
            (ident.organization_id,),
        )
